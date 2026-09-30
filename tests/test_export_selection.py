# SPDX-License-Identifier: LGPL-2.1-or-later
"""Focused pure tests for catalog validation and generated-part export selection."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
def load_selection_api():
    previous = {name: sys.modules.get(name) for name in ("FreeCAD", "Part")}
    sys.modules["FreeCAD"] = types.ModuleType("FreeCAD")
    sys.modules["Part"] = types.ModuleType("Part")
    try:
        module = importlib.import_module("freecad.CaseInsertGenerator.engine")
        return vars(module)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class FakeShape:
    def isNull(self):
        return False


class FakeObject:
    def __init__(self, name):
        self.Name = name
        self.Label = name.replace("_", " ")
        self.Shape = FakeShape()


class FakeParameters:
    GeneratedResults = ["LowerCarrier", "UpperCarrier", "SharedPanel"]
    GeneratedResult = "LowerCarrier"


class FakeDocument:
    def __init__(self):
        self.objects = {
            "CaseInsertGeneratorParameters": FakeParameters(),
            **{
                name: FakeObject(name)
                for name in FakeParameters.GeneratedResults
            },
        }

    def getObject(self, name):
        return self.objects.get(name)


class ExportSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_selection_api()

    def test_default_selection_is_every_generated_part(self):
        resolve = self.api["_resolve_export_names"]
        self.assertEqual(
            resolve(["LowerCarrier", "UpperCarrier", "SharedPanel"]),
            ["LowerCarrier", "UpperCarrier", "SharedPanel"],
        )

    def test_bundled_catalog_contains_only_synthetic_examples(self):
        catalog = self.api["load_case_catalog"]()
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(len(catalog["presets"]), 3)
        self.assertEqual(
            {item["verification"]["level"] for item in catalog["presets"]},
            {"synthetic"},
        )
        self.assertTrue(all(
            not item["verification"]["physical_fit"]
            for item in catalog["presets"]
        ))

    def test_explicit_selection_uses_stable_generation_order(self):
        resolve = self.api["_resolve_export_names"]
        self.assertEqual(
            resolve(
                ["LowerCarrier", "UpperCarrier", "SharedPanel"],
                ["SharedPanel", "LowerCarrier", "SharedPanel"],
            ),
            ["LowerCarrier", "SharedPanel"],
        )
        self.assertEqual(
            resolve(["LowerCarrier", "UpperCarrier"], "UpperCarrier"),
            ["UpperCarrier"],
        )

    def test_empty_and_stale_selections_are_actionable(self):
        resolve = self.api["_resolve_export_names"]
        with self.assertRaisesRegex(ValueError, "at least one generated part"):
            resolve(["LowerCarrier"], [])
        with self.assertRaisesRegex(ValueError, "no longer available"):
            resolve(["LowerCarrier"], ["OldPart"])

    def test_active_results_filters_to_the_chosen_part(self):
        active_results = self.api["active_results"]
        document = FakeDocument()
        self.assertEqual(
            [obj.Name for obj in active_results(document)],
            ["LowerCarrier", "UpperCarrier", "SharedPanel"],
        )
        self.assertEqual(
            [obj.Name for obj in active_results(
                document, selected_names=["UpperCarrier"])],
            ["UpperCarrier"],
        )


class CaseCatalogValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_selection_api()

    def setUp(self):
        catalog = self.api["load_case_catalog"]()
        self.catalog = {"schema_version": 1, "presets": catalog["presets"]}
        self.preset = self.catalog["presets"][0]

    def load_custom_catalog(self, field=None, value=None):
        if field is not None:
            self.preset["geometry"][field] = value
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(self.catalog), encoding="utf-8")
            return self.api["load_case_catalog"](path)

    def assert_invalid_dimension(self, field, value, reason):
        with self.assertRaises(ValueError) as caught:
            self.load_custom_catalog(field, value)
        self.assertEqual(
            str(caught.exception),
            "%s must be %s for %s" % (
                field.replace("_", " "), reason, self.preset["display_name"]),
        )

    def test_nan_numeric_literal_is_rejected(self):
        self.assert_invalid_dimension("internal_length", float("nan"), "a finite number")

    def test_infinity_numeric_literal_is_rejected(self):
        self.assert_invalid_dimension("internal_width", float("inf"), "a finite number")

    def test_negative_corner_radius_is_rejected(self):
        self.assert_invalid_dimension("bottom_corner_radius", -10, "non-negative")

    def test_boolean_depth_is_rejected(self):
        self.assert_invalid_dimension("internal_depth", True, "a finite number")

    def test_nan_string_is_rejected(self):
        self.assert_invalid_dimension("internal_length", "NaN", "a finite number")

    def test_infinity_string_is_rejected(self):
        self.assert_invalid_dimension("internal_width", "Infinity", "a finite number")

    def test_all_required_geometry_fields_require_finite_numbers(self):
        geometry = self.preset["geometry"]
        for field, original in list(geometry.items()):
            for value in (None, False, "12.5", [], {}, float("-inf"), 10 ** 400):
                with self.subTest(field=field, value=value):
                    self.assert_invalid_dimension(field, value, "a finite number")
            geometry[field] = original

    def test_dimension_ranges_are_enforced(self):
        geometry = self.preset["geometry"]
        for field in ("internal_length", "internal_width", "internal_depth", "bottom_depth"):
            original = geometry[field]
            for value in (0, -1):
                with self.subTest(field=field, value=value):
                    self.assert_invalid_dimension(field, value, "positive")
            geometry[field] = original
        for field in ("bottom_corner_radius", "floor_fillet_radius", "profile_reference_height"):
            original = geometry[field]
            with self.subTest(field=field):
                self.assert_invalid_dimension(field, -1, "non-negative")
            geometry[field] = original

    def test_valid_integer_dimensions_and_zero_radii_are_preserved(self):
        geometry = self.preset["geometry"]
        geometry.update({
            "internal_length": 180,
            "internal_width": 120,
            "internal_depth": 45,
            "bottom_depth": 40,
            "bottom_corner_radius": 0,
            "floor_fillet_radius": 0,
            "draft_angle_degrees": 0,
            "profile_reference_height": 0,
        })
        loaded = self.load_custom_catalog()
        self.assertEqual(loaded["presets"], self.catalog["presets"])
        model = loaded["models"][self.preset["display_name"]]
        self.assertEqual({field: model[field] for field in geometry}, geometry)


if __name__ == "__main__":
    unittest.main()
