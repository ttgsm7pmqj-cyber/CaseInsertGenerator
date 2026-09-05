# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run generation recovery and staged-export regressions inside FreeCAD.

Import this module and call ``run()``. Each check closes only the document it
creates; the runner neither exits FreeCAD nor touches existing documents.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import Part


ROOT = Path(__file__).resolve().parents[1]


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _snapshot(doc):
    records = []
    for obj in doc.Objects:
        record = {
            "name": obj.Name,
            "id": obj.ID,
            "type": obj.TypeId,
            "label": obj.Label,
        }
        for prop in ("ProjectJSON", "ParameterJSON", "GeneratedResults", "GeneratedResult"):
            if prop in obj.PropertiesList:
                value = getattr(obj, prop)
                record[prop] = list(value) if prop == "GeneratedResults" else str(value)
        if "Group" in obj.PropertiesList:
            record["children"] = sorted(item.Name for item in obj.Group)
        if "Shape" in obj.PropertiesList and not obj.Shape.isNull():
            shape = obj.Shape
            bounds = shape.BoundBox
            record["shape"] = {
                "valid": shape.isValid(),
                "solids": len(shape.Solids),
                "volume": round(shape.Volume, 7),
                "area": round(shape.Area, 7),
                "bounds": [round(value, 7) for value in (
                    bounds.XMin, bounds.YMin, bounds.ZMin,
                    bounds.XMax, bounds.YMax, bounds.ZMax)],
            }
        records.append(record)
    return sorted(records, key=lambda item: item["name"])


def _legacy_params():
    return {
        "insert_type": "Dividers", "case_model": "Custom Case",
        "internal_length": 120.0, "internal_width": 90.0,
        "insert_depth": 30.0, "corner_radius": 8.0,
        "side_clearance": 0.0, "bottom_clearance": 1.0,
        "taper_allowance": 0.0, "base_thickness": 2.4,
        "outer_wall": 2.4, "divider_wall": 2.0, "divider_height": 15.0,
        "rows": 2, "columns": 2, "divider_layout": "Equal grid",
        "bed_x": 256.0, "bed_y": 256.0, "bed_margin": 5.0,
        "split_for_bed": False, "lid_clearance_source": "unknown",
    }


def run():
    sys.path.insert(0, str(ROOT))
    engine = importlib.import_module("freecad.CaseInsertGenerator.engine")
    _require(
        Path(engine.__file__).resolve() ==
        (ROOT / "freecad" / "CaseInsertGenerator" / "engine.py").resolve(),
        "Recovery tests loaded a different checkout's engine")
    helpers = runpy.run_path(str(ROOT / "tests" / "freecad_integration.py"))
    base_spec = helpers["_base_spec"]
    lid_spec = helpers["_lid_panel_spec"]
    new_document = helpers["_unique_document"]
    close_document = helpers["_close_document"]
    records = []

    def check(name, operation):
        try:
            details = operation()
            records.append({"name": name, "status": "passed", "details": details})
        except Exception as exc:
            records.append({
                "name": name, "status": "failed", "error": str(exc),
                "traceback": traceback.format_exc(),
            })

    def baseline(name):
        doc = new_document("Recovery_" + name)
        sentinel = doc.addObject("Part::Feature", "UserOwnedReference")
        sentinel.Label = "Unrelated design must survive generation"
        sentinel.Shape = Part.makeBox(5.0, 6.0, 7.0)
        engine.generate_project(
            base_spec(length=120.0, width=90.0, depth=30.0), document=doc)
        doc.clearUndos()
        return doc

    variants = {
        "composer": (
            engine.generate_project,
            lambda: base_spec(length=126.0, width=96.0, depth=32.0)),
        "lid_panel": (
            engine.generate_lid_panel_project,
            lambda: lid_spec(length=180.0, width=120.0)),
        "lid_preview": (
            engine.preview_lid_panel_project,
            lambda: lid_spec(length=180.0, width=120.0, clearance=None)),
        "legacy_dividers": (engine.generate_insert, _legacy_params),
    }

    def rollback(mode, undo_enabled=True):
        generate, specification = variants[mode]
        doc = baseline(mode)
        try:
            doc.UndoMode = int(undo_enabled)
            before = _snapshot(doc)
            store = engine._add_parameter_object
            reached_storage = []

            def fail_after_parameters(*args, **kwargs):
                result = store(*args, **kwargs)
                reached_storage.append(result.Name)
                raise RuntimeError("injected failure after parameter storage")

            with patch.object(engine, "_add_parameter_object", fail_after_parameters):
                try:
                    generate(specification(), document=doc)
                except RuntimeError as exc:
                    _require("injected failure" in str(exc), str(exc))
                else:
                    raise AssertionError("Injected storage failure did not propagate")
            _require(bool(reached_storage), "Check never reached document storage")
            _require(_snapshot(doc) == before, "Rollback changed prior IDs, geometry, or JSON")
            _require(doc.UndoCount == 0, "Failed generation left an Undo entry")
            _require(not doc.HasPendingTransaction, "Failed generation left a transaction open")
            _require(bool(doc.UndoMode) == undo_enabled, "Failure changed the document Undo setting")
            return {"objects_preserved": len(before), "undo_mode_preserved": True}
        finally:
            close_document(doc)

    def undo_success(mode):
        generate, specification = variants[mode]
        doc = baseline("Undo_" + mode)
        try:
            before = _snapshot(doc)
            generate(specification(), document=doc)
            _require(_snapshot(doc) != before, "The replacement did not change the project")
            _require(doc.UndoCount == 1, "Successful replacement is not one Undo step")
            _require(not doc.HasPendingTransaction, "Generation left a transaction open")
            doc.undo()
            doc.recompute()
            _require(_snapshot(doc) == before, "One Undo did not restore the previous project")
            return {"objects_restored": len(before), "undo_steps": 1}
        finally:
            close_document(doc)

    for mode in variants:
        check("rollback_" + mode, lambda mode=mode: rollback(mode))
        check("single_undo_" + mode, lambda mode=mode: undo_success(mode))
    check("rollback_with_undo_disabled", lambda: rollback("composer", False))

    def invalid_metadata(mode):
        generate, specification = variants[mode]
        doc = baseline("Metadata_" + mode)
        try:
            before = _snapshot(doc)
            spec = specification()
            spec["verification"] = []
            try:
                generate(spec, document=doc)
            except (ValueError, TypeError, AttributeError):
                pass
            else:
                raise AssertionError("Malformed verification was accepted")
            _require(_snapshot(doc) == before, "Malformed metadata replaced the last good project")
            _require(doc.UndoCount == 0, "Rejected metadata left an Undo step")
            return {"objects_preserved": len(before)}
        finally:
            close_document(doc)

    check("invalid_lid_metadata_preserves_project", lambda: invalid_metadata("lid_panel"))
    check("invalid_preview_metadata_preserves_project", lambda: invalid_metadata("lid_preview"))

    def pending_transaction():
        doc = baseline("Pending")
        try:
            doc.openTransaction("User operation in progress")
            doc.getObject("UserOwnedReference").Label = "Uncommitted user edit"
            before = _snapshot(doc)
            try:
                engine.generate_project(base_spec(), document=doc)
            except RuntimeError as exc:
                _require("Finish the active FreeCAD operation" in str(exc), str(exc))
            else:
                raise AssertionError("Generation consumed another operation's transaction")
            _require(doc.HasPendingTransaction, "Caller transaction was closed")
            _require(_snapshot(doc) == before, "Caller operation was changed")
            doc.abortTransaction()
            return {"caller_transaction_preserved": True}
        finally:
            close_document(doc)

    check("caller_transaction_is_preserved", pending_transaction)

    def real_export(format_name):
        import Mesh

        doc = new_document("Recovery_Export_" + format_name)
        try:
            engine.generate_lid_panel_project(
                lid_spec(length=180.0, width=120.0), document=doc)
            generated = engine.active_results(doc)
            _require(len(generated) > 1, "Export test requires numbered outputs")
            export = getattr(engine, "export_" + format_name)
            with tempfile.TemporaryDirectory(prefix="caseinsert-cad-export-") as directory:
                base = Path(directory) / ("panel." + format_name)
                paths = engine.export_paths(base, doc=doc)
                Path(paths[0]).write_bytes(b"existing numbered part")
                try:
                    export(base, doc=doc)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("Existing numbered part was replaced without approval")
                _require(Path(paths[0]).read_bytes() == b"existing numbered part", "Rejection changed bytes")
                _require(len(list(Path(directory).iterdir())) == 1, "Rejection created partial outputs")
                returned = export(base, doc=doc, overwrite=True)
                _require(returned == paths, "Export paths differ from confirmed paths")
                for path in paths:
                    if format_name == "step":
                        shape = Part.read(path)
                        _require(not shape.isNull() and shape.isValid(), "STEP is invalid")
                        _require(len(shape.Solids) > 0, "STEP has no solid")
                    else:
                        mesh = Mesh.Mesh(path)
                        _require(mesh.isSolid(), "STL is not a closed mesh")
                _require(not base.exists(), "Numbered export also wrote the base filename")
                _require(len(list(Path(directory).iterdir())) == len(paths), "Staging files leaked")
                return {"parts_reopened": len(paths), "collision_preserved": True}
        finally:
            close_document(doc)

    check("staged_step_exports_reopen", lambda: real_export("step"))
    check("staged_stl_exports_reopen", lambda: real_export("stl"))
    return {
        "summary": {
            "passed": sum(item["status"] == "passed" for item in records),
            "failed": sum(item["status"] == "failed" for item in records),
            "total": len(records),
        },
        "tests": records,
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
