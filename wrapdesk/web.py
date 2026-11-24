"""Expose the label inspection desk and its photo preparation workbench."""

import csv
from io import BytesIO, StringIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.exceptions import NotFound

from image_lab import create_app as create_preparation
from image_lab.validation import Invalid, object_value

from . import geometry, photos
from .library import Library


def body():
    return object_value(request.get_json(silent=True), "瓶标操作")


def file_png(data, name):
    return send_file(data, mimetype="image/png", download_name=name,
                     as_attachment="download" in request.args, max_age=0)


def create_app(root):
    app = create_preparation(root / "prepare")
    library = Library(root)
    desk = Blueprint("wrapdesk", __name__, url_prefix="/wrapdesk")

    @app.get("/")
    def home():
        return render_template("wrapdesk.html")

    @desk.get("/health")
    def health():
        return jsonify(status="ready", service="wrapdesk")

    @desk.get("/photos")
    def entries():
        return jsonify(library.list())

    @desk.post("/demo")
    def demo():
        image = photos.demo()
        return jsonify(library.create(image, "合成原照 · 植物标本圆罐", synthetic=True)), 201

    @desk.post("/photos")
    def upload():
        file = request.files.get("image")
        if file is None or not file.filename:
            raise Invalid("请选择一张瓶标照片。")
        image = photos.decode(file.stream.read(photos.MAX_UPLOAD + 1))
        return jsonify(library.create(image, Path(file.filename).stem)), 201

    @desk.post("/photos/prepared")
    def prepared():
        value = body()
        project, result = value.get("project"), value.get("result")
        if not isinstance(project, str) or not isinstance(result, str):
            raise Invalid("请选择可用的照片准备结果。")
        preparation = app.extensions["results"]
        if preparation.metadata(project, result)["stale"]:
            raise Invalid("请重新计算照片准备结果。")
        with preparation.image_path(project, "image", result).open("rb") as stream:
            image = photos.decode(stream.read(photos.MAX_UPLOAD + 1))
        return jsonify(library.create(image, "照片准备 · 瓶标", origin={"project": project, "result": result})), 201

    @desk.get("/photos/<identifier>")
    def open_photo(identifier):
        return jsonify(library.record(identifier))

    @desk.put("/photos/<identifier>")
    def update(identifier):
        value = body()
        return jsonify(library.update(identifier, value.get("revision"), value.get("calibration"), value.get("occlusions")))

    @desk.get("/photos/<identifier>/source.png")
    def source(identifier):
        return file_png(library.source(identifier), "label-source.png")

    def analysis(identifier):
        revision = request.args.get("revision", type=int)
        return library.results(identifier, revision)

    @desk.get("/photos/<identifier>/flatten")
    def flatten_info(identifier):
        _, source, model, boxes = analysis(identifier)
        metadata, _ = geometry.render(source, model, boxes)
        return jsonify(metadata)

    @desk.get("/photos/<identifier>/flatten.png")
    def flattened(identifier):
        _, source, model, boxes = analysis(identifier)
        _, raster = geometry.render(source, model, boxes)
        return file_png(BytesIO(photos.png(raster)), "label-flat.png")

    @desk.get("/photos/<identifier>/probe")
    def probe(identifier):
        report = request.args.get("report")
        if report:
            saved = library.report(identifier, report)
            source = library.image(identifier)
            model = geometry.Cylinder(saved["calibration"])
            boxes = saved["occlusions"]
        else:
            _, source, model, boxes = analysis(identifier)
        return jsonify(geometry.probe(source, model, boxes, request.args.get("column"), request.args.get("row")))

    @desk.get("/photos/<identifier>/transect")
    def transect(identifier):
        _, source, model, boxes = analysis(identifier)
        selected = (request.args.get("start"), request.args.get("end"), request.args.get("row"))
        return jsonify(geometry.transect(source, model, boxes, *selected))

    @desk.get("/photos/<identifier>/reports")
    def reports(identifier):
        return jsonify(library.reports(identifier))

    @desk.post("/photos/<identifier>/reports")
    def archive(identifier):
        value = body()
        return jsonify(library.archive(identifier, value.get("revision"), value.get("title"), value.get("transect"))), 201

    @desk.get("/photos/<identifier>/reports/<report>")
    def open_report(identifier, report):
        return jsonify(library.report(identifier, report))

    @desk.get("/photos/<identifier>/reports/<report>/flatten.png")
    def saved_raster(identifier, report):
        library.report(identifier, report)
        return file_png(library.report_path(identifier, report) / "flattened.png", "label-flat.png")

    @desk.get("/photos/<identifier>/reports/<report>/transect.csv")
    def saved_transect(identifier, report):
        profile = library.report(identifier, report)["transect"]
        if profile is None:
            raise NotFound("这份检查稿未保存横向测线。")
        table = StringIO(newline="")
        writer = csv.writer(table)
        writer.writerow(["column", "row", "source_x", "source_y", "angle", "arc_mm", "height_mm", "visible", "red", "green", "blue"])
        for item in profile["samples"]:
            values = [item[name] for name in ("column", "row", "source_x", "source_y", "angle", "arc_mm", "height_mm", "visible")]
            writer.writerow(values + (item["rgb"] if item["visible"] else ["", "", ""]))
        return send_file(BytesIO(table.getvalue().encode("utf-8")), mimetype="text/csv",
                         download_name="label-transect.csv", as_attachment=True, max_age=0)

    app.register_blueprint(desk)
    return app
