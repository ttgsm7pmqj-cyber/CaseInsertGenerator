# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run editing-sequence regressions inside an actual FreeCAD GUI process.

Import this module after Qt starts, then call ``run(output_directory)``.
Only file pickers, overwrite decisions, and error-message presentation are
replaced. Widgets, controller actions, CAD, document persistence, and exports
remain real. The runner closes only documents and dialogs it created.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import Part

from freecad.CaseInsertGenerator import engine as E
from freecad.CaseInsertGenerator import project_model as M


def _spec(width=20.0, object_id="rectangular-pocket-01"):
    return {
        "schema_version": 1,
        "case": {"case_model": "Custom Case", "internal_length": 160.0,
                 "internal_width": 110.0, "insert_depth": 32.0,
                 "corner_radius": 5.0, "side_clearance": 0.0,
                 "bottom_clearance": 0.0, "taper_allowance": 0.0},
        "lid": {"source": "unknown", "clearance_mm": None},
        "layers": {"enabled": False, "ratio": 0.5, "floor_mm": 2.4},
        "containment": {"mode": "none", "clearance_mm": 0.4,
                        "panel_thickness_mm": 2.0},
        "printer": {"bed_x": 256.0, "bed_y": 256.0, "margin": 5.0, "split": False},
        "objects": [{"id": object_id, "type": "rectangular_pocket",
                     "name": "Original pocket", "x": 25.0, "y": 25.0,
                     "rotation": 0.0, "layer": "lower", "locked": False,
                     "width": width, "length": 30.0, "height": 10.0}],
    }


def _lid_spec(thickness=3.0):
    spec = _spec()
    spec["objects"] = []
    spec["lid"] = {"source": "measured", "clearance_mm": 20.0,
                   "envelope_source": "measured", "length_mm": 160.0,
                   "width_mm": 110.0}
    spec["lid_panel"] = M.default_lid_panel()
    spec["lid_panel"].update(enabled=True, thickness_mm=thickness)
    return spec


class GuiStateRegressions(unittest.TestCase):
    output_directory = None

    def setUp(self):
        self.output = Path(self.output_directory) / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        self.documents = []
        self.controllers = []
        self.details = {}
        active = App.ActiveDocument
        self.previous_active = (active.Name, active) if active is not None else None

    def tearDown(self):
        for controller in self.controllers:
            controller.dialog.close()
        # FreeCAD invalidates a closed document's Python wrapper, including
        # its Name attribute. Retain names while documents are alive and check
        # identity so a different document reusing a name is never closed.
        for name, doc in reversed(self.documents):
            if App.listDocuments().get(name) is doc:
                App.closeDocument(name)
        if self.previous_active is not None:
            name, doc = self.previous_active
            if App.listDocuments().get(name) is doc:
                App.setActiveDocument(name)

    def remember_document(self, doc):
        self.documents.append((doc.Name, doc))
        return doc

    def document(self, name, spec=None, lid=False):
        doc = App.newDocument("GuiRegression_" + name)
        self.remember_document(doc)
        if spec is not None:
            (E.generate_lid_panel_project if lid else E.generate_project)(
                spec, document=doc)
        return doc

    def controller(self, doc=None):
        App.setActiveDocument(doc.Name if doc is not None else "")
        controller = E.CaseInsertDialog()
        controller.errors = []

        def record_error(exc):
            controller.errors.append(str(exc))
            controller.status.setText("Error: %s" % exc)

        controller._show_error = record_error
        controller.dialog.resize(1040, 900)
        controller.show()
        self.controllers.append(controller)
        controller.QtWidgets.QApplication.processEvents()
        return controller

    def reopen(self, doc, path):
        App.closeDocument(doc.Name)
        reopened = App.openDocument(str(path))
        self.remember_document(reopened)
        return reopened

    def capture(self, controller, name):
        controller.QtWidgets.QApplication.processEvents()
        path = self.output / (name + ".png")
        self.assertTrue(controller.dialog.grab().save(str(path), "PNG"))
        self.assertGreater(path.stat().st_size, 1024)
        self.details.setdefault("screenshots", []).append(str(path))

    def test_save_keeps_current_composer_edits_after_cold_reopen(self):
        doc = self.document("Save", _spec())
        controller = self.controller(doc)
        controller.object_list.setCurrentRow(0)
        controller.object_width.setValue(42.0)
        controller.object_x.setValue(28.0)
        controller.internal_l.setValue(180.0)
        extra = dict(_spec()["objects"][0], id="extra-pocket", x=110.0, y=65.0)
        controller.project_canvas.add_object(extra)
        controller._add_object_list_item(extra)
        path = self.output / "current.FCStd"
        controller._save_path = lambda *_args: str(path)
        controller.save_fcstd_button.click()
        self.assertEqual(controller.errors, [])
        self.capture(controller, "saved-current-controls")
        controller.dialog.close()
        reopened = E.load_project(self.reopen(doc, path))
        objects = {obj["id"]: obj for obj in reopened["objects"]}
        self.assertEqual(objects["rectangular-pocket-01"]["width"], 42.0)
        self.assertEqual(objects["rectangular-pocket-01"]["x"], 28.0)
        self.assertEqual(reopened["case"]["internal_length"], 180.0)
        self.assertIn("extra-pocket", objects)
        self.details["reopened_object_count"] = len(objects)

    def test_cancelled_save_does_not_regenerate(self):
        doc = self.document("Cancel", _spec())
        before = E.load_project(doc)
        controller = self.controller(doc)
        controller.object_width.setValue(42.0)
        controller._save_path = lambda *_args: ""
        controller.save_fcstd_button.click()
        self.assertEqual(E.load_project(doc), before)
        self.assertEqual(controller.errors, [])

    def test_dirty_lid_export_is_blocked_then_exports_current_geometry(self):
        doc = self.document("Lid", _lid_spec(10.0), lid=True)
        controller = self.controller(doc)
        controller.workflow_tabs.setCurrentIndex(1)
        controller.lid_clearance.setValue(8.0)
        controller.panel_thickness.setValue(3.0)
        self.assertTrue(M.lid_panel_height_budget(controller._project_spec())["printable"])
        self.assertFalse(controller.export_step_button.isEnabled())
        self.assertFalse(controller.export_stl_button.isEnabled())
        controller._save_path = lambda *_args: str(self.output / "stale.step")
        controller._export_step()
        controller._export_stl()
        self.assertEqual(len(controller.errors), 2)
        self.assertTrue(all("Settings changed" in error for error in controller.errors))
        self.assertEqual(list(self.output.glob("*.step")), [])
        self.assertEqual(list(self.output.glob("*.stl")), [])
        self.capture(controller, "stale-export-blocked")
        controller.errors.clear()
        controller._set_export_parts_checked(False)
        controller.export_parts.item(0).setCheckState(controller.QtCore.Qt.Checked)
        selected = controller._selected_export_names()
        controller.generate_button.click()
        self.assertEqual(controller.errors, [])
        self.assertEqual(controller._selected_export_names(), selected)
        self.assertTrue(controller.export_step_button.isEnabled())
        for extension, action in (("step", controller._export_step),
                                  ("stl", controller._export_stl)):
            path = self.output / ("current." + extension)
            controller._save_path = lambda *_args, path=path: str(path)
            action()
            self.assertEqual(controller.errors, [])
            self.assertTrue(path.exists())
            if extension == "step":
                shape = Part.Shape()
                shape.read(str(path))
                thickness = shape.BoundBox.ZLength
            else:
                thickness = Mesh.Mesh(str(path)).BoundBox.ZLength
            self.assertAlmostEqual(thickness, 3.0, places=3)
            self.details[extension + "_panel_thickness_mm"] = thickness

    def test_actions_stay_with_the_dialog_document(self):
        first = self.document("First", _spec(20.0))
        controller = self.controller(first)
        second = self.document("Second", _spec(55.0))
        controller.object_width.setValue(25.0)
        controller.generate_button.click()
        self.assertEqual(controller.errors, [])
        self.assertEqual(E.load_project(first)["objects"][0]["width"], 25.0)
        self.assertEqual(E.load_project(second)["objects"][0]["width"], 55.0)
        controller.object_width.setValue(32.0)
        saved = self.output / "first.FCStd"
        controller._save_path = lambda *_args: str(saved)
        controller.save_fcstd_button.click()
        self.assertEqual(controller.errors, [])
        self.assertEqual(E.load_project(first)["objects"][0]["width"], 32.0)
        self.assertEqual(E.load_project(second)["objects"][0]["width"], 55.0)
        output = self.output / "first.step"
        controller._save_path = lambda *_args: str(output)
        controller.export_step_button.click()
        self.assertEqual(controller.errors, [])
        shape = Part.Shape()
        shape.read(str(output))
        self.assertAlmostEqual(shape.Volume, E.active_result(first).Shape.Volume, places=3)
        self.assertIn(first.Name, controller.dialog.windowTitle())
        self.capture(controller, "owned-document-after-export")
        controller.dialog.close()
        reopened = E.load_project(self.reopen(first, saved))
        self.assertEqual(reopened["objects"][0]["width"], 32.0)

    def test_closed_or_replaced_document_cannot_redirect_actions(self):
        first = self.document("Closed", _spec())
        controller = self.controller(first)
        name = first.Name
        App.closeDocument(name)
        second = App.newDocument(name)
        self.remember_document(second)
        E.generate_project(_spec(55.0), document=second)
        before = E.load_project(second)
        path = self.output / "closed.FCStd"
        controller._save_path = lambda *_args: str(path)
        controller._generate()
        controller._save_fcstd()
        controller._export_step()
        self.assertEqual(len(controller.errors), 3)
        self.assertTrue(all("was closed" in error for error in controller.errors))
        self.assertEqual(E.load_project(second), before)
        self.assertFalse(path.exists())

    def test_new_dialog_does_not_adopt_a_later_active_document(self):
        controller = self.controller()
        controller._reset()
        second = self.document("Later", _spec(55.0))
        controller._generate()
        self.assertEqual(controller.errors, [])
        created = controller._document
        self.remember_document(created)
        self.assertIsNot(created, second)
        self.assertEqual(E.load_project(second)["objects"][0]["width"], 55.0)

    def _legacy_roundtrip(self, mode):
        doc = self.document("Legacy")
        controller = self.controller(doc)
        controller._set_case_selection("Custom Case")
        controller.internal_l.setValue(160.0)
        controller.internal_w.setValue(110.0)
        controller.insert_depth.setValue(40.0)
        controller.mode_combo.setCurrentIndex(mode)
        controller.rows.setValue(4)
        controller.columns.setValue(2)
        controller.divider_height.setValue(20.0)
        controller.svg_path.setText(str(Path(E.macro_directory()) / "examples" / "example_cutout.svg"))
        controller.svg_scale.setValue(0.3)
        controller.cutout_depth.setValue(6.0)
        controller.through_cut.setChecked(False)
        controller._generate()
        self.assertEqual(controller.errors, [])
        path = self.output / "legacy.FCStd"
        controller._save_path = lambda *_args: str(path)
        controller._save_fcstd()
        self.assertEqual(controller.errors, [])
        before = json.loads(E._find_parameter_object(doc).ParameterJSON)
        volume = sum(obj.Shape.Volume for obj in E.active_results(doc))
        controller.dialog.close()
        doc = self.reopen(doc, path)
        loaded = self.controller(doc)
        self.assertEqual(loaded.mode_combo.currentIndex(), mode)
        self.assertEqual(loaded.internal_l.value(), 160.0)
        self.assertEqual(loaded.rows.value(), 4)
        self.assertEqual(loaded._params(), before)
        loaded._generate()
        self.assertEqual(loaded.errors, [])
        self.assertAlmostEqual(sum(obj.Shape.Volume for obj in E.active_results(doc)), volume, places=3)
        loaded.internal_l.setValue(170.0)
        next_path = self.output / "legacy-edited.FCStd"
        loaded._save_path = lambda *_args: str(next_path)
        loaded._save_fcstd()
        self.assertEqual(loaded.errors, [])
        loaded.dialog.close()
        reopened = self.reopen(doc, next_path)
        parameters = json.loads(E._find_parameter_object(reopened).ParameterJSON)
        self.assertEqual(parameters["internal_length"], 170.0)
        self.details["mode"] = parameters["insert_type"]

    def test_divider_parameters_roundtrip(self):
        self._legacy_roundtrip(2)

    def test_svg_parameters_roundtrip(self):
        self._legacy_roundtrip(1)

    def test_case_blank_parameters_roundtrip(self):
        self._legacy_roundtrip(4)

    def _minimal_api_roundtrip(self, mode):
        # _as_float reads required keys; its third argument is a minimum.
        # Omit only actual optional keys consumed through params.get().
        params = {
            "internal_length": 160.0, "internal_width": 110.0,
            "insert_depth": 32.0, "corner_radius": 5.0,
            "side_clearance": 0.0, "bottom_clearance": 0.0,
            "taper_allowance": 0.0,
        }
        if mode == 2:
            params.update(insert_type="Dividers", base_thickness=2.4,
                          outer_wall=2.4, divider_wall=1.6, divider_height=20.0)
        elif mode == 1:
            # SVG Cutout is itself the default when insert_type is omitted.
            params.update(
                svg_path=str(Path(E.macro_directory()) / "examples" / "example_cutout.svg"),
                svg_scale=0.3, svg_x=10.0, svg_y=10.0, svg_rotation=0.0,
                svg_clearance=0.0, cutout_depth=6.0)
        else:
            params["insert_type"] = "Case Blank"
        doc = self.document("MinimalAPI")
        E.generate_insert(params, document=doc)
        volume = sum(obj.Shape.Volume for obj in E.active_results(doc))
        stored = json.loads(E._find_parameter_object(doc).ParameterJSON)
        path = self.output / "minimal-api.FCStd"
        E.save_fcstd(str(path), doc=doc)
        doc = self.reopen(doc, path)
        controller = self.controller(doc)
        self.assertEqual(controller.mode_combo.currentIndex(), mode)
        self.assertEqual(controller.rows.value(), 1)
        self.assertEqual(controller.columns.value(), 1)
        self.assertEqual(controller.bed_x.value(), E.DEFAULT_BED)
        self.assertEqual(controller.bed_y.value(), E.DEFAULT_BED)
        self.assertEqual(controller.bed_margin.value(), 5.0)
        self.assertFalse(controller.through_cut.isChecked())
        self.assertFalse(controller.invert_svg.isChecked())
        self.assertFalse(controller.split_for_bed.isChecked())
        self.assertEqual(controller.lid_clearance_source.currentData(), "unknown")
        self.assertEqual(controller._params(), stored)
        controller._generate()
        self.assertEqual(controller.errors, [])
        self.assertEqual(json.loads(E._find_parameter_object(doc).ParameterJSON), stored)
        self.assertAlmostEqual(sum(obj.Shape.Volume for obj in E.active_results(doc)), volume, places=3)
        self.details["mode"] = mode
        self.details["unchanged_volume_mm3"] = volume

    def test_minimal_api_divider_defaults_roundtrip(self):
        self._minimal_api_roundtrip(2)

    def test_minimal_api_svg_defaults_roundtrip(self):
        self._minimal_api_roundtrip(1)

    def test_minimal_api_blank_defaults_roundtrip(self):
        self._minimal_api_roundtrip(4)

    def test_reopened_auto_layout_does_not_repack_manual_edits_on_save(self):
        spec = _spec()
        spec["layout_strategy"] = "balanced"
        spec["case"]["geometry_provenance"] = "Synthetic GUI regression"
        doc = self.document("HistoricLayout", spec)
        path = self.output / "automatic.FCStd"
        E.save_fcstd(str(path), doc=doc)
        doc = self.reopen(doc, path)
        controller = self.controller(doc)
        object_id = spec["objects"][0]["id"]
        item = controller.project_canvas.items[object_id]
        item.setPos(60.0, 30.0)
        controller.QtWidgets.QApplication.processEvents()
        moved = next(obj for obj in controller.project_canvas.to_objects() if obj["id"] == object_id)
        self.assertEqual((moved["x"], moved["y"]), (60.0, 30.0))
        saved = self.output / "manual.FCStd"
        controller._save_path = lambda *_args: str(saved)
        controller._save_fcstd()
        self.assertEqual(controller.errors, [])
        self.capture(controller, "manual-placement-saved")
        controller.dialog.close()
        doc = self.reopen(doc, saved)
        project = E.load_project(doc)
        pocket = next(obj for obj in project["objects"] if obj["id"] == object_id)
        self.assertEqual((pocket["x"], pocket["y"]), (60.0, 30.0))
        self.assertEqual(project["case"]["geometry_provenance"], spec["case"]["geometry_provenance"])
        probe = Part.makeBox(2.0, 2.0, 2.0, App.Vector(65.0, 35.0, 25.0))
        self.assertAlmostEqual(E.active_result(doc).Shape.common(probe).Volume, 0.0, places=5)
        self.details["reopened_xy_mm"] = [pocket["x"], pocket["y"]]

    def test_external_lid_shape_change_blocks_generate_save_and_exports(self):
        doc = self.document("ShapeChange", _lid_spec(), lid=True)
        controller = self.controller(doc)
        panel = E.active_results(doc)[0]
        bounds = panel.Shape.BoundBox
        panel.Shape = Part.makeBox(bounds.XLength, bounds.YLength, 10.0)
        doc.recompute()
        controller._save_path = lambda *_args: str(self.output / "stale.FCStd")
        controller._export_step()
        controller._export_stl()
        controller._save_fcstd()
        controller._generate()
        self.assertEqual(len(controller.errors), 4)
        self.assertTrue(all("geometry changed outside" in error for error in controller.errors))
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(panel.Shape.BoundBox.ZLength, 10.0)
        self.assertEqual(E.load_project(doc)["lid_panel"]["thickness_mm"], 3.0)

    def test_external_part_placement_change_blocks_export(self):
        doc = self.document("PlacementChange", _spec())
        controller = self.controller(doc)
        part = E.active_result(doc)
        part.Placement = App.Placement(App.Vector(5.0, 0.0, 0.0), App.Rotation())
        doc.recompute()
        controller._save_path = lambda *_args: str(self.output / "moved.step")
        controller._export_step()
        self.assertEqual(len(controller.errors), 1)
        self.assertIn("geometry changed outside", controller.errors[0])
        self.assertFalse((self.output / "moved.step").exists())

    def test_supported_metadata_and_precision_survive_gui_save(self):
        spec = _spec(object_id="rectangular-pocket-02")
        spec["layers"].update(floor_mm=4.0, ratio=0.54321)
        spec["case"]["geometry_provenance"] = "GUI regression synthetic dimensions"
        spec["verification"] = {"physical_fit": False, "status": "synthetic-test"}
        spec["objects"][0]["x"] = 25.12345
        doc = self.document("Metadata", spec)
        stored = E.load_project(doc)
        controller = self.controller(doc)
        hydrated = controller._project_spec()
        self.assertEqual(hydrated["layers"], stored["layers"])
        self.assertEqual(hydrated["verification"], stored["verification"])
        self.assertEqual(hydrated["case"]["geometry_provenance"], stored["case"]["geometry_provenance"])
        self.assertEqual(hydrated["objects"][0]["x"], 25.12345)
        controller.object_width.setValue(42.0)
        path = self.output / "metadata.FCStd"
        controller._save_path = lambda *_args: str(path)
        controller._save_fcstd()
        self.assertEqual(controller.errors, [])
        controller.dialog.close()
        reopened = E.load_project(self.reopen(doc, path))
        self.assertEqual(reopened["layers"], stored["layers"])
        self.assertEqual(reopened["verification"], stored["verification"])
        self.assertEqual(reopened["objects"][0]["x"], 25.12345)
        self.assertEqual(reopened["objects"][0]["width"], 42.0)

    def test_sparse_ids_remain_addable(self):
        doc = self.document("IDs", _spec(object_id="rectangular-pocket-02"))
        controller = self.controller(doc)
        controller.object_type.setCurrentIndex(controller.object_type.findData("rectangular_pocket"))
        controller._add_composer_object()
        self.assertEqual(controller.errors, [])
        ids = [item["id"] for item in controller.project_canvas.to_objects()]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertIn("rectangular-pocket-02", ids)

    def test_fit_and_selection_keep_warnings_and_export_readiness(self):
        doc = self.document("Fit", _spec())
        controller = self.controller(doc)
        warning = "Generated model; physical fit remains unverified."
        controller.status.setText(warning)
        controller.project_canvas.scene.clearSelection()
        controller._fit_view()
        controller.QtWidgets.QApplication.processEvents()
        self.assertEqual(controller.status.text(), warning)
        self.assertTrue(controller.export_step_button.isEnabled())
        self.capture(controller, "fit-with-preserved-warning")

    def test_external_generation_or_undo_requires_reloading(self):
        doc = self.document("External", _spec())
        controller = self.controller(doc)
        E.generate_project(_spec(55.0), document=doc)
        controller._generate()
        self.assertTrue(any("changed outside" in error for error in controller.errors))
        self.assertEqual(E.load_project(doc)["objects"][0]["width"], 55.0)
        controller.dialog.close()
        loaded = self.controller(doc)
        doc.UndoMode = 1
        loaded.object_width.setValue(42.0)
        loaded._generate()
        self.assertEqual(loaded.errors, [])
        doc.undo()
        self.assertEqual(E.load_project(doc)["objects"][0]["width"], 55.0)
        loaded._export_step()
        self.assertTrue(any("changed outside" in error for error in loaded.errors))

    def test_numbered_export_cancel_preserves_files_and_confirm_replaces(self):
        doc = self.document("Overwrite", _lid_spec(), lid=True)
        controller = self.controller(doc)
        base = self.output / "existing.step"
        target = self.output / "existing_part_01.step"
        sentinel = b"Previous export must survive cancellation."
        target.write_bytes(sentinel)
        seen = []
        controller._save_path = lambda *_args: str(base)
        controller._confirm_export_overwrite = lambda paths: seen.extend(paths) or False
        controller._export_step()
        self.assertEqual(controller.errors, [])
        self.assertEqual(seen, [str(target)])
        self.assertEqual(target.read_bytes(), sentinel)
        self.assertFalse(base.exists())
        self.assertEqual(len(list(self.output.glob("*.step"))), 1)
        controller._confirm_export_overwrite = lambda paths: True
        controller._export_step()
        self.assertEqual(controller.errors, [])
        self.assertNotEqual(target.read_bytes(), sentinel)
        self.assertEqual(len(list(self.output.glob("*.step"))), controller.export_parts.count())

    def test_unknown_lid_save_keeps_editable_preview(self):
        doc = self.document("Preview")
        controller = self.controller(doc)
        controller.mode_combo.setCurrentIndex(3)
        controller.panel_thickness.setValue(4.0)
        controller.lid_clearance_source.setCurrentIndex(controller.lid_clearance_source.findData("unknown"))
        path = self.output / "preview.FCStd"
        controller._save_path = lambda *_args: str(path)
        controller._save_fcstd()
        self.assertEqual(controller.errors, [])
        self.assertFalse(controller.export_step_button.isEnabled())
        controller.dialog.close()
        project = E.load_project(self.reopen(doc, path))
        self.assertEqual(project["lid_panel"]["thickness_mm"], 4.0)
        self.assertEqual(project["lid"]["source"], "unknown")


def run(output_directory=None):
    output = Path(output_directory or tempfile.mkdtemp(prefix="caseinsert-gui-"))
    output.mkdir(parents=True, exist_ok=True)
    GuiStateRegressions.output_directory = output
    records = []

    class Result(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            records.append({"name": test._testMethodName, "status": "PASS", "details": test.details})

        def addFailure(self, test, error):
            super().addFailure(test, error)
            records.append({"name": test._testMethodName, "status": "FAIL",
                            "traceback": self._exc_info_to_string(error, test), "details": test.details})

        def addError(self, test, error):
            super().addError(test, error)
            records.append({"name": test._testMethodName, "status": "ERROR",
                            "traceback": self._exc_info_to_string(error, test), "details": test.details})

    result = Result()
    unittest.defaultTestLoader.loadTestsFromTestCase(GuiStateRegressions).run(result)
    failed = len(result.errors) + len(result.failures)
    report = {
        "summary": {"passed": result.testsRun - failed, "failed": failed, "total": result.testsRun},
        "tests": records,
        "screenshots": [path for record in records for path in record["details"].get("screenshots", [])],
        "engine": E.__file__,
        "freecad_version": App.Version(),
    }
    (output / "gui-state-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
