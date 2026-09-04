# SPDX-License-Identifier: LGPL-2.1-or-later
"""Focused pure tests for generated-part export selection."""

from __future__ import annotations

import importlib
import sys
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


if __name__ == "__main__":
    unittest.main()
