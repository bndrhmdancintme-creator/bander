#!/usr/bin/env python3
"""Render one Arabic caption line into a transparent PNG at canvas size.

Pre-rendering with PIL sidesteps ffmpeg's drawtext, whose Arabic shaping
support is unreliable across builds. PIL/libraqm does its own Arabic
shaping + bidi reordering internally, so raw logical-order text is passed
straight through -- pre-shaping it (e.g. with arabic_reshaper/python-bidi)
would double-process it and garble the output.
"""
from PIL import Image, ImageDraw, ImageFont, features

COLORS = {
    "yellow": (255, 212, 0, 255),
    "red": (255, 45, 40, 255),
    "white": (255, 255, 255, 255),
}

STROKE_COLOR = (0, 0, 0, 255)

if not features.check("raqm"):
    raise RuntimeError(
        "Pillow was built without libraqm -- Arabic shaping/RTL layout will "
        "come out disconnected or reversed. Install libraqm and reinstall "
        "Pillow (pip install --force-reinstall Pillow) before rendering captions."
    )


def fit_font(draw, text, font_path, max_width, max_height, start_size=140, min_size=40):
    size = start_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font, direction="rtl",
                              stroke_width=max(2, size // 22))
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_width and h <= max_height:
            return font, w, h
        size -= 4
    font = ImageFont.truetype(font_path, min_size)
    bbox = draw.textbbox((0, 0), text, font=font, direction="rtl")
    return font, bbox[2] - bbox[0], bbox[3] - bbox[1]


def render_caption(text, color_name, canvas_w, canvas_h, font_path, out_path,
                    vertical_frac=0.40, max_width_frac=0.88):
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_w = int(canvas_w * max_width_frac)
    max_h = int(canvas_h * 0.16)
    font, tw, th = fit_font(draw, text, font_path, max_w, max_h)

    x = (canvas_w - tw) // 2
    y = int(canvas_h * vertical_frac) - th // 2
    stroke_w = max(3, font.size // 20)

    fill = COLORS.get(color_name, COLORS["white"])
    draw.text((x, y), text, font=font, fill=fill, direction="rtl",
               stroke_width=stroke_w, stroke_fill=STROKE_COLOR)

    img.save(out_path)
    return out_path
