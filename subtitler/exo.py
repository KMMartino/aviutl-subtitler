"""AviUtl EXO generation."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ExoWriteError
from .models import ExoMarker, ExoMediaPlan, ExoMediaSegment, ExoSettings, Subtitle


def encode_text_for_exo(text: str) -> str:
    max_bytes = 2048
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > max_bytes:
        encoded = encoded[: max_bytes - 2] + b"\x00\x00"
    if len(encoded) < max_bytes:
        encoded += b"\x00" * (max_bytes - len(encoded))
    return encoded.hex()


def time_to_frame(time_seconds: float, fps: int) -> int:
    return int(time_seconds * fps) + 1


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


def generate_exo_video_object(index: int, segment: ExoMediaSegment, source_path: str) -> str:
    return f"""[{index}]
start={segment.output_start_frame}
end={segment.output_end_frame}
layer=1
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
拡大率=100.00
透明度=0.0
回転=0.00
blend=0"""


def generate_exo_audio_object(index: int, segment: ExoMediaSegment, source_path: str) -> str:
    return f"""[{index}]
start={segment.output_start_frame}
end={segment.output_end_frame}
layer=2
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
音量=100.0
左右=0.0"""


def generate_exo_file(
    subtitles: list[Subtitle],
    settings: ExoSettings,
    total_duration: float,
    insert_initial_empty: bool = True,
    chapter_markers: list[ExoMarker] | None = None,
    mistranscription_markers: list[ExoMarker] | None = None,
    media_plan: ExoMediaPlan | None = None,
) -> str:
    _validate_shift_jis_literal("exo.font", settings.font)
    media_source = ""
    if media_plan is not None:
        media_source = str(media_plan.source_path.resolve())
        _validate_media_path(media_source)
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
            objects.append(generate_exo_video_object(index, segment, media_source))
            index += 1
        for segment in media_plan.segments:
            objects.append(generate_exo_audio_object(index, segment, media_source))
            index += 1
    subtitle_layer = 3 if media_plan is not None else 1
    qa_layer = 4 if media_plan is not None else 2
    chapter_layer = 5 if media_plan is not None else 3
    for start, end, text in frame_ranges:
        objects.append(generate_exo_object(index, start, end, text, settings, layer=subtitle_layer, include_animation=True))
        index += 1
    for start, end, text in _marker_frame_ranges(chapter_markers or [], settings.rate):
        objects.append(generate_exo_object(index, start, end, text, settings, layer=chapter_layer))
        index += 1
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
    return header + "\n" + "\n".join(objects) + ("\n" if objects else "\n")


def _validate_media_path(value: str) -> None:
    if "\r" in value or "\n" in value:
        raise ExoWriteError("EXO media path cannot contain a line break")
    _validate_shift_jis_literal("media_plan.source_path", value)


def _diagnostic_text_color(text: str) -> str:
    lowered = text.lower()
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
