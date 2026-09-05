#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Generate one synthetic assembled/exploded inside-lid panel example."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any

import FreeCAD as App

from scripts.artifact_audit import EXAMPLE_LICENSE, EXAMPLE_LICENSE_URL, scan_fcstd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "lid-panel"
RENDER_WIDTH = 1400
RENDER_HEIGHT = 1000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def synthetic_lid_panel_spec() -> dict[str, Any]:
    from freecad.CaseInsertGenerator.project_model import default_lid_panel

    panel = default_lid_panel()
    panel.update({
        "enabled": True,
        "pattern": "slot_grid",
        "thickness_mm": 3.0,
        "payload_thickness_mm": 8.0,
        "edge_inset_mm": 4.0,
        "corner_radius_mm": 8.0,
    })
    panel["slot_grid"].update({
        "slot_length_mm": 26.0,
        "slot_width_mm": 4.5,
        "pitch_x_mm": 36.0,
        "pitch_y_mm": 24.0,
        "margin_x_mm": 11.0,
        "margin_y_mm": 10.0,
        "orientation": "horizontal",
    })
    panel["keepouts"].update({
        "rim_mm": 4.0,
        "seal_mm": 3.0,
        "hinge_mm": 16.0,
        "hinge_edge": "top",
        "clearance_margin_mm": 3.0,
        "rectangles": [{
            "label": "Synthetic lid rib keep-out",
            "x_mm": 91.0,
            "y_mm": 41.0,
            "length_mm": 34.0,
            "width_mm": 18.0,
        }],
    })
    panel["mounting"].update({
        "perimeter_enabled": True,
        "retainers_enabled": True,
        "retainer_count": 4,
        "retainer_width_mm": 11.0,
        "retainer_projection_mm": 3.2,
        "retainer_clearance_mm": 0.35,
        "lift_access_enabled": True,
        "lift_access_diameter_mm": 20.0,
        "fastener_holes_enabled": True,
        "fastener_hole_diameter_mm": 3.5,
        "fastener_edge_offset_mm": 16.0,
        "custom_fastener_holes": [],
    })
    panel["splitting"].update({
        "keyed_alignment": True,
        "key_size_mm": 8.0,
        "key_clearance_mm": 0.25,
    })
    return {
        "schema_version": 1,
        "case": {
            "case_model": "Custom Case",
            "internal_length": 250.0,
            "internal_width": 170.0,
            "insert_depth": 34.0,
            "corner_radius": 10.0,
            "side_clearance": 1.0,
            "bottom_clearance": 0.5,
            "taper_allowance": 0.5,
            "geometry_provenance": "synthetic-demonstration",
            "compatibility_claim": "none",
        },
        "lid": {
            "source": "cad-derived",
            "clearance_mm": 22.0,
            "envelope_source": "cad-derived",
            "length_mm": 250.0,
            "width_mm": 170.0,
        },
        "lid_panel": panel,
        "layers": {"enabled": False, "ratio": 0.5, "floor_mm": 2.4},
        "containment": {
            "mode": "none",
            "clearance_mm": 0.4,
            "panel_thickness_mm": 2.0,
        },
        "printer": {
            "bed_x": 256.0,
            "bed_y": 256.0,
            "margin": 5.0,
            "split": True,
        },
        "objects": [],
        "verification": {
            "geometry_provenance": "synthetic-demonstration",
            "evidence_scope": "synthetic CAD envelope and clearance only",
            "physical_fit": False,
            "status": "physical-fit unverified",
            "compatibility_claim": "none",
        },
    }


def _qt_modules():
    try:
        from PySide import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _style_document(document: Any) -> None:
    colours = {
        "LidPanel": (0.18, 0.48, 0.78),
        "LidPanelRetainers": (0.95, 0.48, 0.12),
        "LidEnvelopeReference": (0.72, 0.76, 0.82),
        "CaseRimPlane": (0.95, 0.65, 0.10),
        "ClosedLidCeiling": (0.35, 0.80, 0.55),
    }
    for obj in document.Objects:
        view = getattr(obj, "ViewObject", None)
        if view is None:
            continue
        colour_name = ("LidPanelRetainers"
                       if obj.Name.startswith("LidPanelRetainer")
                       else obj.Name)
        if colour_name in colours:
            view.ShapeColor = colours[colour_name]
        if obj.Name == "LidEnvelopeReference":
            view.LineColor = colours[obj.Name]
            view.LineWidth = 3.0
        if obj.Name in ("CaseRimPlane", "ClosedLidCeiling"):
            view.Transparency = 72
        if obj.Name == "LidPanelTools":
            view.Visibility = False


def _annotated_render(document: Any, output: Path, view_label: str) -> dict[str, Any]:
    import FreeCADGui as Gui

    QtCore, QtGui, QtWidgets = _qt_modules()
    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError("Lid-panel example rendering requires the FreeCAD GUI")
    App.setActiveDocument(document.Name)
    view = Gui.activeDocument().activeView()
    view.viewAxonometric()
    view.fitAll()
    QtWidgets.QApplication.processEvents()
    with tempfile.TemporaryDirectory(prefix="cig-lid-panel-render-") as directory:
        raw_path = Path(directory) / "raw.png"
        view.saveImage(str(raw_path), RENDER_WIDTH, RENDER_HEIGHT - 104, "White")
        QtWidgets.QApplication.processEvents()
        raw = QtGui.QImage(str(raw_path))
        if raw.isNull():
            raise RuntimeError("FreeCAD did not produce a readable lid-panel render")
        canvas = QtGui.QImage(
            RENDER_WIDTH, RENDER_HEIGHT, QtGui.QImage.Format_ARGB32)
        canvas.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(canvas)
        try:
            painter.fillRect(0, 0, RENDER_WIDTH, 104, QtGui.QColor(28, 33, 40))
            painter.setPen(QtGui.QColor(255, 255, 255))
            title_font = QtGui.QFont("Sans Serif", 24)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.drawText(
                34, 10, RENDER_WIDTH - 68, 48,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                "Synthetic Inside-lid Equipment Panel — %s" % view_label)
            painter.setFont(QtGui.QFont("Sans Serif", 13))
            painter.setPen(QtGui.QColor(194, 205, 219))
            painter.drawText(
                34, 58, RENDER_WIDTH - 68, 30,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                "original synthetic CAD evidence · no compatibility claim · physical fit unverified")
            scaled = raw.scaled(
                RENDER_WIDTH, RENDER_HEIGHT - 104,
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            painter.drawImage(
                (RENDER_WIDTH - scaled.width()) // 2,
                104 + (RENDER_HEIGHT - 104 - scaled.height()) // 2,
                scaled)
        finally:
            painter.end()
        if not canvas.save(str(output), "PNG"):
            raise RuntimeError("Could not save annotated lid-panel render")
    rendered = QtGui.QImage(str(output))
    if rendered.isNull() or rendered.width() != RENDER_WIDTH or rendered.height() != RENDER_HEIGHT:
        raise RuntimeError("Lid-panel render has the wrong size or is unreadable")
    samples = {
        int(rendered.pixel(x, y))
        for x in range(0, rendered.width(), max(1, rendered.width() // 14))
        for y in range(0, rendered.height(), max(1, rendered.height() // 10))
    }
    if len(samples) < 5:
        raise RuntimeError("Lid-panel render appears blank")
    return {
        "path": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "width": rendered.width(),
        "height": rendered.height(),
        "sample_colours": len(samples),
    }


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _remove_own_freecad_backups(fcstd_path: Path) -> None:
    """Remove timestamped backups created by overwriting this one output."""
    for backup in fcstd_path.parent.glob(f"{fcstd_path.stem}.*.FCBak"):
        if backup.is_file():
            backup.unlink()


def generate_lid_panel_example(repo_root=ROOT, output_root=OUTPUT,
                               render=False) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    from freecad.CaseInsertGenerator import engine

    expected_engine = root / "freecad" / "CaseInsertGenerator" / "engine.py"
    if Path(engine.__file__).resolve() != expected_engine:
        raise RuntimeError("Lid-panel example imported a different engine checkout")
    spec = synthetic_lid_panel_spec()
    spec_path = output / "synthetic-lid-equipment-panel.json"
    assembled_path = output / "synthetic-lid-equipment-panel.FCStd"
    exploded_path = output / "synthetic-lid-equipment-panel-exploded.FCStd"
    assembled_png = output / "synthetic-lid-equipment-panel.png"
    exploded_png = output / "synthetic-lid-equipment-panel-exploded.png"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    documents = []
    try:
        assembled = App.newDocument("CIGSyntheticLidPanelAssembled")
        documents.append(assembled)
        assembled.License = EXAMPLE_LICENSE
        assembled.LicenseURL = EXAMPLE_LICENSE_URL
        assembled_report = engine.generate_lid_panel_project(spec, document=assembled)
        assembled_payload = (
            assembled_report.to_mapping()
            if hasattr(assembled_report, "to_mapping") else dict(assembled_report))
        _style_document(assembled)
        engine.save_fcstd(str(assembled_path), assembled)
        _remove_own_freecad_backups(assembled_path)
        assembled_render = (
            _annotated_render(assembled, assembled_png, "Assembled")
            if render else None)

        exploded = App.newDocument("CIGSyntheticLidPanelExploded")
        documents.append(exploded)
        exploded.License = EXAMPLE_LICENSE
        exploded.LicenseURL = EXAMPLE_LICENSE_URL
        exploded_report = engine.generate_lid_panel_project(spec, document=exploded)
        exploded_payload = (
            exploded_report.to_mapping()
            if hasattr(exploded_report, "to_mapping") else dict(exploded_report))
        _style_document(exploded)
        panel = exploded.getObject("LidPanel")
        retainers = [
            exploded.getObject(name)
            for name in exploded_payload["results"]
            if str(name).startswith("LidPanelRetainer")
        ]
        if panel is None or not retainers or any(item is None for item in retainers):
            raise RuntimeError("Synthetic example did not create panel and retainer parts")
        panel.Placement.Base = panel.Placement.Base + App.Vector(0.0, 0.0, 24.0)
        retainer_centres = [(52.0 + index * 42.0, -25.0, 10.0)
                            for index in range(len(retainers))]
        for retainer, target in zip(retainers, retainer_centres):
            bounds = retainer.Shape.BoundBox
            retainer.Placement.Base = retainer.Placement.Base + App.Vector(
                target[0] - (bounds.XMin + bounds.XMax) / 2.0,
                target[1] - (bounds.YMin + bounds.YMax) / 2.0,
                target[2] - bounds.ZMin,
            )
        exploded.recompute()
        engine.save_fcstd(str(exploded_path), exploded)
        _remove_own_freecad_backups(exploded_path)
        exploded_render = (
            _annotated_render(exploded, exploded_png, "Exploded")
            if render else None)

        manifest = {
            "schema_version": 1,
            "generated_on": datetime.now(timezone.utc).date().isoformat(),
            "generator": "Case Insert Generator",
            "title": "Synthetic Inside-lid Equipment Panel",
            "geometry_provenance": "synthetic-demonstration",
            "compatibility_claim": "none",
            "physical_fit_status": "unverified",
            "rendered": bool(render),
            "panel_pattern": "slot_grid",
            "printable_parts": assembled_payload["parts"],
            "pattern_count": assembled_payload["project"]["lid_panel_report"]["pattern_count"],
            "height_budget": assembled_payload["height_budget"],
            "assembled": {
                "fcstd": _artifact(assembled_path),
                "png": assembled_render,
                "result_names": assembled_payload["results"],
            },
            "exploded": {
                "fcstd": _artifact(exploded_path),
                "png": exploded_render,
                "result_names": exploded_payload["results"],
                "exploded_layout": {
                    "LidPanel_offset_mm": [0.0, 0.0, 24.0],
                    "LidPanelRetainer_centres_mm": [
                        list(item) for item in retainer_centres],
                },
            },
            "spec": _artifact(spec_path),
        }
        manifest["ok"] = bool(
            assembled_payload["valid"] and exploded_payload["valid"] and
            assembled_payload.get("printable") is True and
            manifest["physical_fit_status"] == "unverified" and
            scan_fcstd(assembled_path, require_example_license=True)["ok"] and
            scan_fcstd(exploded_path, require_example_license=True)["ok"] and
            (not render or (assembled_render and exploded_render)))
        manifest_path = output / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return manifest
    finally:
        for document in reversed(documents):
            if document.Name in App.listDocuments():
                App.closeDocument(document.Name)


if __name__ == "__main__":
    print(json.dumps(generate_lid_panel_example(render=False), sort_keys=True))
