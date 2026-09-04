# SPDX-License-Identifier: LGPL-2.1-or-later

import copy
import unittest
from collections.abc import Mapping

from freecad.CaseInsertGenerator.project_model import (
    CONTAINMENT_MODES,
    GenerationResult,
    LAYOUT_STRATEGIES,
    LID_EVIDENCE_STATES,
    LID_HINGE_EDGES,
    LID_PANEL_ORIENTATIONS,
    LID_PANEL_PATTERNS,
    LayoutResult,
    OBJECT_TYPES,
    ProjectValidationError,
    default_lid_panel,
    generate_layouts,
    lid_panel_height_budget,
    lid_panel_plan,
    layout_project,
    validate_project,
)


def project_spec(*, objects=None, layers=None, lid=None, containment=None):
    return {
        "schema_version": 1,
        "case": {
            "case_model": "Test case",
            "internal_length": 100.0,
            "internal_width": 80.0,
            "insert_depth": 30.0,
            "corner_radius": 5.0,
            "side_clearance": 0.0,
            "bottom_clearance": 2.0,
            "taper_allowance": 0.0,
        },
        "lid": lid or {"source": "unknown", "clearance_mm": None},
        "layers": layers or {"enabled": False, "ratio": 0.5},
        "containment": containment
        or {"mode": "none", "clearance_mm": 0.4, "panel_thickness_mm": 2.0},
        "printer": {"bed_x": 256.0, "bed_y": 256.0, "margin": 5.0, "split": True},
        "objects": objects or [],
    }


def project_object(
    object_id,
    object_type="rectangular_pocket",
    *,
    width=20.0,
    depth=15.0,
    height=10.0,
    x=0.0,
    y=0.0,
    rotation=0.0,
    layer="lower",
    locked=False,
    priority=0,
    options=None,
):
    return {
        "id": object_id,
        "type": object_type,
        "name": object_id.replace("-", " ").title(),
        "x": x,
        "y": y,
        "rotation": rotation,
        "layer": layer,
        "locked": locked,
        "priority": priority,
        "width": width,
        "length": depth,
        "height": height,
        **(options or {}),
    }


def lid_panel_spec(
    *,
    envelope_source="measured",
    clearance_source="measured",
    clearance=18.0,
):
    source = project_spec(
        lid={
            "source": clearance_source,
            "clearance_mm": (clearance if clearance_source != "unknown" else None),
            "envelope_source": envelope_source,
            "length_mm": 180.0,
            "width_mm": 120.0,
        }
    )
    source["lid_panel"] = default_lid_panel()
    source["lid_panel"]["enabled"] = True
    return source


class ProjectSchemaTests(unittest.TestCase):
    def test_schema_constants_define_the_version_one_contract(self):
        self.assertEqual(
            OBJECT_TYPES,
            (
                "svg_pocket",
                "circular_pocket",
                "rectangular_pocket",
                "removable_bin",
                "existing_container_bay",
                "divider_region",
            ),
        )
        self.assertEqual(
            LID_EVIDENCE_STATES, ("measured", "cad-derived", "unknown")
        )
        self.assertEqual(
            LID_PANEL_PATTERNS, ("solid", "slot_grid", "perforated_grid")
        )
        self.assertEqual(LID_PANEL_ORIENTATIONS, ("horizontal", "vertical"))
        self.assertEqual(LID_HINGE_EDGES, ("top", "bottom", "left", "right"))
        self.assertEqual(
            CONTAINMENT_MODES, ("none", "shared_panel", "individual_lids")
        )
        self.assertEqual(
            LAYOUT_STRATEGIES,
            ("balanced", "maximum_capacity", "fewest_layers"),
        )

    def test_validate_project_returns_complete_stable_object_fields(self):
        source = project_spec(
            objects=[project_object("round", "circular_pocket", options={"diameter": 20})]
        )

        normalized = validate_project(source)

        self.assertEqual(source, project_spec(objects=[project_object("round", "circular_pocket", options={"diameter": 20})]))
        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(
            tuple(
                key
                for key in normalized["objects"][0]
                if key not in {"diameter", "priority"}
            ),
            (
                "id",
                "type",
                "name",
                "x",
                "y",
                "rotation",
                "layer",
                "locked",
                "width",
                "length",
                "height",
            ),
        )
        self.assertEqual(normalized["objects"][0]["diameter"], 20)
        self.assertIsNot(normalized, source)

    def test_all_six_object_types_are_valid(self):
        objects = [project_object(kind, kind) for kind in OBJECT_TYPES]

        normalized = validate_project(project_spec(objects=objects))

        self.assertEqual([item["type"] for item in normalized["objects"]], list(OBJECT_TYPES))

    def test_schema_version_is_exactly_one(self):
        source = project_spec()
        source["schema_version"] = 2

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        self.assertEqual(caught.exception.issues[0].path, "schema_version")
        self.assertEqual(caught.exception.issues[0].code, "unsupported_schema_version")

    def test_rejects_a_third_layer_and_reports_the_object_field(self):
        source = project_spec(
            objects=[project_object("third-layer", layer="middle")],
            layers={"enabled": True, "ratio": 0.5},
        )

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        self.assertTrue(
            any(
                issue.path == "objects[0].layer"
                and issue.code == "unsupported_object_layer"
                for issue in caught.exception.issues
            )
        )

    def test_known_lid_evidence_requires_a_numeric_clearance(self):
        source = project_spec(lid={"source": "measured", "clearance_mm": None})

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        self.assertTrue(
            any(
                issue.path == "lid.clearance_mm"
                and issue.code == "required_for_known_lid"
                for issue in caught.exception.issues
            )
        )

    def test_unknown_lid_evidence_cannot_claim_clearance(self):
        source = project_spec(lid={"source": "unknown", "clearance_mm": 12.0})

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        self.assertTrue(
            any(
                issue.path == "lid.clearance_mm"
                and issue.code == "clearance_requires_evidence"
                for issue in caught.exception.issues
            )
        )

    def test_duplicate_object_ids_and_invalid_containment_are_actionable(self):
        source = project_spec(
            objects=[project_object("same"), project_object("same")],
            containment={
                "mode": "elastic_cloud",
                "clearance_mm": 0.4,
                "panel_thickness_mm": 2.0,
            },
        )

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        issue_pairs = {(issue.path, issue.code) for issue in caught.exception.issues}
        self.assertIn(("objects[1].id", "duplicate_object_id"), issue_pairs)
        self.assertIn(("containment.mode", "invalid_containment_mode"), issue_pairs)

    def test_layers_default_to_a_2_4_mm_carrier_floor(self):
        normalized = validate_project(project_spec())

        self.assertEqual(normalized["layers"]["floor_mm"], 2.4)

    def test_preset_case_does_not_require_redundant_insert_depth(self):
        source = project_spec()
        source["case"]["case_model"] = "Small rounded envelope (synthetic)"
        source["case"].pop("insert_depth")

        normalized = validate_project(source)

        self.assertIsNone(normalized["case"]["insert_depth"])

    def test_custom_case_still_requires_insert_depth(self):
        source = project_spec()
        source["case"]["case_model"] = "Custom Case"
        source["case"].pop("insert_depth")

        with self.assertRaises(ProjectValidationError) as caught:
            validate_project(source)

        self.assertIn(
            ("case.insert_depth", "required_for_custom_case"),
            {(issue.path, issue.code) for issue in caught.exception.issues},
        )

    def test_case_evidence_fields_survive_normalization_and_are_copied(self):
        source = project_spec()
        source["case"].update(
            {
                "preset_id": "synthetic-small-rounded",
                "evidence": {
                    "status": "synthetic / physical-fit unverified",
                    "source": "original-demonstration",
                    "physical_fit": False,
                },
            }
        )

        normalized = validate_project(source)
        source["case"]["evidence"]["status"] = "mutated"

        self.assertEqual(normalized["case"]["preset_id"], "synthetic-small-rounded")
        self.assertEqual(
            normalized["case"]["evidence"],
            {
                "status": "synthetic / physical-fit unverified",
                "source": "original-demonstration",
                "physical_fit": False,
            },
        )

    def test_schema_v1_result_metadata_survives_normalization(self):
        source = project_spec()
        source.update(
            {
                "parts": 2,
                "warnings": ["Physical fit remains unverified."],
                "unplaced": [
                    {
                        "object_id": "large-bin",
                        "code": "insufficient_height",
                        "reason": "Object height 40.0 mm exceeds 25.6 mm available.",
                    }
                ],
                "layout_strategy": "balanced",
            }
        )

        normalized = validate_project(source)

        self.assertEqual(normalized["parts"], 2)
        self.assertEqual(normalized["warnings"], source["warnings"])
        self.assertEqual(normalized["unplaced"], source["unplaced"])
        self.assertEqual(normalized["layout_strategy"], "balanced")
        source["unplaced"][0]["reason"] = "mutated"
        self.assertIn("40.0 mm", normalized["unplaced"][0]["reason"])


class LidPanelSchemaTests(unittest.TestCase):
    def test_unknown_clearance_preserves_configuration_but_blocks_printing(self):
        source = lid_panel_spec(clearance_source="unknown", clearance=None)
        source["lid_panel"]["pattern"] = "slot_grid"
        source["lid_panel"]["slot_grid"].update(
            {"slot_length_mm": 28.0, "slot_width_mm": 5.0}
        )

        normalized = validate_project(source)
        gate = lid_panel_height_budget(normalized)

        self.assertEqual(normalized["lid_panel"]["pattern"], "slot_grid")
        self.assertEqual(normalized["lid_panel"]["slot_grid"]["slot_length_mm"], 28.0)
        self.assertFalse(gate["printable"])
        self.assertIsNone(gate["available_clearance_mm"])
        self.assertTrue(any("configuration and preview" in item for item in gate["reasons"]))

    def test_known_envelope_and_clearance_pass_conservative_height_budget(self):
        source = lid_panel_spec(clearance=18.0)
        source["lid_panel"]["thickness_mm"] = 3.0
        source["lid_panel"]["payload_thickness_mm"] = 8.0
        source["lid_panel"]["mounting"]["retainer_projection_mm"] = 4.0

        gate = lid_panel_height_budget(source)

        self.assertTrue(gate["printable"])
        self.assertEqual(gate["required_height_mm"], 11.0)
        self.assertEqual(gate["remaining_clearance_mm"], 7.0)

    def test_payload_or_retainer_over_budget_blocks_printing(self):
        source = lid_panel_spec(clearance=9.0)
        source["lid_panel"]["thickness_mm"] = 3.0
        source["lid_panel"]["payload_thickness_mm"] = 8.0

        gate = lid_panel_height_budget(source)

        self.assertFalse(gate["printable"])
        self.assertEqual(gate["required_height_mm"], 11.0)
        self.assertEqual(gate["remaining_clearance_mm"], -2.0)
        self.assertTrue(any("exceeds" in item for item in gate["reasons"]))

    def test_unknown_envelope_evidence_blocks_even_with_configured_dimensions(self):
        gate = lid_panel_height_budget(
            lid_panel_spec(envelope_source="unknown", clearance=18.0)
        )

        self.assertFalse(gate["printable"])
        self.assertTrue(any("envelope evidence is Unknown" in item for item in gate["reasons"]))

    def test_every_nested_panel_setting_round_trips_in_schema_v1(self):
        source = lid_panel_spec()
        panel = source["lid_panel"]
        panel["pattern"] = "perforated_grid"
        panel["perforated_grid"].update(
            {"diameter_mm": 6.0, "pitch_x_mm": 14.0, "pitch_y_mm": 15.0}
        )
        panel["keepouts"]["rectangles"] = [{
            "label": "Synthetic lid rib",
            "x_mm": 55.0,
            "y_mm": 25.0,
            "length_mm": 24.0,
            "width_mm": 12.0,
        }]
        panel["mounting"].update({
            "fastener_holes_enabled": True,
            "custom_fastener_holes": [{"x_mm": 18.0, "y_mm": 18.0}],
        })
        panel["splitting"].update({"key_size_mm": 9.0, "key_clearance_mm": 0.3})
        expected_panel = copy.deepcopy(panel)

        normalized = validate_project(source)
        source["lid_panel"]["keepouts"]["rectangles"][0]["label"] = "mutated"
        plan = lid_panel_plan(normalized)

        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["lid_panel"], expected_panel)
        self.assertEqual(normalized["lid_panel"]["pattern"], "perforated_grid")
        self.assertEqual(
            normalized["lid_panel"]["keepouts"]["rectangles"][0]["label"],
            "Synthetic lid rib",
        )
        self.assertEqual(normalized["lid_panel"]["splitting"]["key_size_mm"], 9.0)
        self.assertEqual(plan["length_mm"], 160.0)
        self.assertEqual(plan["width_mm"], 88.0)


class GeometryGenerationResultTests(unittest.TestCase):
    def test_geometry_report_round_trips_and_supports_mapping_access(self):
        report = {
            "document": "CaseInsert",
            "results": ["LowerCarrier", "SharedPanel"],
            "parts": 2,
            "mode": "Project Composer",
            "valid": True,
            "solids": 2,
            "volume": 1234.5,
            "warnings": ["Physical fit remains unverified."],
            "unplaced": [
                {
                    "object_id": "large",
                    "code": "insufficient_height",
                    "reason": "Object height exceeds the available clear height.",
                }
            ],
            "project": {"schema_version": 1},
        }

        result = GenerationResult.from_mapping(report)

        self.assertIsInstance(result, Mapping)
        self.assertEqual(result["document"], "CaseInsert")
        self.assertEqual(result.get("parts"), 2)
        self.assertEqual(result.to_mapping(), report)

    def test_geometry_report_defensively_copies_input_and_output(self):
        report = {
            "document": "CaseInsert",
            "results": ["Carrier"],
            "parts": 1,
            "mode": "Project Composer",
            "valid": True,
            "solids": 1,
            "volume": 50.0,
            "warnings": [],
            "unplaced": [],
            "project": {"schema_version": 1},
            "verification_note": {"source": "headless"},
        }

        result = GenerationResult.from_mapping(report)
        report["project"]["schema_version"] = 99
        exported = result.to_mapping()
        exported["project"]["schema_version"] = 42

        self.assertEqual(result["project"]["schema_version"], 1)
        self.assertEqual(result["verification_note"], {"source": "headless"})


class DeterministicLayoutTests(unittest.TestCase):
    def test_returns_exactly_three_layouts_in_stable_order(self):
        source = project_spec(
            objects=[project_object("a"), project_object("b", width=25, depth=20)]
        )

        results = generate_layouts(source)

        self.assertEqual(tuple(result.strategy for result in results), LAYOUT_STRATEGIES)
        self.assertTrue(all(result.placed_count == 2 for result in results))
        self.assertTrue(all(result.unplaced_count == 0 for result in results))

    def test_layouts_are_repeatable_and_do_not_mutate_the_project(self):
        source = project_spec(
            objects=[
                project_object("c", width=30, depth=20),
                project_object("a", width=15, depth=15, priority=2),
                project_object("b", width=20, depth=10),
            ]
        )
        before = copy.deepcopy(source)

        first = tuple(result.to_dict() for result in generate_layouts(source))
        second = tuple(result.to_dict() for result in generate_layouts(source))

        self.assertEqual(first, second)
        self.assertEqual(source, before)

    def test_locked_objects_keep_position_rotation_and_layer(self):
        layers = {"enabled": True, "ratio": 0.5}
        locked = project_object(
            "locked",
            "removable_bin",
            width=20,
            depth=10,
            height=8,
            x=61,
            y=43,
            rotation=90,
            layer="upper",
            locked=True,
        )
        source = project_spec(objects=[locked, project_object("free")], layers=layers)

        for result in generate_layouts(source):
            placement = next(item for item in result.placements if item.object_id == "locked")
            self.assertEqual(
                (placement.x, placement.y, placement.rotation, placement.layer),
                (61.0, 43.0, 90.0, "upper"),
            )
            self.assertTrue(placement.locked)

    def test_fewest_layers_fills_layer_zero_before_layer_one(self):
        layers = {"enabled": True, "ratio": 0.5}
        source = project_spec(
            objects=[project_object("preferred-upper", layer="upper", height=5)],
            layers=layers,
        )

        result = next(
            item for item in generate_layouts(source) if item.strategy == "fewest_layers"
        )

        self.assertEqual(result.placements[0].layer, "lower")

    def test_layout_project_returns_only_the_requested_strategy(self):
        source = project_spec(objects=[project_object("one")])

        result = layout_project(source, "maximum_capacity")

        self.assertIsInstance(result, LayoutResult)
        self.assertNotIsInstance(result, GenerationResult)
        self.assertEqual(result.strategy, "maximum_capacity")
        self.assertEqual(result.placed_count, 1)

    def test_reports_insufficient_height_with_available_height(self):
        source = project_spec(objects=[project_object("too-tall", height=40)])

        result = generate_layouts(source)[0]

        self.assertEqual(result.unplaced_count, 1)
        self.assertEqual(result.unplaced[0].code, "insufficient_height")
        self.assertIn("40.0 mm", result.unplaced[0].reason)
        self.assertIn("25.6 mm", result.unplaced[0].reason)

    def test_known_lid_clearance_does_not_increase_printed_carrier_height(self):
        source = project_spec(
            objects=[project_object("too-tall", height=35)],
            lid={"source": "measured", "clearance_mm": 20.0},
        )

        result = generate_layouts(source)[0]

        self.assertEqual(result.unplaced[0].code, "insufficient_height")
        self.assertIn("25.6 mm", result.unplaced[0].reason)

    def test_two_layer_clear_heights_remove_one_floor_per_carrier(self):
        source = project_spec(layers={"enabled": True, "ratio": 0.25})

        result = layout_project(source, "balanced")

        self.assertEqual(dict(result.layer_heights), {"lower": 5.8, "upper": 17.4})

    def test_shared_panel_and_carrier_floor_are_both_reserved(self):
        source = project_spec(
            containment={
                "mode": "shared_panel",
                "clearance_mm": 0.4,
                "panel_thickness_mm": 2.0,
            }
        )

        result = layout_project(source, "balanced")

        self.assertEqual(dict(result.layer_heights), {"lower": 23.2})

    def test_reports_locked_out_of_bounds_without_moving_the_object(self):
        source = project_spec(
            objects=[
                project_object(
                    "outside", width=30, depth=20, x=90, y=70, locked=True
                )
            ]
        )

        for result in generate_layouts(source):
            self.assertEqual(result.placed_count, 0)
            self.assertEqual(result.unplaced[0].code, "locked_out_of_bounds")
            self.assertIn("locked position", result.unplaced[0].reason)

    def test_conservative_layout_inset_keeps_objects_out_of_case_corners(self):
        source = project_spec(objects=[project_object("corner")])
        source["case"]["layout_inset"] = 8.0

        result = layout_project(source, "fewest_layers")

        self.assertEqual(result.placed_count, 1)
        self.assertGreaterEqual(result.placements[0].x, 8.0)
        self.assertGreaterEqual(result.placements[0].y, 8.0)

        source["objects"][0].update({"x": 0.0, "y": 0.0, "locked": True})
        locked = layout_project(source, "balanced")
        self.assertEqual(locked.unplaced[0].code, "locked_out_of_bounds")

    def test_reports_collision_for_overlapping_locked_objects(self):
        source = project_spec(
            objects=[
                project_object("first", x=10, y=10, locked=True),
                project_object("second", x=15, y=10, locked=True),
            ]
        )

        result = generate_layouts(source)[0]

        self.assertEqual(result.placed_count, 1)
        self.assertEqual(result.unplaced[0].object_id, "second")
        self.assertEqual(result.unplaced[0].code, "locked_collision")
        self.assertIn("first", result.unplaced[0].reason)

    def test_reports_footprint_and_packing_failures_separately(self):
        source = project_spec(
            objects=[
                project_object("full", width=80, depth=100, locked=True),
                project_object("too-wide", width=101, depth=10),
                project_object("blocked", width=10, depth=10),
            ]
        )

        result = generate_layouts(source)[0]
        reasons = {item.object_id: item for item in result.unplaced}

        self.assertEqual(reasons["too-wide"].code, "footprint_exceeds_case")
        self.assertEqual(reasons["blocked"].code, "insufficient_area_or_collision")

    def test_none_containment_warns_for_uncovered_bins(self):
        source = project_spec(
            objects=[project_object("loose", "removable_bin")],
            containment={
                "mode": "none",
                "clearance_mm": 0.4,
                "panel_thickness_mm": 2.0,
            },
        )

        result = generate_layouts(source)[0]

        self.assertTrue(any("containment" in warning.lower() for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
