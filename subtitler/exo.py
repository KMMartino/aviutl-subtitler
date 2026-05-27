"""AviUtl EXO generation."""

from __future__ import annotations

from pathlib import Path

from .errors import ExoWriteError
from .models import ExoMarker, ExoSettings, Subtitle


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
) -> str:
    encoded_text = encode_text_for_exo(text)
    return f"""[{index}]
start={start_frame}
end={end_frame}
layer={layer}
overlay=1
camera=0
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
[{index}.6]
_name=標準描画
X=0.0
Y={settings.y_position}
Z=0.0
拡大率=100.00
透明度=0.0
回転=0.00
blend=0"""


def generate_exo_file(
    subtitles: list[Subtitle],
    settings: ExoSettings,
    total_duration: float,
    insert_initial_empty: bool = True,
    vad_markers: list[ExoMarker] | None = None,
    chain_markers: list[ExoMarker] | None = None,
    mistranscription_markers: list[ExoMarker] | None = None,
) -> str:
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
    for start, end, text in frame_ranges:
        objects.append(generate_exo_object(index, start, end, text, settings, layer=1))
        index += 1
    for start, end, text in _marker_frame_ranges(vad_markers or [], settings.rate):
        objects.append(generate_exo_object(index, start, end, text, settings, layer=2))
        index += 1
    for start, end, text in _marker_frame_ranges(chain_markers or [], settings.rate):
        objects.append(generate_exo_object(index, start, end, text, settings, layer=3))
        index += 1
    for start, end, text in _marker_frame_ranges(mistranscription_markers or [], settings.rate):
        objects.append(generate_exo_object(index, start, end, text, settings, layer=4))
        index += 1
    return header + "\n" + "\n".join(objects) + ("\n" if objects else "\n")


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
        path.write_text(content, encoding="shift_jis", errors="replace")
    except Exception as exc:
        raise ExoWriteError(f"Could not write EXO file: {path}") from exc
