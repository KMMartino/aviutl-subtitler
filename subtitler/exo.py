"""AviUtl EXO generation."""

from __future__ import annotations

import ctypes
import math
import os
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import ExoWriteError
from .models import (
    BrollPlacement,
    ExoCompositeMediaClip,
    ExoMarker,
    ExoMediaPlan,
    ExoMediaSegment,
    ExoSettings,
    Subtitle,
)


def encode_text_for_exo(text: str) -> str:
    max_bytes = 2048
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    encoded = normalized.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > max_bytes:
        encoded = encoded[: max_bytes - 2] + b"\x00\x00"
    if len(encoded) < max_bytes:
        encoded += b"\x00" * (max_bytes - len(encoded))
    return encoded.hex()


def time_to_frame(time_seconds: float, fps: int) -> int:
    return int(time_seconds * fps) + 1


_CHAPTER_EASING = "15@イージング（通常）@イージング,23"
_CHAPTER_BACKGROUND_TRANSITION_SECONDS = 0.8
_CHAPTER_TEXT_TRANSITION_MULTIPLIER = 3
_CHAPTER_GRADIENT_SHOULDER = 50
_CHAPTER_WIDTH_GRID = 20
_SUBTITLE_BACKGROUND_EXTRA_WIDTH = 55
_SUBTITLE_BACKGROUND_TOP_OFFSET = 87.5


@dataclass(frozen=True)
class _ChapterLayout:
    start: int
    end: int
    text: str
    width: int
    x: float
    background_y: float
    text_x: float
    text_y: float


@lru_cache(maxsize=256)
def _measure_windows_text_width(text: str, font_name: str, font_size: int) -> int | None:
    if os.name != "nt" or not text:
        return None

    from ctypes import wintypes

    class Size(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.GetTextFaceW.argtypes = [wintypes.HDC, ctypes.c_int, wintypes.LPWSTR]
    gdi32.GetTextFaceW.restype = ctypes.c_int
    gdi32.GetTextExtentPoint32W.argtypes = [
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(Size),
    ]
    gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    device_context = gdi32.CreateCompatibleDC(None)
    if not device_context:
        return None
    # AviUtl's font size is in points. At the standard 96 DPI used by EXEdit,
    # a 60-point marker font maps to an 80-pixel GDI cell height.
    cell_height = max(1, round(font_size * 96 / 72))
    font = gdi32.CreateFontW(
        cell_height,
        0,
        0,
        0,
        400,
        0,
        0,
        0,
        128,
        0,
        0,
        0,
        0,
        font_name,
    )
    if not font:
        gdi32.DeleteDC(device_context)
        return None
    previous = gdi32.SelectObject(device_context, font)
    try:
        face_length = gdi32.GetTextFaceW(device_context, 0, None)
        if face_length <= 0:
            return None
        face_buffer = ctypes.create_unicode_buffer(face_length)
        if not gdi32.GetTextFaceW(device_context, face_length, face_buffer):
            return None
        if face_buffer.value.casefold() != font_name.casefold():
            return None
        size = Size()
        utf16_units = len(text.encode("utf-16-le")) // 2
        if not gdi32.GetTextExtentPoint32W(device_context, text, utf16_units, ctypes.byref(size)):
            return None
        return max(0, int(size.cx))
    finally:
        if previous:
            gdi32.SelectObject(device_context, previous)
        gdi32.DeleteObject(font)
        gdi32.DeleteDC(device_context)


def _estimated_text_width(text: str, font_size: int) -> float:
    width = 0.0
    for character in text:
        if unicodedata.combining(character):
            continue
        if unicodedata.east_asian_width(character) in {"W", "F", "A"}:
            width += font_size * 0.955
        else:
            width += font_size * 0.55
    return width


def _chapter_background_width(text: str, settings: ExoSettings) -> int:
    measured = _measure_windows_text_width(text, settings.font, settings.font_size)
    text_width = float(measured) if measured is not None else _estimated_text_width(text, settings.font_size)
    padded_width = max(settings.font_size * 2.0, text_width + settings.font_size)
    return max(
        _CHAPTER_WIDTH_GRID,
        int(math.floor(padded_width / _CHAPTER_WIDTH_GRID + 0.5)) * _CHAPTER_WIDTH_GRID,
    )


def _chapter_layouts(
    frame_ranges: list[tuple[int, int, str]],
    settings: ExoSettings,
) -> list[_ChapterLayout]:
    marker_height = max(1, round(settings.font_size * 22 / 15))
    background_y = -settings.height / 2 + 20 + marker_height / 2
    text_y = background_y + settings.font_size * 2 / 3
    layouts: list[_ChapterLayout] = []
    for start, end, text in frame_ranges:
        width = _chapter_background_width(text, settings)
        x = -settings.width / 2 + 20 + width / 2
        layouts.append(
            _ChapterLayout(
                start=start,
                end=end,
                text=text,
                width=width,
                x=x,
                background_y=background_y,
                text_x=x - 2,
                text_y=text_y,
            )
        )
    return layouts


def generate_exo_object(
    index: int,
    start_frame: int,
    end_frame: int,
    text: str,
    settings: ExoSettings,
    layer: int = 1,
    font_size: int | None = None,
    text_color: str | None = None,
    y_position: float | None = None,
    include_animation: bool = False,
) -> str:
    encoded_text = encode_text_for_exo(text)
    size = font_size if font_size is not None else settings.font_size
    color = text_color if text_color is not None else settings.text_color
    y = y_position if y_position is not None else settings.y_position
    animation = ""
    standard_index = 6
    if include_animation:
        animation = f"""[{index}.6]
_name=アニメーション効果
track0=0.20
track1=105.00
track2=0.00
track3=0.00
check0=100
type=6
filter=0
name=
param=
[{index}.7]
_name=アニメーション効果
track0=-0.20
track1=105.00
track2=0.00
track3=0.00
check0=100
type=6
filter=0
name=
param=
"""
        standard_index = 8
    return f"""[{index}]
start={start_frame}
end={end_frame}
layer={layer}
overlay=1
camera=0
[{index}.0]
_name=テキスト
サイズ={size}
表示速度=0.0
文字毎に個別オブジェクト=0
移動座標上に表示する=0
自動スクロール=0
B=0
I=0
type=0
autoadjust=0
soft=1
monospace=0
align=7
spacing_x=0
spacing_y=0
precision=1
color={color}
color2=00ffff
font={settings.font}
text={encoded_text}
[{index}.1]
_name=グラデーション
_disable=1
強さ=100.0
中心X=0
中心Y=0
角度=0.0
幅=65
blend=0
color=ffda44
no_color=0
color2=d28e00
no_color2=0
type=3
[{index}.2]
_name=縁取り
サイズ=1
ぼかし=100
color=000000
file=
[{index}.3]
_name=縁取り
サイズ=2
ぼかし=0
color=000000
file=
[{index}.4]
_name=シャドー
X=4
Y=2
濃さ=100.0
拡散=0
影を別オブジェクトで描画=0
color=000000
file=
[{index}.5]
_name=縁取り
_disable=1
サイズ=10
ぼかし=50
color=ffffff
file=
{animation}[{index}.{standard_index}]
_name=標準描画
X=0.0
Y={y}
Z=0.0
拡大率=100.00
透明度=0.0
回転=0.00
blend=0"""


def _chapter_custom_object(
    index: int,
    start: int,
    end: int,
    layer: int,
    start_layout: _ChapterLayout,
    end_layout: _ChapterLayout,
    *,
    filtered: bool,
    gradient_start: int,
    gradient_end: int,
    gradient_reversed: bool,
    chained: bool,
    marker_height: int,
    corner_radius: int,
) -> str:
    chain = "\nchain=1" if chained else ""
    moving = start_layout.width != end_layout.width or start_layout.x != end_layout.x
    if moving:
        width_track = f"{start_layout.width:.2f},{end_layout.width:.2f},{_CHAPTER_EASING}"
        x_track = f"{start_layout.x:.1f},{end_layout.x:.1f},{_CHAPTER_EASING}"
        y_track = (
            f"{start_layout.background_y:.1f},{end_layout.background_y:.1f},"
            f"{_CHAPTER_EASING}"
        )
        z_track = f"0.0,0.0,{_CHAPTER_EASING}"
    else:
        width_track = f"{end_layout.width:.2f}"
        x_track = f"{end_layout.x:.1f}"
        y_track = f"{end_layout.background_y:.1f}"
        z_track = "0.0"

    filters = ""
    if filtered:
        color = "000000" if gradient_reversed else "ffffff"
        color2 = "ffffff" if gradient_reversed else "000000"
        filters = f"""
[{index}.1]
_name=縁取り
サイズ=3
ぼかし=100
color=000000
file=
[{index}.2]
_name=縁取り
サイズ=3
ぼかし=0
color=000000
file=
[{index}.3]
_name=グラデーション
強さ=100.0
中心X={gradient_start},{gradient_end},1
中心Y=0
角度=60.0
幅={_CHAPTER_GRADIENT_SHOULDER * 2}
blend=0
color={color}
no_color=0
color2={color2}
no_color2=0
type=0
[{index}.4]
_name=シャドー
X=0
Y=0
濃さ=50.0
拡散=15
影を別オブジェクトで描画=0
color=000000
file="""
        standard_index = 5
    else:
        standard_index = 1

    return f"""[{index}]
start={start}
end={end}
layer={layer}
overlay=1
camera=0{chain}
[{index}.0]
_name=カスタムオブジェクト
track0={width_track}
track1={marker_height:.2f}
track2=1000.00
track3={corner_radius:.2f}
check0=0
type=0
filter=2
name=角丸四角形@hksy
param=color=0xffffff{filters}
[{index}.{standard_index}]
_name=標準描画
X={x_track}
Y={y_track}
Z={z_track}
拡大率=100.00
透明度=0.0
回転=0.00
blend=0"""


def _chapter_background_objects(
    layouts: list[_ChapterLayout],
    settings: ExoSettings,
    *,
    start_index: int,
    layer: int,
    filtered: bool,
) -> tuple[list[str], int]:
    objects: list[str] = []
    index = start_index
    transition_frames = max(1, round(settings.rate * _CHAPTER_BACKGROUND_TRANSITION_SECONDS))
    marker_height = max(1, round(settings.font_size * 22 / 15))
    corner_radius = max(1, round(settings.font_size / 2))

    for chapter_index, layout in enumerate(layouts):
        gradient_extent = layout.width // 2 + _CHAPTER_GRADIENT_SHOULDER
        gradient_reversed = chapter_index % 2 == 1
        previous = layouts[chapter_index - 1] if chapter_index else layout
        total_frames = layout.end - layout.start + 1
        resize_frames = min(transition_frames, total_frames) if chapter_index else 0
        if resize_frames:
            transition_end = layout.start + resize_frames - 1
            objects.append(
                _chapter_custom_object(
                    index,
                    layout.start,
                    transition_end,
                    layer,
                    previous,
                    layout,
                    filtered=filtered,
                    gradient_start=-gradient_extent,
                    gradient_end=-gradient_extent,
                    gradient_reversed=gradient_reversed,
                    chained=False,
                    marker_height=marker_height,
                    corner_radius=corner_radius,
                )
            )
            index += 1
            if transition_end == layout.end:
                continue
            segment_start = transition_end + 1
            chained = True
        else:
            segment_start = layout.start
            chained = False
        objects.append(
            _chapter_custom_object(
                index,
                segment_start,
                layout.end,
                layer,
                layout,
                layout,
                filtered=filtered,
                gradient_start=-gradient_extent,
                gradient_end=gradient_extent,
                gradient_reversed=gradient_reversed,
                chained=chained,
                marker_height=marker_height,
                corner_radius=corner_radius,
            )
        )
        index += 1
    return objects, index


def _chapter_text_object(
    index: int,
    start: int,
    end: int,
    layout: _ChapterLayout,
    settings: ExoSettings,
    layer: int,
    clip_start: int,
    clip_end: int,
    *,
    chained: bool,
) -> str:
    chain = "\nchain=1" if chained else ""
    encoded_text = encode_text_for_exo(layout.text)
    clip_track = f"{clip_start},{clip_end},{_CHAPTER_EASING}"
    return f"""[{index}]
start={start}
end={end}
layer={layer}
overlay=1
camera=0{chain}
[{index}.0]
_name=テキスト
サイズ={settings.font_size}
表示速度=0.0
文字毎に個別オブジェクト=0
移動座標上に表示する=0
自動スクロール=0
B=0
I=0
type=0
autoadjust=0
soft=1
monospace=0
align=7
spacing_x=0
spacing_y=0
precision=1
color={settings.text_color}
color2=00ffff
font={settings.font}
text={encoded_text}
[{index}.1]
_name=縁取り
サイズ=1
ぼかし=100
color=000000
file=
[{index}.2]
_name=縁取り
サイズ=2
ぼかし=0
color=000000
file=
[{index}.3]
_name=シャドー
X=4
Y=2
濃さ=100.0
拡散=0
影を別オブジェクトで描画=0
color=000000
file=
[{index}.4]
_name=斜めクリッピング
中心X={clip_track}
中心Y=0
角度=-90.0
ぼかし={_CHAPTER_GRADIENT_SHOULDER}
幅=0
[{index}.5]
_name=標準描画
X={layout.text_x:.1f}
Y={layout.text_y:.1f}
Z=0.0
拡大率=100.00
透明度=0.0
回転=0.00
blend=0"""


def _chapter_text_objects(
    layouts: list[_ChapterLayout],
    settings: ExoSettings,
    *,
    start_index: int,
    layer: int,
) -> tuple[list[str], int]:
    objects: list[str] = []
    index = start_index
    tail_frames = max(1, round(settings.rate * _CHAPTER_BACKGROUND_TRANSITION_SECONDS))
    head_frames = tail_frames * _CHAPTER_TEXT_TRANSITION_MULTIPLIER

    for chapter_index, layout in enumerate(layouts):
        total_frames = layout.end - layout.start + 1
        desired_tail = tail_frames if chapter_index < len(layouts) - 1 else 0
        tail_length = min(desired_tail, total_frames)
        available_before_tail = total_frames - tail_length
        desired_head = head_frames if chapter_index else 0
        head_length = min(desired_head, available_before_tail)
        middle_length = total_frames - head_length - tail_length
        clip_extent = layout.width // 2
        cursor = layout.start
        chained = False

        if head_length:
            segment_end = cursor + head_length - 1
            objects.append(
                _chapter_text_object(
                    index,
                    cursor,
                    segment_end,
                    layout,
                    settings,
                    layer,
                    -clip_extent,
                    clip_extent,
                    chained=chained,
                )
            )
            index += 1
            cursor = segment_end + 1
            chained = True
        if middle_length:
            segment_end = cursor + middle_length - 1
            objects.append(
                _chapter_text_object(
                    index,
                    cursor,
                    segment_end,
                    layout,
                    settings,
                    layer,
                    clip_extent,
                    clip_extent,
                    chained=chained,
                )
            )
            index += 1
            cursor = segment_end + 1
            chained = True
        if tail_length:
            objects.append(
                _chapter_text_object(
                    index,
                    cursor,
                    layout.end,
                    layout,
                    settings,
                    layer,
                    clip_extent,
                    -clip_extent,
                    chained=chained,
                )
            )
            index += 1
    return objects, index


def generate_chapter_exo_objects(
    index: int,
    frame_ranges: list[tuple[int, int, str]],
    settings: ExoSettings,
    *,
    base_layer: int,
) -> tuple[list[str], int]:
    layouts = _chapter_layouts(frame_ranges, settings)
    objects: list[str] = []
    filtered, index = _chapter_background_objects(
        layouts,
        settings,
        start_index=index,
        layer=base_layer,
        filtered=True,
    )
    objects.extend(filtered)
    clean, index = _chapter_background_objects(
        layouts,
        settings,
        start_index=index,
        layer=base_layer + 1,
        filtered=False,
    )
    objects.extend(clean)
    text, index = _chapter_text_objects(
        layouts,
        settings,
        start_index=index,
        layer=base_layer + 2,
    )
    objects.extend(text)
    return objects, index


def generate_exo_video_object(
    index: int,
    segment: ExoMediaSegment,
    source_path: str,
    *,
    layer: int = 1,
    scale_percent: float = 100.0,
) -> str:
    return f"""[{index}]
start={segment.output_start_frame}
end={segment.output_end_frame}
layer={layer}
group={segment.group_id}
overlay=1
camera=0
[{index}.0]
_name=動画ファイル
再生位置={segment.source_start_frame}
再生速度=100.0
ループ再生=0
アルファチャンネルを読み込む=0
file={source_path}
[{index}.1]
_name=標準描画
X=0.0
Y=0.0
Z=0.0
拡大率={scale_percent:.2f}
透明度=0.0
回転=0.00
blend=0"""


def generate_exo_audio_object(
    index: int,
    segment: ExoMediaSegment,
    source_path: str,
    *,
    layer: int = 2,
    volume: float = 100.0,
) -> str:
    return f"""[{index}]
start={segment.output_start_frame}
end={segment.output_end_frame}
layer={layer}
group={segment.group_id}
overlay=1
audio=1
[{index}.0]
_name=音声ファイル
再生位置=0.00
再生速度=100.0
ループ再生=0
動画ファイルと連携=1
file={source_path}
[{index}.1]
_name=標準再生
音量={volume:.1f}
左右=0.0"""


def generate_exo_image_object(
    index: int,
    placement: BrollPlacement,
    source_path: str,
    *,
    layer: int = 1,
) -> str:
    return f"""[{index}]
start={placement.output_start_frame}
end={placement.output_end_frame}
layer={layer}
group={10000 + index}
overlay=1
camera=0
[{index}.0]
_name=画像ファイル
file={source_path}
[{index}.1]
_name=標準描画
X=0.0
Y=0.0
Z=0.0
拡大率={placement.scale_percent:.2f}
透明度=0.0
回転=0.00
blend=0"""


def generate_subtitle_background_object(
    index: int,
    start_frame: int,
    end_frame: int,
    settings: ExoSettings,
    *,
    layer: int,
) -> str:
    size = settings.width + _SUBTITLE_BACKGROUND_EXTRA_WIDTH
    y = settings.y_position + size / 2 - _SUBTITLE_BACKGROUND_TOP_OFFSET
    return f"""[{index}]
start={start_frame}
end={end_frame}
layer={layer}
overlay=1
camera=0
[{index}.0]
_name=図形
サイズ={size}
縦横比=0.0
ライン幅=4000
type=2
color=000000
name=
[{index}.1]
_name=境界ぼかし
範囲=40
縦横比=-100.0
透明度の境界をぼかす=0
[{index}.2]
_name=標準描画
X=0.0
Y={y:.1f}
Z=0.0
拡大率=100.00
透明度=50.0
回転=0.00
blend=0"""


def generate_exo_file(
    subtitles: list[Subtitle],
    settings: ExoSettings,
    total_duration: float,
    insert_initial_empty: bool = True,
    chapter_markers: list[ExoMarker] | None = None,
    mistranscription_markers: list[ExoMarker] | None = None,
    media_plan: ExoMediaPlan | None = None,
    composite_media_clips: list[ExoCompositeMediaClip] | None = None,
    broll_placements: list[BrollPlacement] | None = None,
    subtitle_background: bool = True,
    additional_marker_layers: list[list[ExoMarker]] | None = None,
) -> str:
    _validate_shift_jis_literal("exo.font", settings.font)
    if media_plan is not None and composite_media_clips:
        raise ExoWriteError("Use either a single media plan or composite media clips, not both")
    media_source = ""
    if media_plan is not None:
        media_source = str(media_plan.source_path.resolve())
        _validate_media_path(media_source)
    clips = composite_media_clips or []
    clip_paths: list[tuple[str, str]] = []
    for clip in clips:
        video_path = str(clip.video_path.resolve())
        audio_path = str(clip.audio_path.resolve())
        _validate_media_path(video_path)
        _validate_media_path(audio_path)
        clip_paths.append((video_path, audio_path))
    broll = sorted(
        broll_placements or [],
        key=lambda item: (item.output_start_frame, item.output_end_frame, item.id),
    )
    for placement in broll:
        _validate_media_path(str(placement.asset_path.resolve()))
    total_frames = time_to_frame(total_duration, settings.rate)
    header = f"""[exedit]
width={settings.width}
height={settings.height}
rate={settings.rate}
scale={settings.scale}
length={total_frames}
audio_rate={settings.audio_rate}
audio_ch={settings.audio_ch}"""
    frame_ranges: list[tuple[int, int, str]] = []
    for sub in sorted(subtitles, key=lambda s: (s.start_time, s.end_time)):
        start = time_to_frame(sub.start_time, settings.rate)
        end = time_to_frame(sub.end_time, settings.rate)
        if end <= start:
            end = start + 1
        frame_ranges.append((start, end, sub.text))

    for i in range(len(frame_ranges) - 1):
        start, end, text = frame_ranges[i]
        next_start = frame_ranges[i + 1][0]
        if end >= next_start:
            frame_ranges[i] = (start, max(start + 1, next_start - 1), text)

    if insert_initial_empty and frame_ranges and frame_ranges[0][0] > 1:
        frame_ranges.insert(0, (1, frame_ranges[0][0] - 1, ""))

    objects = []
    index = 0
    if media_plan is not None:
        for segment in media_plan.segments:
            objects.append(generate_exo_video_object(index, segment, media_source, layer=1))
            index += 1
        for segment in media_plan.segments:
            objects.append(generate_exo_audio_object(index, segment, media_source, layer=2))
            index += 1
    for clip, (video_path, _) in zip(clips, clip_paths):
        objects.append(generate_exo_video_object(index, clip.segment, video_path, layer=1))
        index += 1
    for clip, (_, audio_path) in zip(clips, clip_paths):
        objects.append(generate_exo_audio_object(index, clip.segment, audio_path, layer=2))
        index += 1
    has_primary_media = media_plan is not None or bool(clips)
    edit_video_layer = 3 if has_primary_media else 1
    edit_audio_layer = edit_video_layer + 1
    for placement in broll:
        source_path = str(placement.asset_path.resolve())
        if placement.media_kind == "image":
            objects.append(
                generate_exo_image_object(
                    index,
                    placement,
                    source_path,
                    layer=edit_video_layer,
                )
            )
            index += 1
            continue
        segment = ExoMediaSegment(
            output_start_frame=placement.output_start_frame,
            output_end_frame=placement.output_end_frame,
            source_start_frame=placement.source_start_frame,
            group_id=10000 + index,
        )
        objects.append(
            generate_exo_video_object(
                index,
                segment,
                source_path,
                layer=edit_video_layer,
                scale_percent=placement.scale_percent,
            )
        )
        index += 1
        if placement.has_audio:
            # Keep linked asset audio immediately below its video while
            # guaranteeing that it never replaces narration.
            objects.append(
                generate_exo_audio_object(
                    index,
                    segment,
                    source_path,
                    layer=edit_audio_layer,
                    volume=0.0,
                )
            )
            index += 1
    reserved_media_layers = (2 if has_primary_media else 0) + (2 if broll else 0)
    background_layer = reserved_media_layers + 1
    subtitle_layer = background_layer + 1 if subtitle_background else background_layer
    qa_layer = subtitle_layer + 1
    extra_marker_layers = additional_marker_layers or []
    chapter_layer = qa_layer + max(1, len(extra_marker_layers))
    if frame_ranges and subtitle_background:
        objects.append(
            generate_subtitle_background_object(
                index,
                1,
                total_frames,
                settings,
                layer=background_layer,
            )
        )
        index += 1
    for start, end, text in frame_ranges:
        objects.append(generate_exo_object(index, start, end, text, settings, layer=subtitle_layer, include_animation=True))
        index += 1
    chapter_objects, index = generate_chapter_exo_objects(
        index,
        _chapter_marker_frame_ranges(chapter_markers or [], settings.rate, total_frames),
        settings,
        base_layer=chapter_layer,
    )
    objects.extend(chapter_objects)
    for start, end, text in _marker_frame_ranges(mistranscription_markers or [], settings.rate):
        objects.append(
            generate_exo_object(
                index,
                start,
                end,
                text,
                settings,
                layer=qa_layer,
                font_size=max(24, int(settings.font_size * 0.55)),
                text_color=_diagnostic_text_color(text),
                y_position=max(40.0, settings.y_position - settings.font_size * 1.25),
            )
        )
        index += 1
    for layer_offset, markers in enumerate(extra_marker_layers):
        for start, end, text in _marker_frame_ranges(markers, settings.rate):
            objects.append(
                generate_exo_object(
                    index,
                    start,
                    end,
                    text,
                    settings,
                    layer=qa_layer + layer_offset,
                    font_size=max(24, int(settings.font_size * 0.55)),
                    text_color=_diagnostic_text_color(text),
                    y_position=max(40.0, settings.y_position - settings.font_size * 1.25),
                )
            )
            index += 1
    return header + "\n" + "\n".join(objects) + ("\n" if objects else "\n")
def _validate_media_path(value: str) -> None:
    if "\r" in value or "\n" in value:
        raise ExoWriteError("EXO media path cannot contain a line break")
    _validate_shift_jis_literal("media_plan.source_path", value)


def _diagnostic_text_color(text: str) -> str:
    lowered = text.lower()
    if "montage + voiceover" in lowered:
        return "cc88ff"
    if "cut" in lowered:
        return "ff5555"
    if "condense" in lowered:
        return "ff9900"
    if "keep" in lowered:
        return "66dd88"
    if "connect / review" in lowered:
        return "55ccff"
    if "high" in lowered:
        return "ff0000"
    if "medium" in lowered:
        return "ff9900"
    return "ffff00"


def _marker_frame_ranges(markers: list[ExoMarker], fps: int) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    for marker in sorted(markers, key=lambda item: (item.start_time, item.end_time)):
        start = time_to_frame(marker.start_time, fps)
        end = time_to_frame(marker.end_time, fps)
        if end < start:
            end = start
        ranges.append((start, end, marker.text))
    for i in range(len(ranges) - 1):
        start, end, text = ranges[i]
        next_start = ranges[i + 1][0]
        if end >= next_start:
            ranges[i] = (start, max(start, next_start - 1), text)
    return ranges


def _chapter_marker_frame_ranges(
    markers: list[ExoMarker],
    fps: int,
    total_frames: int,
) -> list[tuple[int, int, str]]:
    ranges = _marker_frame_ranges(markers, fps)
    if not ranges or total_frames < 1:
        return []
    starts_and_titles: list[tuple[int, str]] = []
    for index, (start, _end, text) in enumerate(ranges):
        candidate = 1 if index == 0 else max(starts_and_titles[-1][0] + 1, start)
        if candidate > total_frames:
            break
        starts_and_titles.append((candidate, text))
    contiguous: list[tuple[int, int, str]] = []
    for index, (start, text) in enumerate(starts_and_titles):
        if index + 1 < len(starts_and_titles):
            normalized_end = starts_and_titles[index + 1][0] - 1
        else:
            normalized_end = total_frames
        contiguous.append((start, normalized_end, text))
    return contiguous


def write_exo(path: Path, content: str) -> None:
    try:
        content.encode("shift_jis")
    except UnicodeEncodeError as exc:
        unsupported = content[exc.start : exc.end]
        raise ExoWriteError(
            "Could not encode generated EXO content as Shift-JIS; "
            f"unsupported character {unsupported!r} at character offset {exc.start}"
        ) from exc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="shift_jis")
        os.replace(temporary, path)
    except OSError as exc:
        raise ExoWriteError(f"Could not write EXO file: {path}") from exc
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)


def _validate_shift_jis_literal(field: str, value: str) -> None:
    try:
        value.encode("shift_jis")
    except UnicodeEncodeError as exc:
        unsupported = value[exc.start : exc.end]
        raise ExoWriteError(
            f"EXO setting {field}={value!r} cannot be encoded as Shift-JIS; "
            f"unsupported character {unsupported!r}"
        ) from exc
