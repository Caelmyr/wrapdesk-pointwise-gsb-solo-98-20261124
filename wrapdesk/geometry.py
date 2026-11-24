"""Cylindrical label projection, source support queries and physical transects."""

from dataclasses import dataclass
from math import ceil, radians

import cv2
import numpy as np
from PIL import Image

from image_lab.validation import Invalid, integer, list_value, number, object_value

MAX_OUTPUT_PIXELS = 2_000_000


def calibration(value, photo):
    value = object_value(value, "瓶身标定")
    width, height = photo["width"], photo["height"]
    result = {
        "left": integer(value.get("left"), "左边", 0, width - 1),
        "right": integer(value.get("right"), "右边", 0, width - 1),
        "bottom_left": integer(value.get("bottom_left", value.get("left")), "下沿左边", 0, width - 1),
        "bottom_right": integer(value.get("bottom_right", value.get("right")), "下沿右边", 0, width - 1),
        "top": integer(value.get("top"), "上边", 0, height - 1),
        "bottom": integer(value.get("bottom"), "下边", 0, height - 1),
        "diameter": number(value.get("diameter"), "实物直径", 1, 1000),
        "height": number(value.get("height"), "实物高度", 1, 1000),
        "start": number(value.get("start"), "起始角", -80, 75),
        "end": number(value.get("end"), "终止角", -75, 80),
        "density": number(value.get("density"), "每毫米像素数", 1, 16),
    }
    if min(result["right"] - result["left"], result["bottom_right"] - result["bottom_left"],
           result["bottom"] - result["top"]) < 10:
        raise Invalid("瓶身框的宽、高都须至少 10 像素。")
    if result["end"] - result["start"] < 5:
        raise Invalid("起止角之间至少留 5°。")
    Cylinder(result)
    return result


def occlusions(value, photo):
    boxes = []
    for box in list_value(value, "缺证框", 12):
        if not isinstance(box, list) or len(box) != 4:
            raise Invalid("缺证框需要两个角。")
        x0, y0, x1, y1 = (integer(coordinate, "缺证框角点", 0, bound - 1)
                           for coordinate, bound in zip(box, [photo["width"], photo["height"]] * 2))
        if x0 >= x1 or y0 >= y1:
            raise Invalid("缺证框宽高须大于零。")
        boxes.append([x0, y0, x1, y1])
    return boxes


@dataclass(frozen=True)
class Cylinder:
    settings: dict

    @property
    def arc_length(self):
        return self.settings["diameter"] / 2 * radians(self.settings["end"] - self.settings["start"])

    @property
    def shape(self):
        density = self.settings["density"]
        return max(2, ceil(self.settings["height"] * density) + 1), max(2, ceil(self.arc_length * density) + 1)

    def __post_init__(self):
        height, width = self.shape
        if height * width > MAX_OUTPUT_PIXELS:
            raise Invalid("平展稿超过 200 万像素，请降低像素密度。")

    def position(self, column, row):
        c = self.settings
        height, width = self.shape
        fraction = column / (width - 1)
        vertical = row / (height - 1)
        angles = c["start"] + (c["end"] - c["start"]) * fraction
        left = c["left"] + (c.get("bottom_left", c["left"]) - c["left"]) * vertical
        right = c["right"] + (c.get("bottom_right", c["right"]) - c["right"]) * vertical
        x = (left + right) / 2 + (right - left) / 2 * np.sin(np.deg2rad(angles))
        y = c["top"] + (c["bottom"] - c["top"]) * vertical
        return x, y, angles, self.arc_length * fraction, c["height"] * vertical

    def metadata(self):
        height, width = self.shape
        c = self.settings
        return {"width": width, "height": height, "arc_mm": self.arc_length,
                "vertical_mm": c["height"], "angles": [c["start"], c["end"]]}


def supported(x, y, boxes):
    # Bilinear sampling must avoid reading pixels on the edge of an excluded box.
    left, right = np.floor(x), np.ceil(x)
    top, bottom = np.floor(y), np.ceil(y)
    valid = np.ones(np.broadcast_shapes(np.shape(x), np.shape(y)), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        valid &= ~((left <= x1) & (right >= x0) & (top <= y1) & (bottom >= y0))
    return valid


def render(source, model, boxes):
    height, width = model.shape
    columns, rows = np.meshgrid(np.arange(width), np.arange(height), sparse=True)
    x, y, _, _, _ = model.position(columns, rows)
    x_map = np.broadcast_to(x, (height, width)).astype(np.float32)
    y_map = np.broadcast_to(y, (height, width)).astype(np.float32)
    color = cv2.remap(np.asarray(source), x_map, y_map, interpolation=cv2.INTER_LINEAR)
    valid = supported(x, y, boxes)
    color[~valid] = 0
    image = Image.fromarray(np.dstack((color, valid.astype(np.uint8) * 255)))
    return {"visible_pixels": int(valid.sum()), "total_pixels": valid.size, **model.metadata()}, image


def probe(source, model, boxes, column, row):
    height, width = model.shape
    column = integer(column, "平展列", 0, width - 1)
    row = integer(row, "平展行", 0, height - 1)
    x, y, angle, arc_mm, height_mm = model.position(column, row)
    valid = bool(supported(x, y, boxes))
    rgb = None
    if valid:
        rgb = cv2.remap(np.asarray(source), np.asarray([[x]], dtype=np.float32),
                        np.asarray([[y]], dtype=np.float32), interpolation=cv2.INTER_LINEAR)[0, 0].tolist()
    return {"column": column, "row": row, "source_x": float(x), "source_y": float(y),
            "angle": float(angle), "arc_mm": arc_mm, "height_mm": height_mm,
            "rgb": rgb, "visible": valid}


def transect(source, model, boxes, column0, column1, row):
    height, width = model.shape
    row = integer(row, "平展行", 0, height - 1)
    column0 = integer(column0, "测线起列", 0, width - 1)
    column1 = integer(column1, "测线止列", 0, width - 1)
    if column0 > column1:
        column0, column1 = column1, column0
    columns = np.arange(column0, column1 + 1)
    x, y, angles, arc_mm, height_mm = model.position(columns, row)
    x_map = x.astype(np.float32).reshape(1, -1)
    y_map = np.full_like(x_map, y)
    color = cv2.remap(np.asarray(source), x_map, y_map, interpolation=cv2.INTER_LINEAR)[0]
    valid = supported(x, y, boxes)
    samples = [
        {"column": int(i), "row": row, "source_x": float(px), "source_y": float(y),
         "angle": float(angle), "arc_mm": float(arc), "height_mm": float(height_mm),
         "rgb": rgb.tolist() if present else None, "visible": bool(present)}
        for i, px, angle, arc, rgb, present in zip(columns, x, angles, arc_mm, color, valid)
    ]
    step_mm = model.arc_length / (width - 1)
    observed_pairs = int(np.count_nonzero(valid[:-1] & valid[1:]))
    return {"row": row, "start_column": column0, "end_column": column1, "samples": samples,
            "span_mm": step_mm * (column1 - column0), "observed_mm": step_mm * observed_pairs}
