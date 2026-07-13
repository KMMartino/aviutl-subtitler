# AviUtl EXO Subtitle Format

This documents the `.exo` file and text object format used by
`aviutl_subtitle.py`.

The EXO container and literal fields are written as Shift-JIS text. In
particular, the configured `exo.font` value must be Shift-JIS encodable; the
generator validates it and reports an error instead of replacing unsupported
characters. Subtitle and marker payloads are different: their `text=` values
are UTF-16LE encoded as hexadecimal, so Japanese text support is independent
of the container's Shift-JIS encoding.

```python
open(output_path, "w", encoding="shift_jis")
```

## File Header

Every file starts with an `[exedit]` section:

```ini
[exedit]
width=2560
height=1440
rate=60
scale=1
length=TOTAL_FRAMES
audio_rate=48000
audio_ch=2
```

Fields:

- `width`: video width in pixels.
- `height`: video height in pixels.
- `rate`: frame rate.
- `scale`: AviUtl timeline scale. This program uses `1`.
- `length`: total project length in frames.
- `audio_rate`: audio sample rate.
- `audio_ch`: audio channel count.

Frame numbers are 1-based:

```python
frame = int(time_seconds * fps) + 1
```

If a subtitle end frame is not after its start frame, the end frame is forced
to `start + 1`. If adjacent subtitles overlap, the earlier subtitle is trimmed
to end before the next subtitle when possible. When two subtitle starts fall on
the same or adjacent frames, the earlier subtitle is still kept at least one
frame long so it remains visible in AviUtl.

## Subtitle Object

Each subtitle is an indexed object. The first subtitle uses `[0]`, the second
uses `[1]`, and so on.

Template:

```ini
[INDEX]
start=START_FRAME
end=END_FRAME
layer=1
overlay=1
camera=0
[INDEX.0]
_name=テキスト
サイズ=60
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
color=ffffff
color2=00ffff
font=M+ 2p heavy
text=TEXT_HEX
[INDEX.1]
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
[INDEX.2]
_name=縁取り
サイズ=1
ぼかし=100
color=000000
file=
[INDEX.3]
_name=縁取り
サイズ=2
ぼかし=0
color=000000
file=
[INDEX.4]
_name=シャドー
X=4
Y=2
濃さ=100.0
拡散=0
影を別オブジェクトで描画=0
color=000000
file=
[INDEX.5]
_name=縁取り
_disable=1
サイズ=10
ぼかし=50
color=ffffff
file=
[INDEX.6]
_name=標準描画
X=0.0
Y=717.0
Z=0.0
拡大率=100.00
透明度=0.0
回転=0.00
blend=0
```

The object has these parts:

- `[INDEX]`: timeline placement and compositing metadata.
- `[INDEX.0]`: the actual AviUtl text object.
- `[INDEX.1]`: disabled gradient filter.
- `[INDEX.2]`: first outline filter.
- `[INDEX.3]`: second outline filter.
- `[INDEX.4]`: shadow filter.
- `[INDEX.5]`: disabled outline filter.
- `[INDEX.6]`: standard drawing settings.

## Text Encoding

The `text=` field is not plain text. It is a fixed-size UTF-16LE byte buffer
encoded as lowercase hexadecimal.

Rules:

1. Encode the subtitle string as UTF-16LE.
2. Append a UTF-16LE null terminator: `00 00`.
3. The full text buffer must be exactly `2048` bytes.
4. Render those bytes as hex, producing exactly `4096` hex characters.
5. If the encoded text is shorter than `2048` bytes, pad the remainder with
   zero bytes.
6. If the encoded text is longer than `2048` bytes, truncate to leave room for
   the final `00 00` terminator.

The trailing zeroes are necessary. Do not strip or shorten the `text=` value.
A valid `text=` field always contains the encoded text, its `0000` terminator,
and enough trailing `00` padding to fill the 2048-byte buffer.

Reference implementation:

```python
def encode_text_for_exo(text: str) -> str:
    max_bytes = 2048

    encoded = text.encode("utf-16-le")
    encoded += b"\x00\x00"

    if len(encoded) > max_bytes:
        encoded = encoded[: max_bytes - 2] + b"\x00\x00"

    if len(encoded) < max_bytes:
        encoded += b"\x00" * (max_bytes - len(encoded))

    return encoded.hex()
```

Example for `あ`:

```text
UTF-16LE bytes before padding:
42 30 00 00

text=42300000...
```

The `...` represents the required trailing zero padding out to `4096` hex
characters.
