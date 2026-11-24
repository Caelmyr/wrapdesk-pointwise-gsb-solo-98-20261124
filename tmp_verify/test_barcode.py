"""Exercise slanted transects and the barcode rescan search on a synthetic label."""

import sys

import numpy as np
from PIL import Image

sys.path.insert(0, "/workspace")

from wrapdesk import geometry  # noqa: E402

H, W = 260, 320
raster = np.full((H, W, 3), 245, dtype=np.uint8)

calibration = {
    "top": 10, "bottom": 249, "left": 10, "right": 309,
    "bottom_left": 10, "bottom_right": 309,
    "diameter": 80.0, "height": 100.0, "start": -30.0, "end": 30.0, "density": 2.0,
}
model = geometry.Cylinder(calibration)
flat_h, flat_w = model.shape
print("flat shape:", flat_h, flat_w)


def y_of(flat_row):
    return model.position(0, flat_row)[1]


# Paint bars at source x 110..200 with quiet zones 95..110 and 200..215.
bar_x = [(110, 113), (122, 124), (130, 136), (148, 150), (160, 166), (180, 183), (195, 198)]
y_clean0, y_clean1 = y_of(40), y_of(78)
for x0, x1 in bar_x:
    raster[int(y_clean0):int(y_clean1) + 1, x0:x1 + 1] = 20
# Faint band (weak contrast) at flat rows 52..54.
yf0, yf1 = int(y_of(52)), int(y_of(54)) + 1
raster[yf0:yf1, 105:205] = np.maximum(raster[yf0:yf1, 105:205], 150)
# Smudge breaking the white quiet zones at flat rows 73..75.
ys0, ys1 = int(y_of(73)), int(y_of(75)) + 1
raster[ys0:ys1, 98:212] = 120

source = Image.fromarray(raster)

cols = np.arange(flat_w)
x_at_60 = model.position(cols, 60)[0]
c0 = int(np.argmax(x_at_60 >= 95))
c1 = int(np.argmax(x_at_60 >= 215)) - 1
print("scan columns:", c0, c1, "source x:", round(x_at_60[c0], 1), round(x_at_60[c1], 1))

# 1. Horizontal transect at row 60 (clean band).
line = geometry.transect(source, model, [], c0, c1, 60)
assert line["slanted"] is False
assert all(s["visible"] for s in line["samples"])
assert all(s["row"] == 60 for s in line["samples"])
print("horizontal transect samples:", len(line["samples"]))

# 2. Slanted transect: per-point rows differ; source positions follow each point's own row.
slant = geometry.transect(source, model, [], c0, c1, 58, 64)
assert slant["slanted"] is True
rows = [s["row"] for s in slant["samples"]]
assert rows[0] == 58 and rows[-1] == 64 and rows[1] > rows[0]
ys = [s["source_y"] for s in slant["samples"]]
assert ys[0] < ys[-1]
print("slanted rows/y:", rows[0], rows[-1], round(ys[0], 2), round(ys[-1], 2))

# 3. Barcode search centered at row 60.
result = geometry.barcode_lines(source, model, [], c0, c1, 60, 4, 4, 1, 25)
print("checked:", result["checked_lines"], "qualified:", result["qualified_count"])
assert result["checked_lines"] == 17 * 17
assert result["qualified_count"] > 0
pick = result["recommended"]
print("recommended:", pick["row"], pick["end_row"], "contrast", pick["weakest_contrast"],
      "margins", pick["left_margin_columns"], pick["right_margin_columns"],
      "min bar", pick["min_black_span_columns"], "source cols", pick["source_columns"])
assert pick["weakest_contrast"] >= 25
assert pick["left_margin_columns"] >= 4 and pick["right_margin_columns"] >= 4
# A fully-faint horizontal line (both endpoints inside rows 52-54) never qualifies.
assert all(not (c["row"] == c["end_row"] and 52 <= c["row"] <= 54) for c in result["candidates"])
trace53 = geometry.barcode_trace(
    geometry.transect(source, model, [], c0, c1, 53)["samples"], 4, 4, 1, 25)
print("row53:", trace53)
assert not trace53["qualified"] and trace53["reason"] == "反差不足"
# Smudge rows 73-75 fail on end white margins.
trace74 = geometry.barcode_trace(
    geometry.transect(source, model, [], c0, c1, 74)["samples"], 4, 4, 1, 25)
print("row74:", trace74)
assert not trace74["qualified"] and trace74["reason"] == "两端白边不足"

# 4. An occlusion spanning the full +/-8 row band disqualifies every line crossing it.
x0, y0, _, _, _ = model.position(c0 + 5, 60)
ytop = model.position(0, result["row_low"])[1]
ybottom = model.position(0, result["row_high"])[1]
box = [int(x0) - 2, int(ytop), int(x0) + 2, int(ybottom) + 1]
blocked = geometry.barcode_lines(source, model, [box], c0, c1, 60, 4, 4, 1, 25)
print("blocked checked:", blocked["checked_lines"], "qualified:", blocked["qualified_count"])
assert blocked["qualified_count"] == 0
blk_line = geometry.transect(source, model, [box], c0, c1, 60)
assert any(not s["visible"] for s in blk_line["samples"])
assert all(s["rgb"] is None for s in blk_line["samples"] if not s["visible"])

# 5. Impossible contrast threshold -> nothing qualifies; caller keeps the original line.
none = geometry.barcode_lines(source, model, [], c0, c1, 60, 4, 4, 1, 250)
assert none["recommended"] is None and none["qualified_count"] == 0

# 6. A thin ink line one source column wide counts as one distinct source column,
#    even though the flattened view magnifies it across several flat pixels.
tiny = np.full((120, 260, 3), 250, dtype=np.uint8)
cal = dict(calibration, top=10, bottom=109, left=10, right=249, height=100.0, density=4.0)
m2 = geometry.Cylinder(cal)
_, w2 = m2.shape
xx = m2.position(np.arange(w2), 60)[0]
ax = int(np.argmax(xx >= 100))
bx = int(np.argmax(xx >= 185)) - 1
paint_x = int(round(m2.position((ax + bx) // 2, 60)[0]))
paint_y0, paint_y1 = int(m2.position(0, 50)[1]), int(m2.position(0, 70)[1]) + 1
tiny[paint_y0:paint_y1, paint_x] = 0
res = geometry.barcode_lines(Image.fromarray(tiny), m2, [], ax, bx, 60, 2, 2, 1, 50)
print("tiny qualified:", res["qualified_count"])
assert res["qualified_count"] > 0
rec = res["recommended"]
print("tiny metrics:", {k: rec[k] for k in
      ("source_columns", "min_black_span_columns", "left_margin_columns", "right_margin_columns")})
assert rec["min_black_span_columns"] == 1
print("ALL GEOMETRY CHECKS PASSED")
