"""Cylindrical label projection, source support queries and physical transects."""

from dataclasses import dataclass
from math import ceil, radians

import cv2
import numpy as np
from PIL import Image

from image_lab.validation import Invalid, integer, list_value, number, object_value

MAX_OUTPUT_PIXELS = 2_000_000
SCAN_ROW_LIMIT = 8
LUMA_WEIGHTS = np.array([0.213, 0.715, 0.072])


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


def _line_rows(row0, row1, shape):
    row0 = integer(row0, "测线起行", 0, shape[0] - 1)
    row1 = row0 if row1 is None else integer(row1, "测线止行", 0, shape[0] - 1)
    return row0, row1


def transect(source, model, boxes, column0, column1, row, end_row=None):
    height, width = model.shape
    row0, row1 = _line_rows(row, end_row, model.shape)
    column0 = integer(column0, "测线起列", 0, width - 1)
    column1 = integer(column1, "测线止列", 0, width - 1)
    if column0 > column1:
        column0, column1 = column1, column0
    columns = np.arange(column0, column1 + 1)
    if column1 == column0:
        rows = np.full(columns.shape, float(row0))
    else:
        rows = row0 + (row1 - row0) * (columns - column0) / (column1 - column0)
    x, y, angles, arc_mm, height_mm = model.position(columns, rows)
    x_map = x.astype(np.float32).reshape(1, -1)
    y_map = y.astype(np.float32).reshape(1, -1)
    color = cv2.remap(np.asarray(source), x_map, y_map, interpolation=cv2.INTER_LINEAR)[0]
    valid = supported(x, y, boxes)
    samples = [
        {"column": int(i), "row": float(r), "source_x": float(px), "source_y": float(py),
         "angle": float(angle), "arc_mm": float(arc), "height_mm": float(hm),
         "rgb": rgb.tolist() if present else None, "visible": bool(present)}
        for i, r, px, py, angle, arc, hm, rgb, present in zip(
            columns, rows, x, y, angles, arc_mm, height_mm, color, valid)
    ]
    step_mm = model.arc_length / (width - 1)
    observed_pairs = int(np.count_nonzero(valid[:-1] & valid[1:]))
    return {"row": row0, "end_row": row1, "start_column": column0, "end_column": column1,
            "slanted": bool(row0 != row1), "samples": samples,
            "span_mm": step_mm * (column1 - column0), "observed_mm": step_mm * observed_pairs}


def _luma(rgb):
    return rgb @ LUMA_WEIGHTS


def _source_columns(x, luma):
    """Average one luminance per source-photo column actually traversed by the line."""
    columns = np.rint(x).astype(np.int64)
    order = np.argsort(columns, kind="stable")
    ordered = columns[order]
    groups = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]])
    counts = np.diff(np.r_[groups, ordered.size])
    return ordered[groups], np.add.reduceat(luma[order], groups) / counts


def _otsu(values):
    hist, _ = np.histogram(values, bins=256, range=(0, 255))
    total = values.size
    weighted = np.arange(256) * hist
    sum_all = weighted.sum()
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    sum_bg = np.cumsum(weighted)
    nonzero = (weight_bg > 0) & (weight_fg > 0)
    means_bg = np.divide(sum_bg, weight_bg, where=nonzero, out=np.zeros(256, dtype=float))
    means_fg = np.divide(sum_all - sum_bg, weight_fg, where=nonzero, out=np.zeros(256, dtype=float))
    spread = np.zeros(256)
    spread[nonzero] = (means_bg[nonzero] - means_fg[nonzero]) ** 2
    between = weight_bg * weight_fg * spread
    return float(np.argmax(between)) + 0.5


def _runs(dark):
    """Yield (is_black, run_length) over a binary sequence."""
    if dark.size == 0:
        return
    changes = np.flatnonzero(dark[1:] != dark[:-1]) + 1
    starts = np.r_[0, changes]
    ends = np.r_[changes, dark.size]
    for start, end in zip(starts, ends):
        yield bool(dark[start]), int(end - start)


def barcode_trace(samples, margin_left, margin_right, min_bar_columns, min_contrast):
    """Evaluate one straight scan line in terms of source-photo columns."""
    visible = np.array([sample["visible"] for sample in samples])
    if not visible.all():
        return {"qualified": False, "reason": "缺证"}
    x = np.array([sample["source_x"] for sample in samples])
    rgb = np.array([sample["rgb"] for sample in samples], dtype=float)
    source_x, luma = _source_columns(x, _luma(rgb))
    if luma.size < 3:
        return {"qualified": False, "reason": "原片列不足"}
    threshold = _otsu(luma)
    dark = luma < threshold
    sequence = list(_runs(dark))
    if not sequence or sequence[0][0] or sequence[-1][0] or not any(is_black for is_black, _ in sequence):
        return {"qualified": False, "reason": "两端留白或黑条不成立"}
    left_margin = sequence[0][1]
    right_margin = sequence[-1][1]
    black_spans = [length for is_black, length in sequence if is_black]
    black_values = luma[dark]
    white_values = luma[~dark]
    weakest_contrast = float(white_values.min() - black_values.max())
    result = {
        "qualified": False,
        "reason": "",
        "source_columns": int(luma.size),
        "left_margin_columns": int(left_margin),
        "right_margin_columns": int(right_margin),
        "min_black_span_columns": int(min(black_spans)),
        "black_bar_count": len(black_spans),
        "threshold": round(threshold, 1),
        "weakest_contrast": round(weakest_contrast, 2),
        "luminance_min": round(float(luma.min()), 2),
        "luminance_max": round(float(luma.max()), 2),
    }
    if left_margin < margin_left or right_margin < margin_right:
        result["reason"] = "两端白边不足"
    elif min(black_spans) < min_bar_columns:
        result["reason"] = "黑条过细"
    elif weakest_contrast < min_contrast:
        result["reason"] = "反差不足"
    else:
        result["qualified"] = True
    return result


def barcode_lines(source, model, boxes, column0, column1, row, margin_left, margin_right,
                  min_bar_columns, min_contrast):
    """Search every straight line from column0 to column1 inside +/- eight flat rows."""
    height, width = model.shape
    base = integer(row, "测线所在行", 0, height - 1)
    column0 = integer(column0, "测线起列", 0, width - 1)
    column1 = integer(column1, "测线止列", 0, width - 1)
    if column0 >= column1:
        raise Invalid("条码测线的起列要在止列左侧。")
    margin_left = integer(margin_left, "左端白边", 1, width)
    margin_right = integer(margin_right, "右端白边", 1, width)
    min_bar_columns = integer(min_bar_columns, "黑条最少原片列", 1, width)
    min_contrast = number(min_contrast, "黑白最小反差", 0, 255)
    low = max(0, base - SCAN_ROW_LIMIT)
    high = min(height - 1, base + SCAN_ROW_LIMIT)
    columns = np.arange(column0, column1 + 1)
    span = column1 - column0
    qualified, checked = [], 0
    for row0 in range(low, high + 1):
        for row1 in range(low, high + 1):
            rows = row0 + (row1 - row0) * (columns - column0) / span
            x, y, _, _, _ = model.position(columns, rows)
            if not bool(supported(x, y, boxes).all()):
                continue
            x_map = x.astype(np.float32).reshape(1, -1)
            y_map = y.astype(np.float32).reshape(1, -1)
            color = cv2.remap(np.asarray(source), x_map, y_map, interpolation=cv2.INTER_LINEAR)[0]
            samples = [
                {"source_x": float(px), "rgb": rgb.tolist(), "visible": True}
                for px, rgb in zip(x, color)
            ]
            checked += 1
            metrics = barcode_trace(samples, margin_left, margin_right, min_bar_columns, min_contrast)
            if metrics["qualified"]:
                qualified.append({"row": row0, "end_row": row1, **metrics})
    qualified.sort(key=lambda item: (
        -item["weakest_contrast"],
        -min(item["left_margin_columns"], item["right_margin_columns"]),
        -item["min_black_span_columns"],
        abs(item["row"] - base) + abs(item["end_row"] - base),
        item["row"], item["end_row"],
    ))
    return {
        "start_column": column0, "end_column": column1, "base_row": base,
        "row_limit": SCAN_ROW_LIMIT, "row_low": low, "row_high": high,
        "checked_lines": checked, "qualified_count": len(qualified),
        "candidates": qualified,
        "recommended": qualified[0] if qualified else None,
    }
