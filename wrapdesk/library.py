"""Store original photographs, editable inspections and immutable reports."""

from hashlib import sha256
import json
import re
import shutil
from threading import RLock
from uuid import uuid4

from PIL import Image
from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid, list_value, timestamp

from . import geometry, photos

KEY = re.compile(r"[0-9a-f]{16}")


def key(value):
    if not isinstance(value, str) or KEY.fullmatch(value) is None:
        raise NotFound("找不到此原片或检查稿。")
    return value


def title(value, default):
    return value.strip()[:100] if isinstance(value, str) and value.strip() else default


def write(path, data):
    scratch = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        scratch.write_bytes(data)
        scratch.replace(path)
    finally:
        scratch.unlink(missing_ok=True)


def save(path, value):
    write(path, json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def read(path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise NotFound("找不到此原片或检查稿。") from None


def check_revision(record, revision):
    if type(revision) is not int or record["revision"] != revision:
        raise Conflict("原片设置已改变，请重开此原片。")


def normalise(record):
    """Fill the two edge diameters on records written before the frustum model.

    Older records carry one ``diameter`` for a true cylinder; both edges take
    that value, so their readings and proofs stay exactly as they were.
    """
    calibration = record.get("calibration")
    if calibration is not None:
        calibration.setdefault("top_diameter", calibration["diameter"])
        calibration.setdefault("bottom_diameter", calibration["diameter"])
    return record


class Library:
    def __init__(self, root):
        self.root = root / "label-photos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def folder(self, identifier):
        return self.root / key(identifier)

    def record(self, identifier):
        return normalise(read(self.folder(identifier) / "photo.json"))

    def source(self, identifier):
        self.record(identifier)
        return self.folder(identifier) / "source.png"

    def image(self, identifier):
        with Image.open(self.source(identifier)) as opened:
            return opened.convert("RGB")

    def list(self):
        records = (read(path) for path in self.root.glob("*/photo.json"))
        return [
            {key: record[key] for key in ("id", "title", "synthetic")}
            for record in sorted(records, key=lambda record: record["created"], reverse=True)
        ]

    def create(self, image, name, synthetic=False, origin=None):
        identifier = uuid4().hex[:16]
        target = self.folder(identifier)
        scratch = target.with_name("." + identifier)
        scratch.mkdir()
        try:
            raw = photos.png(image)
            write(scratch / "source.png", raw)
            record = {
                "id": identifier, "title": title(name, "未命名瓶标"), "created": timestamp(),
                "synthetic": synthetic, "source_origin": origin, "source_sha256": sha256(raw).hexdigest(),
                "width": image.width, "height": image.height, "revision": 1,
                "calibration": photos.DEMO_CALIBRATION if synthetic else None,
                "occlusions": photos.DEMO_OCCLUSIONS if synthetic else [],
            }
            save(scratch / "photo.json", record)
            scratch.rename(target)
        finally:
            if scratch.exists():
                shutil.rmtree(scratch)
        return record

    def update(self, identifier, revision, calibration, boxes):
        with self.lock:
            record = self.record(identifier)
            check_revision(record, revision)
            record["calibration"] = geometry.calibration(calibration, record)
            record["occlusions"] = geometry.occlusions(boxes, record)
            record["revision"] += 1
            save(self.folder(identifier) / "photo.json", record)
            return record

    def results(self, identifier, revision=None):
        record = self.record(identifier)
        if revision is not None:
            check_revision(record, revision)
        if record["calibration"] is None:
            raise Invalid("先应用瓶身标定。")
        return record, self.image(identifier), geometry.Cylinder(record["calibration"]), record["occlusions"]

    def reports(self, identifier):
        self.record(identifier)
        entries = [read(path) for path in self.folder(identifier).glob("reports/*/report.json")]
        return [
            {key: entry[key] for key in ("id", "title", "created")}
            for entry in sorted(entries, key=lambda entry: entry["created"], reverse=True)
        ]

    def report_path(self, identifier, report):
        self.record(identifier)
        return self.folder(identifier) / "reports" / key(report)

    def report(self, identifier, report):
        saved = normalise(read(self.report_path(identifier, report) / "report.json"))
        saved.setdefault("ruler", geometry.ruler_advice(saved["calibration"]))
        return saved

    def archive(self, identifier, revision, label, selection):
        with self.lock:
            record = self.record(identifier)
            check_revision(record, revision)
            record, source, model, boxes = self.results(identifier, revision)
            metadata, raster = geometry.render(source, model, boxes)
            profile = None
            if selection is not None:
                values = list_value(selection, "测线", 3)
                if len(values) != 3:
                    raise Invalid("测线需要所在行和两个端点。")
                profile = geometry.transect(source, model, boxes, *values)
            saved = {
                "id": uuid4().hex[:16], "title": title(label, "瓶标检查稿"), "created": timestamp(),
                "photo_id": identifier, "source_sha256": record["source_sha256"], "revision": revision,
                "calibration": record["calibration"], "occlusions": boxes,
                "ruler": geometry.ruler_advice(record["calibration"]),
                "geometry": metadata, "transect": profile,
            }
            target = self.report_path(identifier, saved["id"])
            target.parent.mkdir(exist_ok=True)
            scratch = target.with_name("." + saved["id"])
            scratch.mkdir()
            try:
                write(scratch / "flattened.png", photos.png(raster))
                save(scratch / "report.json", saved)
                scratch.rename(target)
            finally:
                if scratch.exists():
                    shutil.rmtree(scratch)
            return saved
