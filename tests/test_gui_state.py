# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure regressions for preserving data behind the GUI's visible controls."""

import copy
import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

from freecad.CaseInsertGenerator.project_model import layout_project


with patch.dict(sys.modules, {"FreeCAD": types.ModuleType("FreeCAD"),
                              "Part": types.ModuleType("Part")}):
    engine = importlib.import_module("freecad.CaseInsertGenerator.engine")


class ControlPersistenceTests(unittest.TestCase):
    def test_unchanged_controls_keep_precision_floor_and_evidence(self):
        base = {
            "layers": {"ratio": 0.54321, "floor_mm": 4.0},
            "case": {"internal_length": 160.12345, "geometry_provenance": "measurement"},
            "verification": {"physical_fit": False},
        }
        controls = {"layers": {"ratio": 0.54},
                    "case": {"internal_length": 160.12}}
        merged = engine._overlay_edited_controls(base, controls, controls)
        self.assertEqual(merged, base)
        merged["verification"]["physical_fit"] = True
        self.assertFalse(base["verification"]["physical_fit"])

    def test_changed_object_keeps_other_fields_and_sparse_membership(self):
        stored = [
            {"id": "pocket-02", "x": 12.34567, "width": 20, "floor": 4},
            {"id": "pocket-05", "x": 70, "width": 20},
        ]
        initial = [dict(stored[0], x=12.346), stored[1]]
        current = [dict(initial[0], width=42), {"id": "pocket-09", "width": 30}]
        merged = engine._overlay_edited_controls(stored, initial, current)
        self.assertEqual(merged[0], dict(stored[0], width=42))
        self.assertEqual([item["id"] for item in merged], ["pocket-02", "pocket-09"])

    def test_sparse_id_allocation_skips_existing_ids(self):
        controller = engine.CaseInsertDialog.__new__(engine.CaseInsertDialog)
        controller._object_counter = 1
        controller.project_canvas = types.SimpleNamespace(
            objects={"rectangular-pocket-02": {}, "rectangular-pocket-03": {}})
        self.assertEqual(controller._next_object_id("rectangular_pocket"),
                         "rectangular-pocket-04")

    def test_unchanged_legacy_controls_keep_omitted_optional_keys(self):
        controller = engine.CaseInsertDialog.__new__(engine.CaseInsertDialog)
        controller._base_legacy_params = {"insert_type": "Dividers"}
        controller._initial_legacy_controls = {
            "insert_type": "Dividers", "rows": 1, "columns": 1,
        }
        controller._legacy_controls = lambda: dict(controller._initial_legacy_controls)
        self.assertEqual(controller._params(), {"insert_type": "Dividers"})
        controller._legacy_controls = lambda: dict(controller._initial_legacy_controls, rows=2)
        self.assertEqual(controller._params()["rows"], 2)
        self.assertEqual(controller._params()["columns"], 1)

    def test_bound_document_ignores_global_active_document(self):
        first, second = object(), object()
        controller = engine.CaseInsertDialog.__new__(engine.CaseInsertDialog)
        controller._document = first
        controller._document_name = "First"
        controller._source_record = "saved"
        controller._load_error = None
        controller._document_record = lambda document: "saved"
        app = types.SimpleNamespace(ActiveDocument=second,
                                    listDocuments=lambda: {"First": first, "Second": second})
        with patch.object(engine, "App", app):
            self.assertIs(controller._bound_document(), first)
            app.listDocuments = lambda: {"First": second}
            with self.assertRaisesRegex(RuntimeError, "was closed"):
                controller._bound_document()

    def test_external_project_change_is_detected_before_mutation(self):
        doc = object()
        controller = engine.CaseInsertDialog.__new__(engine.CaseInsertDialog)
        controller._document = doc
        controller._document_name = "First"
        controller._source_record = "original"
        controller._load_error = None
        controller._document_record = lambda document: "external edit"
        with patch.object(engine, "App", types.SimpleNamespace(
                listDocuments=lambda: {"First": doc})):
            with self.assertRaisesRegex(RuntimeError, "changed outside"):
                controller._bound_document()


class DerivedLayoutInsetTests(unittest.TestCase):
    def setUp(self):
        self.controller = engine.CaseInsertDialog.__new__(engine.CaseInsertDialog)
        self.controller._base_project = {
            "schema_version": 1,
            "case": {"case_model": "Custom Case", "internal_length": 100.0,
                     "internal_width": 80.0, "insert_depth": 30.0,
                     "corner_radius": 10.0, "side_clearance": 0.0,
                     "bottom_clearance": 2.0, "taper_allowance": 0.0,
                     "layout_inset": 13.8},
            "layers": {"enabled": True, "ratio": 0.5},
            "containment": {"mode": "none", "clearance_mm": 0.3},
            "lid_panel": {"enabled": False},
            "printer": {"bed_x": 256.0, "bed_y": 256.0, "margin": 5.0,
                        "split": False},
            "objects": [{"id": "wide-pocket", "type": "rectangular_pocket",
                         "length": 80.0, "width": 60.0, "height": 5.0}],
        }
        initial = copy.deepcopy(self.controller._base_project)
        initial["case"].pop("layout_inset")
        self.controller._initial_project_controls = initial
        self.controls = copy.deepcopy(initial)
        self.controller._project_controls = lambda: copy.deepcopy(self.controls)
        self.controller._layout_snapshot = None
        self.controller.mode_combo = Mock(currentIndex=lambda: 2)
        self.controller.project_canvas = Mock()

    def test_disabling_layers_reclaims_canvas_and_planner_space(self):
        with patch.object(engine, "_case_layout_inset", return_value=3.0):
            before = self.controller._project_spec()
            self.assertEqual(layout_project(before, "balanced").placed_count, 0)
            self.controls["layers"]["enabled"] = False
            after = self.controller._project_spec()
        self.assertEqual(after["case"]["layout_inset"], 3.0)
        self.controller.project_canvas.set_case.assert_called_with(100.0, 80.0, 3.0)
        self.assertEqual(layout_project(after, "balanced").placed_count, 1)
        self.assertEqual(self.controller._base_project["case"]["layout_inset"], 13.8)

    def test_lower_alignment_clearance_reduces_stored_inset(self):
        self.controls["containment"]["clearance_mm"] = 0.2
        with patch.object(engine, "_case_layout_inset", return_value=3.0):
            spec = self.controller._project_spec()
        self.assertEqual(spec["case"]["layout_inset"], 13.7)
        self.controller.project_canvas.set_case.assert_called_with(100.0, 80.0, 13.7)

    def test_smaller_corner_geometry_reduces_stored_inset(self):
        self.controls["case"]["corner_radius"] = 5.0
        with patch.object(engine, "_case_layout_inset", return_value=1.5) as contour:
            spec = self.controller._project_spec()
        self.assertEqual(contour.call_args.args[0]["corner_radius"], 5.0)
        self.assertEqual(spec["case"]["layout_inset"], 12.3)
        self.controller.project_canvas.set_case.assert_called_with(100.0, 80.0, 12.3)

    def test_larger_geometry_still_increases_the_required_inset(self):
        self.controls["case"]["corner_radius"] = 20.0
        with patch.object(engine, "_case_layout_inset", return_value=6.0):
            spec = self.controller._project_spec()
        self.assertEqual(spec["case"]["layout_inset"], 16.8)
        self.controller.project_canvas.set_case.assert_called_with(100.0, 80.0, 16.8)


if __name__ == "__main__":
    unittest.main()
