"""Decode cylindrical label photographs to a stable display coordinate system."""

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 3_200_000


def decode(data):
    if len(data) > MAX_UPLOAD:
        raise Invalid("单张原片至多 12 MiB。")
    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if source.format not in {"PNG", "JPEG", "TIFF"} or getattr(source, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG 或 TIFF。")
            if min(width, height) < 20 or width * height > MAX_PIXELS or max(width, height) > 8192:
                raise Invalid("原片短边至少 20 像素，长边至多 8192 像素，总量至多 320 万像素。")
            source = ImageOps.exif_transpose(source)
            if "A" in source.getbands() or "transparency" in source.info:
                flattened = Image.new("RGBA", source.size, "white")
                flattened.alpha_composite(source.convert("RGBA"))
                return flattened.convert("RGB")
            if source.mode.startswith("I;16"):
                raster = np.asarray(source, dtype=np.uint16)
                return Image.fromarray((raster // 257).astype(np.uint8)).convert("RGB")
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("读不到这张原片。") from error


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()



DEMO_CALIBRATION = {
    "top": 80, "bottom": 347, "left": 152, "right": 548,
    "bottom_left": 152, "bottom_right": 548,
    "top_diameter": 73, "bottom_diameter": 73, "diameter": 73,
    "height": 54, "start": -70, "end": 70, "density": 5,
}
DEMO_OCCLUSIONS = [[458, 118, 510, 187]]


def demo():
    """Photograph a programmatically drawn cylindrical paper label."""
    strip = Image.new("RGB", (600, 268), "#e9dac0")
    draw = ImageDraw.Draw(strip)
    draw.rectangle((0, 0, 599, 44), fill="#adc9b4")
    draw.rectangle((0, 223, 599, 267), fill="#245853")
    draw.rectangle((0, 217, 599, 220), fill="#d58351")
    draw.text((65, 14), "NO. 12 / WORKSHOP COLLECTION", fill="#23413e", font_size=18)
    draw.text((173, 65), "FIELD", fill="#245853", font_size=64, stroke_width=1)
    draw.text((166, 132), "BOTANICAL TIN", fill="#8d4934", font_size=27)
    draw.text((196, 183), "BATCH  032    |    54 mm", fill="#23413e", font_size=16)
    draw.text((185, 232), "LABEL STUDY     073", fill="#e9dac0", font_size=18)
    for x in (45, 555):
        draw.line((x, 48, x, 207), fill="#948873", width=2)
        for y in range(56, 207, 16):
            draw.line((x - 6, y, x + 6, y), fill="#948873", width=1)

    y, x = np.indices((440, 700))
    radius = 198
    normalized = (x - 350) / radius
    theta = np.arcsin(np.clip(normalized, -1, 1))
    source_x = np.rint((theta / np.pi + 0.5) * 599).astype(int)
    source_y = np.clip(y - 80, 0, 267)
    surface = np.asarray(strip)[source_y, source_x].astype(float)
    light = 0.70 + 0.29 * np.cos(theta) - 0.035 * np.sin(theta)
    shine = 137 * np.exp(-((x - 488) / 16) ** 2 - ((y - 146) / 42) ** 2)
    surface = np.clip(surface * light[:, :, None] + shine[:, :, None], 0, 255)
    background = np.full_like(surface, [33, 41, 41])
    label = (np.abs(normalized) <= 1) & (y >= 80) & (y <= 347)
    cap = (np.abs(normalized) <= 1) & ((y >= 62) & (y < 80) | (y > 347) & (y <= 369))
    background[cap] = np.array([83, 88, 82]) * light[cap, None]
    background[label] = surface[label]
    raster = Image.fromarray(background.astype(np.uint8))
    graphic = ImageDraw.Draw(raster)
    graphic.text((32, 403), "SYNTHETIC CAPTURE  /  073", fill="#afbbb4", font_size=14)
    return raster
