"""End-to-end checks for barcode rescan endpoints, slanted archiving and CSV export."""

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, "/workspace")

from wrapdesk.web import create_app  # noqa: E402

app = create_app(Path("/workspace/tmp_verify/data"))
client = app.test_client()

# Build a source image with an obvious barcode band.
H, W = 260, 320
raster = np.full((H, W, 3), 245, dtype=np.uint8)
for x0, x1 in [(110, 113), (122, 124), (130, 136), (148, 150), (160, 166), (180, 183), (195, 198)]:
    raster[60:180, x0:x1 + 1] = 15
buf = io.BytesIO()
Image.fromarray(raster).save(buf, format="PNG")
res = client.post("/wrapdesk/photos", data={"image": (io.BytesIO(buf.getvalue()), "can.png")},
                  content_type="multipart/form-data")
assert res.status_code == 201, res.data
pid = res.get_json()["id"]

calibration = {
    "top": 10, "bottom": 249, "left": 10, "right": 309, "bottom_left": 10, "bottom_right": 309,
    "diameter": 80, "height": 100, "start": -30, "end": 30, "density": 2,
}
res = client.put(f"/wrapdesk/photos/{pid}", json={"revision": 1, "calibration": calibration, "occlusions": []})
assert res.status_code == 200, res.data
revision = res.get_json()["revision"]
assert revision == 2

info = client.get(f"/wrapdesk/photos/{pid}/flatten?revision={revision}").get_json()
fw = info["width"]

# Derive columns around the barcode via the probe geometry endpoint.
def x_of(column, row):
    r = client.get(f"/wrapdesk/photos/{pid}/probe?revision={revision}&column={column}&row={row}")
    return r.get_json()["source_x"]

c0 = next(c for c in range(fw) if x_of(c, 60) >= 95)
c1 = next(c for c in range(fw) if x_of(c, 60) >= 215) - 1

# 1. Barcode search endpoint.
res = client.get(f"/wrapdesk/photos/{pid}/barcode-lines?revision={revision}"
                 f"&start={c0}&end={c1}&row=60&margin_left=4&margin_right=4"
                 f"&min_bar_columns=1&min_contrast=25")
assert res.status_code == 200, res.data
data = res.get_json()
assert data["row_limit"] == 8 and data["qualified_count"] > 0
pick = data["recommended"]
print("recommended:", pick["row"], pick["end_row"], pick["weakest_contrast"],
      pick["left_margin_columns"], pick["right_margin_columns"])

# 2. Adopt the recommended (possibly slanted) line through the transect endpoint.
res = client.get(f"/wrapdesk/photos/{pid}/transect?revision={revision}"
                 f"&start={c0}&end={c1}&row={pick['row']}&end_row={pick['end_row']}")
assert res.status_code == 200, res.data
line = res.get_json()
assert line["end_row"] == pick["end_row"]
# Each sample must be read at its own row and its own source position.
ys = [s["source_y"] for s in line["samples"]]
if line["slanted"]:
    assert ys[0] != ys[-1] and all(s["row"] != line["samples"][0]["row"] or s is line["samples"][0]
                                  for s in line["samples"])
assert all(s["visible"] for s in line["samples"])

# 3. Bad parameters are rejected with the shared error format.
res = client.get(f"/wrapdesk/photos/{pid}/barcode-lines?revision={revision}"
                 f"&start={c1}&end={c0}&row=60&margin_left=4&margin_right=4"
                 f"&min_bar_columns=1&min_contrast=25")
assert res.status_code == 400 and "起列" in res.get_json()["error"]

# 4. Archive the slanted line and reopen: CSV rows carry per-point rows and real positions.
res = client.post(f"/wrapdesk/photos/{pid}/reports", json={
    "revision": revision, "title": "斜线封存",
    "transect": [c0, c1, pick["row"], pick["end_row"]]})
assert res.status_code == 201, res.data
report = res.get_json()
rid = report["id"]
assert report["transect"]["slanted"] == (pick["row"] != pick["end_row"])
csv_res = client.get(f"/wrapdesk/photos/{pid}/reports/{rid}/transect.csv")
assert csv_res.status_code == 200
text = csv_res.data.decode()
header, *rows_csv = [r for r in text.strip().split("\n") if r]
assert header.split(",")[:4] == ["column", "row", "source_x", "source_y"]
first, last = rows_csv[0].split(","), rows_csv[-1].split(",")
assert float(first[1]) == pick["row"] and float(last[1]) == pick["end_row"]
assert float(first[3]) != float(last[3]) or not line["slanted"]
print("CSV head:", rows_csv[0])
print("CSV tail:", rows_csv[-1])

# 5. A three-element (horizontal) selection still archives and round-trips.
res = client.post(f"/wrapdesk/photos/{pid}/reports", json={
    "revision": revision, "title": "横线封存", "transect": [c0, c1, 60]})
assert res.status_code == 201
assert res.get_json()["transect"]["slanted"] is False

# 6. Saved-report probe keeps working against archived geometry.
r = client.get(f"/wrapdesk/photos/{pid}/probe?report={rid}&column={c0}&row=60")
assert r.status_code == 200 and r.get_json()["visible"] is True
print("ALL HTTP CHECKS PASSED")
