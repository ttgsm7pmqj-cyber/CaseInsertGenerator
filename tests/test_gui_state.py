# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure regressions for preserving data behind the GUI's visible controls."""

import importlib
import sys
import types
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
