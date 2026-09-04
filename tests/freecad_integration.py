#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD integration contracts for Case Insert Generator.

Run this file inside FreeCAD, not CPython.  It uses only generated custom-case
geometry and temporary SVG/FCStd files; no third-party CAD or network access is
required.  Every check is isolated and the final stdout line is one JSON
object, which makes the same runner usable through FreeCAD MCP or FreeCADCmd::

    import runpy
    runpy.run_path("/path/to/tests/freecad_integration.py", run_name="__main__")

The runner deliberately records all failures instead of stopping at the first
one so a production-review turn gets one complete integration report.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import importlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import traceback

import FreeCAD as App


ROOT = Path(__file__).resolve().parents[1]
MACRO_PATH = ROOT / "CaseInsertGenerator.FCMacro"
ENGINE_MODULE = "freecad.CaseInsertGenerator.engine"
ENGINE_PATH = ROOT / "freecad" / "CaseInsertGenerator" / "engine.py"
EPSILON_VOLUME = 1.0e-5
EPSILON_LENGTH = 1.0e-5


class ContractFailure(AssertionError):
    """A stable integration contract was not satisfied."""


class ContractSkip(RuntimeError):
    """A check was skipped to avoid touching pre-existing user state."""


def _require(condition, message):
    if not condition:
        raise ContractFailure(message)


def _result_mapping(result):
    if isinstance(result, Mapping):
        return dict(result)
    for method_name in ("to_mapping", "to_dict"):
        method = getattr(result, method_name, None)
        if callable(method):
            value = method()
            if isinstance(value, Mapping):
                return dict(value)
    raise ContractFailure(
        "GenerationResult must expose a JSON-ready mapping via to_mapping() or to_dict()"
    )


def _unique_document(label):
    base = "CIGIntegration_%s" % label
    names = App.listDocuments()
    if base not in names:
        return App.newDocument(base)
    index = 2
    while "%s_%02d" % (base, index) in names:
        index += 1
    return App.newDocument("%s_%02d" % (base, index))


def _close_document(doc):
    if doc is None:
        return
    try:
        name = doc.Name
    except Exception:
        return
    if name in App.listDocuments():
        App.closeDocument(name)


def _base_spec(
    objects=None,
    *,
    length=180.0,
    width=140.0,
    depth=34.0,
    radius=10.0,
    layers=False,
    containment="none",
    bed_x=256.0,
    bed_y=256.0,
    bed_margin=5.0,
    split=False,
):
    return {
        "schema_version": 1,
        "case": {
            "case_model": "Custom Case",
            "internal_length": float(length),
            "internal_width": float(width),
            "insert_depth": float(depth),
            "corner_radius": float(radius),
            "side_clearance": 0.0,
            "bottom_clearance": 1.0,
            "taper_allowance": 0.0,
        },
        "lid": {"source": "unknown", "clearance_mm": None},
        "layers": {"enabled": bool(layers), "ratio": 0.5},
        "containment": {
            "mode": containment,
            "clearance_mm": 0.4,
            "panel_thickness_mm": 2.0,
        },
        "printer": {
            "bed_x": float(bed_x),
            "bed_y": float(bed_y),
            "margin": float(bed_margin),
            "split": bool(split),
        },
        "objects": copy.deepcopy(objects or []),
    }


def _lid_panel_spec(
    pattern="solid",
    *,
    length=220.0,
    width=150.0,
    clearance=26.0,
    split=False,
    bed_x=256.0,
    bed_y=256.0,
):
    from freecad.CaseInsertGenerator.project_model import default_lid_panel

    spec = _base_spec(
        length=180.0,
        width=120.0,
        depth=30.0,
        split=split,
        bed_x=bed_x,
        bed_y=bed_y,
    )
    spec["lid"] = {
        "source": "measured" if clearance is not None else "unknown",
        "clearance_mm": clearance,
        "envelope_source": "cad-derived",
        "length_mm": float(length),
        "width_mm": float(width),
    }
    spec["lid_panel"] = default_lid_panel()
    spec["lid_panel"].update({
        "enabled": True,
        "pattern": pattern,
        "payload_thickness_mm": 8.0,
    })
    spec["verification"] = {
        "geometry_provenance": "synthetic-demonstration",
        "physical_fit": False,
    }
    return spec


def _object(
    object_id,
    object_type,
    x,
    y,
    width,
    length,
    height=10.0,
    *,
    rotation=0.0,
    layer="lower",
    locked=True,
    extra=None,
):
    value = {
        "id": object_id,
        "type": object_type,
        "name": object_id.replace("-", " ").title(),
        "x": float(x),
        "y": float(y),
        "rotation": float(rotation),
        "layer": layer,
        "locked": bool(locked),
        "width": float(width),
        "length": float(length),
        "height": float(height),
    }
    if extra:
        value.update(extra)
    return value


def _write_svg(path, fill_rule):
    # Both subpaths run clockwise.  evenodd therefore cuts a hole; nonzero
    # keeps the inner region filled because its winding number is two.
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" viewBox="0 0 40 40">
  <path fill="#000" fill-rule="%s" stroke="none"
        d="M0 0 H40 V40 H0 Z M10 10 H30 V30 H10 Z"/>
</svg>
""" % fill_rule,
        encoding="utf-8",
    )


class IntegrationContracts:
    def __init__(self, api_namespace, temporary_directory):
        self.api = api_namespace
        self.temp = Path(temporary_directory)

    def generate_project_returns_generation_result(self):
        doc = _unique_document("ReturnType")
        try:
            result = self.api["generate_project"](_base_spec(), document=doc)
            _require(
                result.__class__.__name__ == "GenerationResult",
                "generate_project() returned %s instead of GenerationResult"
                % result.__class__.__name__,
            )
            payload = _result_mapping(result)
            _require(payload.get("valid") is True, "GenerationResult did not report valid geometry")
            _require(payload.get("document") == doc.Name, "GenerationResult names the wrong document")
            return {"type": result.__class__.__name__, "parts": payload.get("parts")}
        finally:
            _close_document(doc)

    def preset_project_resolves_catalog_depth(self):
        doc = _unique_document("PresetDepth")
        try:
            spec = _base_spec([
                _object("preset-pocket", "rectangular_pocket", 30, 35,
                        30, 24, 8)
            ])
            spec["case"]["case_model"] = "Small rounded envelope (synthetic)"
            del spec["case"]["insert_depth"]
            spec["layout_strategy"] = "balanced"
            result = _result_mapping(
                self.api["generate_project"](spec, document=doc))
            resolved_depth = result["project"]["case"].get("insert_depth")
            _require(isinstance(resolved_depth, (int, float)) and
                     resolved_depth > 0.0,
                     "preset project did not persist its catalog depth")
            _require(result["project"].get("layout_strategy") == "balanced",
                     "preset project did not complete the requested layout")
            return {
                "case_model": result["project"]["case"]["case_model"],
                "resolved_depth_mm": resolved_depth,
                "layout_strategy": result["project"]["layout_strategy"],
            }
        finally:
            _close_document(doc)

    def all_six_object_types_generate_valid_geometry(self):
        svg_path = self.temp / "all-types.svg"
        _write_svg(svg_path, "evenodd")
        objects = [
            _object(
                "round-01", "circular_pocket", 20, 20, 18, 18,
                extra={"diameter": 18.0},
            ),
            _object("rect-01", "rectangular_pocket", 55, 20, 20, 30),
            _object("bay-01", "existing_container_bay", 100, 20, 20, 30),
            _object("bin-01", "removable_bin", 145, 20, 25, 30),
            _object(
                "divider-01", "divider_region", 20, 65, 35, 55,
                extra={"rows": 2, "columns": 2, "wall": 1.6},
            ),
            _object(
                "svg-01", "svg_pocket", 100, 65, 40, 40,
                extra={"svg_path": str(svg_path), "scale": 1.0, "clearance": 0.0},
            ),
        ]
        doc = _unique_document("AllTypes")
        try:
            result = self.api["generate_project"](
                _base_spec(objects, length=220.0, width=150.0), document=doc
            )
            payload = _result_mapping(result)
            generated = self.api["active_results"](doc)
            _require(payload.get("valid") is True, "six-object project was not reported valid")
            _require(
                all(not item.Shape.isNull() and item.Shape.isValid() and item.Shape.Volume > 0
                    for item in generated),
                "at least one generated print object is null, invalid, or empty",
            )
            project_types = {
                item["type"] for item in payload.get("project", {}).get("objects", [])
            }
            expected_types = {
                "svg_pocket",
                "circular_pocket",
                "rectangular_pocket",
                "removable_bin",
                "existing_container_bay",
                "divider_region",
            }
            _require(project_types == expected_types, "saved project does not retain all six object types")
            _require(doc.getObject("Bin_bin_01") is not None, "removable bin has no stable print-object name")
            return {"types": sorted(project_types), "print_objects": len(generated)}
        finally:
            _close_document(doc)

    def rounded_contour_never_accepts_outside_bin_geometry(self):
        corner_bin = _object("corner-bin", "removable_bin", 0, 0, 20, 20)
        spec = _base_spec(
            [corner_bin], length=100.0, width=80.0, depth=30.0, radius=20.0
        )
        doc = _unique_document("RoundedContainment")
        try:
            try:
                result = self.api["generate_project"](spec, document=doc)
            except Exception as exc:
                message = str(exc).lower()
                _require(
                    "contour" in message or "outside" in message or "boundary" in message,
                    "rounded-corner rejection was not actionable: %s" % exc,
                )
                return {"behavior": "rejected", "message": str(exc)}

            payload = _result_mapping(result)
            bin_object = doc.getObject("Bin_corner_bin")
            if bin_object is None:
                unplaced = payload.get("unplaced", [])
                _require(bool(unplaced), "corner bin disappeared without an unplaced reason")
                return {"behavior": "unplaced", "unplaced": unplaced}

            normalized = self.api["_project_module"]().validate_project(spec)
            case_params = self.api["_project_case_params"](normalized)
            usable_case = self.api["_case_blank"](case_params)
            inside_volume = usable_case.common(bin_object.Shape).Volume
            outside_volume = max(0.0, bin_object.Shape.Volume - inside_volume)
            _require(
                outside_volume <= EPSILON_VOLUME,
                "rounded R20 corner accepted %.6f mm^3 of bin outside the usable contour"
                % outside_volume,
            )
            return {"behavior": "contained", "outside_volume_mm3": outside_volume}
        finally:
            _close_document(doc)

    def over_height_object_is_rejected_instead_of_shortened(self):
        # Insert depth 20 minus 1 mm bottom clearance leaves 19 mm total, but a
        # 2.4 mm carrier floor leaves only 16.6 mm for this bin/bay.
        tall_bin = _object(
            "too-tall", "removable_bin", 30, 30, 25, 35, height=18.0,
            extra={"floor": 2.4},
        )
        spec = _base_spec([tall_bin], depth=20.0)
        doc = _unique_document("OverHeight")
        try:
            try:
                result = self.api["generate_project"](spec, document=doc)
            except Exception as exc:
                _require(
                    "height" in str(exc).lower() or "floor" in str(exc).lower(),
                    "over-height rejection was not actionable: %s" % exc,
                )
                return {"behavior": "rejected", "message": str(exc)}
            payload = _result_mapping(result)
            unplaced = payload.get("unplaced", [])
            codes = {str(item.get("code")) for item in unplaced if isinstance(item, Mapping)}
            _require(
                "insufficient_height" in codes,
                "over-height bin was generated or silently shortened instead of rejected",
            )
            return {"behavior": "unplaced", "codes": sorted(codes)}
        finally:
            _close_document(doc)

    def split_outputs_fit_the_usable_bed(self):
        spec = _base_spec(
            length=300.0,
            width=200.0,
            depth=20.0,
            radius=5.0,
            bed_x=100.0,
            bed_y=100.0,
            bed_margin=5.0,
            split=True,
        )
        doc = _unique_document("BedSplit")
        try:
            result = self.api["generate_project"](spec, document=doc)
            payload = _result_mapping(result)
            objects = self.api["active_results"](doc)
            _require(len(objects) > 1, "split=true left the oversized composer as one part")
            oversized = [
                {
                    "name": item.Name,
                    "x": item.Shape.BoundBox.XLength,
                    "y": item.Shape.BoundBox.YLength,
                }
                for item in objects
                if item.Shape.BoundBox.XLength > 90.0 + EPSILON_LENGTH
                or item.Shape.BoundBox.YLength > 90.0 + EPSILON_LENGTH
            ]
            _require(not oversized, "split output exceeds the 90 x 90 mm usable bed: %r" % oversized)
            _require(payload.get("parts") == len(objects), "GenerationResult part count is stale after splitting")
            return {"parts": len(objects), "usable_bed_mm": [90.0, 90.0]}
        finally:
            _close_document(doc)

    def shared_panel_split_outputs_fit_the_usable_bed(self):
        spec = _base_spec(
            length=300.0,
            width=200.0,
            depth=28.0,
            radius=12.0,
            containment="shared_panel",
            bed_x=100.0,
            bed_y=100.0,
            bed_margin=5.0,
            split=True,
        )
        doc = _unique_document("SharedPanelBedSplit")
        try:
            result = self.api["generate_project"](spec, document=doc)
            payload = _result_mapping(result)
            objects = self.api["active_results"](doc)
            _require(len(objects) > 3, "shared-panel split produced too few parts")
            oversized = []
            for item in objects:
                size_x, size_y = self.api["_printable_plan_dimensions"](item.Shape)
                if size_x > 90.0 + EPSILON_LENGTH or size_y > 90.0 + EPSILON_LENGTH:
                    oversized.append({"name": item.Name, "x": size_x, "y": size_y})
            _require(
                not oversized,
                "shared-panel split output exceeds the 90 x 90 mm usable bed: %r"
                % oversized,
            )
            _require(
                payload.get("parts") == len(objects),
                "shared-panel GenerationResult part count is stale after splitting",
            )
            return {"parts": len(objects), "usable_bed_mm": [90.0, 90.0]}
        finally:
            _close_document(doc)

    def automatic_layout_respects_real_case_contour(self):
        objects = [
            _object("auto-bin-%02d" % index, "removable_bin", 0, 0, 14, 18,
                    height=8.0, locked=False)
            for index in range(1, 4)
        ]
        spec = _base_spec(
            objects, length=110.0, width=85.0, depth=28.0, radius=22.0
        )
        spec["layout_strategy"] = "balanced"
        doc = _unique_document("AutoLayoutContour")
        try:
            result = self.api["generate_project"](spec, document=doc)
            payload = _result_mapping(result)
            project = payload.get("project", {})
            _require(not payload.get("unplaced"),
                     "balanced layout unexpectedly left a small bin unplaced")
            inset = float(project.get("case", {}).get("layout_inset", 0.0))
            _require(inset > 0.0,
                     "rounded case did not derive a conservative real-contour inset")
            normalized = self.api["_project_module"]().validate_project(project)
            usable_case = self.api["_case_blank"](
                self.api["_project_case_params"](normalized)
            )
            outside = {}
            for item in project.get("objects", []):
                part = doc.getObject("Bin_" + item["id"].replace("-", "_"))
                _require(part is not None,
                         "auto-layout bin %s was not generated" % item["id"])
                outside[item["id"]] = max(
                    0.0, part.Shape.Volume - part.Shape.common(usable_case).Volume
                )
            _require(
                all(value <= EPSILON_VOLUME for value in outside.values()),
                "auto-layout accepted bin geometry outside the rounded contour: %r"
                % outside,
            )
            return {
                "strategy": project.get("layout_strategy"),
                "layout_inset_mm": inset,
                "outside_volume_mm3": outside,
            }
        finally:
            _close_document(doc)

    def stl_step_and_fcstd_exports_reopen(self):
        import Mesh
        import Part

        doc = _unique_document("Exports")
        step_doc = None
        fcstd_doc = None
        try:
            self.api["generate_project"](
                _base_spec(length=80.0, width=60.0, depth=18.0, radius=6.0),
                document=doc,
            )
            stl_path = self.temp / "canonical.stl"
            step_path = self.temp / "canonical.step"
            fcstd_path = self.temp / "canonical.FCStd"
            self.api["export_stl"](str(stl_path), doc)
            self.api["export_step"](str(step_path), doc)
            self.api["save_fcstd"](str(fcstd_path), doc)
            for path in (stl_path, step_path, fcstd_path):
                _require(path.is_file() and path.stat().st_size > 0,
                         "%s export is missing or empty" % path.suffix)

            mesh = Mesh.Mesh(str(stl_path))
            _require(mesh.CountFacets > 0 and mesh.isSolid(),
                     "reopened STL is empty or not a closed solid")

            step_doc = _unique_document("StepReopen")
            Part.insert(str(step_path), step_doc.Name)
            step_doc.recompute()
            step_shapes = [
                item.Shape for item in step_doc.Objects
                if hasattr(item, "Shape") and not item.Shape.isNull()
            ]
            _require(
                step_shapes and all(shape.isValid() and len(shape.Solids) > 0
                                    for shape in step_shapes),
                "reopened STEP contains no valid solid",
            )

            fcstd_doc = App.openDocument(str(fcstd_path))
            loaded = self.api["load_project"](fcstd_doc)
            _require(loaded.get("schema_version") == 1,
                     "reopened FCStd lost editable schema-v1 project data")
            return {
                "stl_facets": mesh.CountFacets,
                "step_solids": sum(len(shape.Solids) for shape in step_shapes),
                "fcstd_schema_version": loaded.get("schema_version"),
            }
        finally:
            _close_document(doc)
            _close_document(step_doc)
            _close_document(fcstd_doc)

    def individual_retention_has_no_unintended_overlap(self):
        bin_object = _object("bin-01", "removable_bin", 30, 30, 25, 40)
        doc = _unique_document("Retention_individual_lids")
        try:
            self.api["generate_project"](
                _base_spec([bin_object], length=120.0, width=90.0,
                           depth=30.0, radius=8.0, containment="individual_lids"),
                document=doc,
            )
            bin_part = doc.getObject("Bin_bin_01")
            lid_part = doc.getObject("Lid_bin_01")
            _require(bin_part is not None and lid_part is not None,
                     "individual retention did not use stable bin/lid names")
            overlap = bin_part.Shape.common(lid_part.Shape).Volume
            _require(overlap <= EPSILON_VOLUME,
                     "individual lid intersects its bin by %.6f mm^3" % overlap)
            return {"bin_lid_overlap_mm3": overlap}
        finally:
            _close_document(doc)

    def shared_retention_has_no_unintended_overlap(self):
        bin_object = _object("bin-01", "removable_bin", 30, 30, 25, 40)
        doc = _unique_document("Retention_shared_panel")
        try:
            self.api["generate_project"](
                _base_spec([bin_object], length=120.0, width=90.0,
                           depth=30.0, radius=8.0, containment="shared_panel"),
                document=doc,
            )
            carrier = doc.getObject("LowerCarrier")
            panel = doc.getObject("SharedRetentionPanel")
            clips = doc.getObject("SharedPanelClips")
            _require(all(item is not None for item in (carrier, panel, clips)),
                     "shared retention did not use stable carrier/panel/clip names")
            overlaps = {
                "panel_carrier": panel.Shape.common(carrier.Shape).Volume,
                "clips_panel": clips.Shape.common(panel.Shape).Volume,
                "clips_carrier": clips.Shape.common(carrier.Shape).Volume,
            }
            _require(
                all(value <= EPSILON_VOLUME for value in overlaps.values()),
                "shared retention parts intersect: %r" % overlaps,
            )
            return {key + "_overlap_mm3": value for key, value in overlaps.items()}
        finally:
            _close_document(doc)

    def project_json_survives_save_close_reopen(self):
        project_object = _object(
            "roundtrip-bin", "removable_bin", 32, 24, 22, 36,
            rotation=90.0, locked=False,
        )
        spec = _base_spec([project_object], containment="individual_lids")
        spec["case"]["evidence_status"] = "measured"
        spec["case"]["evidence"] = {
            "source": "user measurement", "physical_fit": False}
        path = self.temp / "project-roundtrip.FCStd"
        doc = _unique_document("ProjectRoundTrip")
        reopened = None
        try:
            self.api["generate_project"](spec, document=doc)
            self.api["save_fcstd"](str(path), doc)
            saved_name = doc.Name
            _close_document(doc)
            doc = None
            reopened = App.openDocument(str(path))
            load_project = self.api.get("load_project")
            _require(callable(load_project), "public API does not expose load_project(document)")
            loaded = load_project(reopened)
            _require(isinstance(loaded, Mapping), "load_project() did not return a project mapping")
            _require(loaded.get("schema_version") == 1, "reopened project lost schema_version")
            objects = loaded.get("objects", [])
            _require(len(objects) == 1 and objects[0].get("id") == "roundtrip-bin",
                     "reopened project lost its composed object")
            _require(loaded.get("containment", {}).get("mode") == "individual_lids",
                     "reopened project lost containment settings")
            _require(loaded.get("case", {}).get("evidence_status") == "measured",
                     "reopened project lost case evidence status")
            _require(loaded.get("case", {}).get("evidence", {}).get("source") ==
                     "user measurement",
                     "reopened project lost nested case evidence")
            _require(loaded.get("parts") == len(loaded.get("results", [])) >= 1,
                     "reopened project lost generated part metadata")
            _require(loaded.get("result") == loaded["results"][0],
                     "reopened project lost primary result metadata")
            _require(isinstance(loaded.get("warnings"), list) and
                     isinstance(loaded.get("unplaced"), list),
                     "reopened project lost warning or unplaced metadata")
            return {
                "saved_document": saved_name,
                "reopened_document": reopened.Name,
                "object_ids": [item.get("id") for item in objects],
                "parts": loaded.get("parts"),
                "evidence_status": loaded["case"]["evidence_status"],
            }
        finally:
            _close_document(doc)
            _close_document(reopened)

    def existing_svg_temp_document_is_preserved(self):
        if "CaseInsertSVGImport" in App.listDocuments():
            raise ContractSkip(
                "A pre-existing CaseInsertSVGImport document is open; the test will not touch it"
            )
        svg_path = self.temp / "temp-document.svg"
        _write_svg(svg_path, "evenodd")
        sentinel_doc = App.newDocument("CaseInsertSVGImport")
        try:
            sentinel = sentinel_doc.addObject("App::FeaturePython", "UserSentinel")
            sentinel.addProperty("App::PropertyString", "Owner", "Test")
            sentinel.Owner = "integration-test-user-document"
            original_identity = id(sentinel_doc)
            self.api["_import_svg_faces"](str(svg_path), 1.0, 0.0, 0.0, 0.0, 0.0)
            _require("CaseInsertSVGImport" in App.listDocuments(),
                     "SVG import closed the caller's CaseInsertSVGImport document")
            preserved = App.getDocument("CaseInsertSVGImport")
            _require(id(preserved) == original_identity,
                     "SVG import replaced the caller's CaseInsertSVGImport document")
            marker = preserved.getObject("UserSentinel")
            _require(marker is not None and marker.Owner == "integration-test-user-document",
                     "SVG import destroyed content in the caller's document")
            return {"document": preserved.Name, "sentinel": marker.Owner}
        finally:
            _close_document(sentinel_doc)

    def generated_objects_are_namespaced_in_existing_documents(self):
        doc = _unique_document("NamespacedObjects")
        try:
            user_parameters = doc.addObject("App::FeaturePython", "Parameters")
            user_parameters.addProperty("App::PropertyString", "Owner", "User")
            user_parameters.Owner = "pre-existing-user-parameters"
            user_named_parameters = doc.addObject(
                "App::FeaturePython", "CaseInsertGeneratorParameters")
            user_named_parameters.addProperty(
                "App::PropertyString", "Owner", "User")
            user_named_parameters.Owner = "pre-existing-namespaced-user-object"
            unrelated_group = doc.addObject(
                "App::DocumentObjectGroup", "UnrelatedInsertGenerator")
            sentinel = doc.addObject("App::FeaturePython", "UserSentinel")
            sentinel.addProperty("App::PropertyString", "Owner", "User")
            sentinel.Owner = "pre-existing-user-group"
            unrelated_group.addObject(sentinel)
            user_named_group = doc.addObject(
                "App::DocumentObjectGroup", "CaseInsertGeneratorProject")
            named_sentinel = doc.addObject("App::FeaturePython", "NamedUserSentinel")
            user_named_group.addObject(named_sentinel)

            spec = _base_spec([
                _object("collision-pocket", "rectangular_pocket", 30, 35,
                        35, 28, 9)
            ])
            first = _result_mapping(self.api["generate_project"](spec, document=doc))
            loaded = self.api["load_project"](doc)
            results = self.api["active_results"](doc)
            _require(loaded["schema_version"] == 1,
                     "load_project selected a pre-existing user Parameters object")
            _require(len(results) == len(first["results"]),
                     "active_results selected a pre-existing user Parameters object")

            # A second generation removes only the marked add-on group.
            self.api["generate_project"](spec, document=doc)
            _require(doc.getObject("Parameters").Owner == "pre-existing-user-parameters",
                     "generation replaced the user's Parameters object")
            _require(doc.getObject("CaseInsertGeneratorParameters").Owner ==
                     "pre-existing-namespaced-user-object",
                     "generation replaced a same-named user object")
            _require(doc.getObject("UnrelatedInsertGenerator").getObject(
                         "UserSentinel") is not None,
                     "generation removed the user's unrelated user group")
            _require(doc.getObject("CaseInsertGeneratorProject").getObject(
                         "NamedUserSentinel") is not None,
                     "generation removed the user's namespaced group")
            return {
                "loaded_schema": loaded["schema_version"],
                "result_count": len(results),
                "user_objects_preserved": 4,
            }
        finally:
            _close_document(doc)

    def every_loose_storage_type_warns_without_containment(self):
        checked = {}
        for object_type in (
                "removable_bin", "existing_container_bay", "divider_region"):
            doc = _unique_document("LooseWarning_%s" % object_type)
            try:
                result = _result_mapping(self.api["generate_project"](
                    _base_spec([
                        _object("loose-item", object_type, 35, 35, 40, 32, 9)
                    ], containment="none"),
                    document=doc,
                ))
                warnings = [str(item).lower() for item in result["warnings"]]
                _require(any("loose storage" in item and
                             "containment" in item for item in warnings),
                         "%s did not warn about missing containment" % object_type)
                checked[object_type] = len(warnings)
            finally:
                _close_document(doc)
        doc = _unique_document("LooseWarning_PositiveGap")
        try:
            spec = _base_spec([
                _object("loose-item", "divider_region", 35, 35, 40, 32, 9)
            ], containment="none")
            spec["lid"] = {"source": "measured", "clearance_mm": 0.1}
            result = _result_mapping(
                self.api["generate_project"](spec, document=doc))
            warnings = [str(item).lower() for item in result["warnings"]]
            _require(any("loose storage" in item for item in warnings),
                     "a known positive lid gap suppressed containment warning")
            checked["measured_positive_gap"] = len(warnings)
        finally:
            _close_document(doc)
        return checked

    def two_layer_keys_clear_contents_and_upper_has_lift_access(self):
        collision_doc = _unique_document("LayerKeyCollision")
        try:
            collision_spec = _base_spec([
                _object("key-zone-bin", "removable_bin", 6, 6, 14, 14, 8,
                        layer="lower")
            ], length=120.0, width=90.0, depth=30.0, radius=8.0,
                layers=True)
            try:
                self.api["generate_project"](
                    collision_spec, document=collision_doc)
            except ValueError as exc:
                _require("keyed alignment zone" in str(exc),
                         "key collision was rejected without an actionable reason")
                collision_message = str(exc)
            else:
                raise ContractFailure(
                    "two-layer alignment peg overlapped lower content without rejection")
        finally:
            _close_document(collision_doc)

        safe_doc = _unique_document("LayerLiftAccess")
        try:
            safe_spec = _base_spec([
                _object("safe-lower", "rectangular_pocket", 42, 34,
                        20, 18, 7, layer="lower"),
                _object("safe-upper", "rectangular_pocket", 70, 48,
                        18, 16, 6, layer="upper"),
            ], length=140.0, width=100.0, depth=34.0, radius=8.0,
                layers=True)
            result = _result_mapping(
                self.api["generate_project"](safe_spec, document=safe_doc))
            upper = safe_doc.getObject("UpperCarrier")
            _require(upper is not None and upper.Shape.isValid(),
                     "safe two-layer project has no valid upper carrier")
            _require(any("lift-access notches" in str(item)
                         for item in result["warnings"]),
                     "two-layer result does not explain its lift access")

            import Part
            params = self.api["_project_case_params"](safe_spec)
            whole = self.api["_case_blank"](params)
            bounds = whole.BoundBox
            radius = min(
                8.0, max(4.0, min(bounds.XLength, bounds.YLength) / 18.0))
            upper_bounds = upper.Shape.BoundBox
            probe = Part.makeCylinder(
                radius / 4.0, upper_bounds.ZLength + 1.0,
                App.Vector(bounds.XMin + radius / 2.0,
                           bounds.YMin + bounds.YLength / 2.0,
                           upper_bounds.ZMin - 0.5),
            )
            notch_overlap = upper.Shape.common(probe).Volume
            _require(notch_overlap <= EPSILON_VOLUME,
                     "upper carrier lift notch is not open through the carrier")
            return {
                "collision_message": collision_message,
                "lift_notch_probe_overlap_mm3": notch_overlap,
            }
        finally:
            _close_document(safe_doc)

    def lid_panel_unknown_clearance_previews_persists_and_blocks_printing(self):
        spec = _lid_panel_spec(pattern="slot_grid", clearance=None)
        spec["lid_panel"]["slot_grid"].update({
            "slot_length_mm": 27.0,
            "slot_width_mm": 5.0,
            "pitch_x_mm": 36.0,
            "pitch_y_mm": 36.0,
            "orientation": "vertical",
        })
        doc = _unique_document("LidPanelPreview")
        reopened = None
        path = self.temp / "lid-panel-preview.FCStd"
        try:
            preview = _result_mapping(
                self.api["preview_lid_panel_project"](spec, document=doc))
            _require(preview["parts"] == 0 and preview.get("printable") is False,
                     "unknown-clearance preview exposed a printable part")
            panel_preview = doc.getObject("LidPanelPreview")
            retainer_preview = doc.getObject("LidRetainersPreview")
            _require(panel_preview is not None and panel_preview.Shape.isValid(),
                     "unknown-clearance configuration did not create a visible preview")
            _require(retainer_preview is not None and retainer_preview.Shape.isValid(),
                     "unknown-clearance preview omitted configured retainers")
            _require(
                preview["project"]["lid_panel_report"]["pattern_count"] > 0,
                "unknown-clearance preview omitted the configured slot pattern")
            _require(self.api["active_results"](doc) == [],
                     "non-printable preview was discoverable as an export result")
            blocked_message = ""
            try:
                self.api["generate_lid_panel_project"](spec, document=doc)
            except ValueError as exc:
                blocked_message = str(exc)
            _require("Closed-lid clearance is Unknown" in blocked_message,
                     "print generation did not fail closed on unknown clearance")
            self.api["save_fcstd"](str(path), doc)
            _close_document(doc)
            doc = None
            reopened = App.openDocument(str(path))
            loaded = self.api["load_project"](reopened)
            _require(loaded["lid_panel"]["enabled"] is True,
                     "reopened preview lost the enabled panel configuration")
            _require(loaded["lid_panel"]["pattern"] == "slot_grid",
                     "reopened preview lost its panel pattern")
            _require(loaded["lid_panel"]["slot_grid"]["orientation"] == "vertical",
                     "reopened preview lost nested slot settings")
            _require(loaded["lid"]["source"] == "unknown",
                     "reopened preview incorrectly promoted clearance evidence")
            return {
                "blocked_message": blocked_message,
                "preview_parts": preview["parts"],
                "persisted_pattern": loaded["lid_panel"]["pattern"],
                "persisted_orientation": loaded["lid_panel"]["slot_grid"]["orientation"],
            }
        finally:
            _close_document(doc)
            _close_document(reopened)

    def lid_panel_patterns_grid_bounds_keepouts_and_valid_solids(self):
        documents = []
        details = {}
        try:
            for pattern in ("solid", "slot_grid", "perforated_grid"):
                spec = _lid_panel_spec(pattern=pattern)
                spec["lid_panel"]["keepouts"]["rectangles"] = [{
                    "label": "Synthetic lid rib",
                    "x_mm": 70.0,
                    "y_mm": 34.0,
                    "length_mm": 28.0,
                    "width_mm": 16.0,
                }]
                spec["lid_panel"]["mounting"].update({
                    "fastener_holes_enabled": True,
                    "custom_fastener_holes": [
                        {"x_mm": 22.0, "y_mm": 22.0},
                        {"x_mm": 170.0, "y_mm": 72.0},
                    ],
                })
                doc = _unique_document("LidPattern_%s" % pattern)
                documents.append(doc)
                result = _result_mapping(
                    self.api["generate_lid_panel_project"](spec, document=doc))
                _require(result.get("valid") is True,
                         "%s panel did not report valid geometry" % pattern)
                panel = doc.getObject("LidPanel")
                retainers = [
                    item for item in self.api["active_results"](doc)
                    if item.Name.startswith("LidPanelRetainer")
                ]
                _require(panel is not None and panel.Shape.isValid() and
                         len(panel.Shape.Solids) == 1,
                         "%s panel is not one valid solid" % pattern)
                _require(len(retainers) == 4 and
                         all(item.Shape.isValid() and len(item.Shape.Solids) == 1
                             for item in retainers),
                         "%s panel did not produce printable retainers" % pattern)
                plan = result["panel_plan"]
                for bounds in result.get("pattern_bounds", []):
                    _require(bounds[0] >= -EPSILON_LENGTH and
                             bounds[1] >= -EPSILON_LENGTH and
                             bounds[0] + bounds[2] <= plan["length_mm"] + EPSILON_LENGTH and
                             bounds[1] + bounds[3] <= plan["width_mm"] + EPSILON_LENGTH,
                             "%s pattern opening escaped the panel bounds" % pattern)
                keepout = spec["lid_panel"]["keepouts"]["rectangles"][0]
                import Part
                probe = Part.makeBox(
                    keepout["length_mm"], keepout["width_mm"],
                    spec["lid_panel"]["thickness_mm"] + 2.0,
                    App.Vector(plan["x_mm"] + keepout["x_mm"],
                               plan["y_mm"] + keepout["y_mm"], -1.0))
                keepout_overlap = panel.Shape.common(probe).Volume
                _require(keepout_overlap <= EPSILON_VOLUME,
                         "%s panel filled a configured lid-clearance keep-out" % pattern)
                pattern_count = result["project"]["lid_panel_report"]["pattern_count"]
                _require((pattern_count == 0) == (pattern == "solid"),
                         "%s pattern count is inconsistent" % pattern)
                details[pattern] = {
                    "pattern_count": pattern_count,
                    "keepout_overlap_mm3": keepout_overlap,
                    "panel_solids": len(panel.Shape.Solids),
                    "retainer_solids": sum(
                        len(item.Shape.Solids) for item in retainers),
                }
            return details
        finally:
            for doc in documents:
                _close_document(doc)

    def lid_panel_keyed_split_parts_fit_and_do_not_overlap(self):
        spec = _lid_panel_spec(
            pattern="slot_grid", length=340.0, width=230.0,
            split=True, bed_x=125.0, bed_y=120.0)
        spec["printer"]["margin"] = 5.0
        spec["lid_panel"]["splitting"].update({
            "keyed_alignment": True,
            "key_size_mm": 9.0,
            "key_clearance_mm": 0.3,
        })
        doc = _unique_document("LidPanelKeyedSplit")
        try:
            result = _result_mapping(
                self.api["generate_lid_panel_project"](spec, document=doc))
            panel_parts = [item for item in self.api["active_results"](doc)
                           if item.Name.startswith("LidPanelPart")]
            retainer_parts = [item for item in self.api["active_results"](doc)
                              if item.Name.startswith("LidPanelRetainer")]
            _require(len(panel_parts) > 1, "oversized lid panel was not split")
            _require(len(retainer_parts) == 4,
                     "oversized lid panel did not keep four separate retainers")
            _require(result["split"]["key_count"] > 0,
                     "split lid panel has no complementary alignment keys")
            usable_x = spec["printer"]["bed_x"] - 2.0 * spec["printer"]["margin"]
            usable_y = spec["printer"]["bed_y"] - 2.0 * spec["printer"]["margin"]
            sizes = []
            for part in panel_parts:
                realised = self.api["_printable_plan_dimensions"](part.Shape)
                _require(realised[0] <= usable_x + 0.02 and
                         realised[1] <= usable_y + 0.02,
                         "keyed panel part exceeds the usable bed")
                sizes.append(realised)
            for retainer in retainer_parts:
                realised = self.api["_printable_plan_dimensions"](retainer.Shape)
                _require(realised[0] <= usable_x + 0.02 and
                         realised[1] <= usable_y + 0.02,
                         "separate lid-panel retainer exceeds the usable bed")
            maximum_overlap = 0.0
            for index, first in enumerate(panel_parts):
                for second in panel_parts[index + 1:]:
                    maximum_overlap = max(
                        maximum_overlap,
                        first.Shape.common(second.Shape).Volume)
            _require(maximum_overlap <= EPSILON_VOLUME,
                     "assembled keyed panel parts overlap")
            return {
                "panel_parts": len(panel_parts),
                "retainer_parts": len(retainer_parts),
                "alignment_keys": result["split"]["key_count"],
                "maximum_overlap_mm3": maximum_overlap,
                "maximum_part_mm": [
                    max(item[0] for item in sizes),
                    max(item[1] for item in sizes),
                ],
            }
        finally:
            _close_document(doc)

    def lid_panel_stl_step_fcstd_exports_and_settings_reopen(self):
        import Mesh
        import Part

        spec = _lid_panel_spec(pattern="perforated_grid")
        spec["lid_panel"]["payload_thickness_mm"] = 9.0
        spec["lid_panel"]["perforated_grid"].update({
            "diameter_mm": 6.0,
            "pitch_x_mm": 15.0,
            "pitch_y_mm": 14.0,
        })
        spec["lid_panel"]["mounting"]["retainer_clearance_mm"] = 0.42
        doc = _unique_document("LidPanelExports")
        reopened = None
        step_documents = []
        try:
            result = _result_mapping(
                self.api["generate_lid_panel_project"](spec, document=doc))
            stl_path = self.temp / "lid-panel.stl"
            step_path = self.temp / "lid-panel.step"
            fcstd_path = self.temp / "lid-panel.FCStd"
            stl_outputs = self.api["export_stl"](str(stl_path), doc)
            step_outputs = self.api["export_step"](str(step_path), doc)
            self.api["save_fcstd"](str(fcstd_path), doc)
            stl_paths = [stl_outputs] if isinstance(stl_outputs, str) else stl_outputs
            step_paths = [step_outputs] if isinstance(step_outputs, str) else step_outputs
            _require(len(stl_paths) == result["parts"] == len(step_paths),
                     "separate panel export counts do not match generated parts")
            for output in stl_paths:
                mesh = Mesh.Mesh(str(output))
                _require(mesh.CountFacets > 0 and mesh.isSolid(),
                         "reopened lid-panel STL is empty or open")
            step_solids = 0
            for index, output in enumerate(step_paths):
                step_doc = _unique_document("LidPanelStep%02d" % index)
                step_documents.append(step_doc)
                Part.insert(str(output), step_doc.Name)
                step_doc.recompute()
                shapes = [item.Shape for item in step_doc.Objects
                          if hasattr(item, "Shape") and not item.Shape.isNull()]
                _require(shapes and all(item.isValid() and item.Solids
                                        for item in shapes),
                         "reopened lid-panel STEP has no valid solid")
                step_solids += sum(len(item.Solids) for item in shapes)
            _close_document(doc)
            doc = None
            reopened = App.openDocument(str(fcstd_path))
            loaded = self.api["load_project"](reopened)
            _require(loaded["lid_panel"]["pattern"] == "perforated_grid",
                     "FCStd round trip lost the panel pattern")
            _require(loaded["lid_panel"]["payload_thickness_mm"] == 9.0,
                     "FCStd round trip lost payload thickness")
            _require(loaded["lid_panel"]["mounting"]["retainer_clearance_mm"] == 0.42,
                     "FCStd round trip lost an advanced mounting setting")
            _require(loaded["verification"]["physical_fit"] is False,
                     "FCStd round trip lost the physical-fit-unverified gate")
            return {
                "stl_files": len(stl_paths),
                "step_files": len(step_paths),
                "step_solids": step_solids,
                "fcstd_schema_version": loaded["schema_version"],
                "persisted_pattern": loaded["lid_panel"]["pattern"],
            }
        finally:
            _close_document(doc)
            _close_document(reopened)
            for step_doc in step_documents:
                _close_document(step_doc)

    def per_object_fit_clearance_and_finger_scoop_modify_geometry(self):
        documents = []
        try:
            volumes = {}
            for label, clearance, finger_scoop in (
                    ("baseline", 0.0, False),
                    ("clearance", 1.0, False),
                    ("clearance_and_scoop", 1.0, True)):
                doc = _unique_document("Modifiers_%s" % label)
                documents.append(doc)
                obj = _object(
                    "modifier-pocket", "rectangular_pocket",
                    50, 48, 26, 34, 8,
                    extra={
                        "clearance": clearance,
                        "finger_scoop": finger_scoop,
                    },
                )
                result = _result_mapping(self.api["generate_project"](
                    _base_spec([obj], length=150.0, width=110.0,
                               depth=26.0, radius=6.0),
                    document=doc,
                ))
                volumes[label] = doc.getObject("LowerCarrier").Shape.Volume
                persisted = result["project"]["objects"][0]
                _require(persisted["clearance"] == clearance and
                         persisted["finger_scoop"] is finger_scoop,
                         "%s modifier did not persist" % label)
            _require(volumes["clearance"] < volumes["baseline"],
                     "fit clearance did not enlarge the generated pocket")
            _require(volumes["clearance_and_scoop"] < volumes["clearance"],
                     "finger scoop removed no additional carrier material")
            return {key + "_volume_mm3": value
                    for key, value in volumes.items()}
        finally:
            for doc in documents:
                _close_document(doc)

    def svg_pocket_through_invert_and_clearance_behaviors(self):
        svg_path = ROOT / "examples" / "example_cutout.svg"
        base = {
            "case_model": "Custom Case",
            "internal_length": 140.0,
            "internal_width": 100.0,
            "insert_depth": 20.0,
            "corner_radius": 5.0,
            "side_clearance": 0.0,
            "bottom_clearance": 0.0,
            "taper_allowance": 0.0,
            "svg_path": str(svg_path),
            "svg_scale": 1.0,
            "svg_x": 20.0,
            "svg_y": 20.0,
            "svg_rotation": 0.0,
            "svg_clearance": 0.0,
            "cutout_depth": 5.0,
            "through_cut": False,
            "invert_svg": False,
        }

        _top_result, top_cutter, _open, _used = self.api["build_svg_insert"](
            dict(base))
        invert_params = dict(base)
        invert_params["invert_svg"] = True
        _invert_result, invert_cutter, _open, _used = self.api[
            "build_svg_insert"](invert_params)
        through_params = dict(base)
        through_params["through_cut"] = True
        _through_result, through_cutter, _open, _used = self.api[
            "build_svg_insert"](through_params)
        clearance_params = dict(base)
        clearance_params["svg_clearance"] = 1.0
        _clear_result, clear_cutter, _open, _used = self.api[
            "build_svg_insert"](clearance_params)

        _require(abs(top_cutter.BoundBox.ZMin - 15.0) <= EPSILON_LENGTH,
                 "top pocket does not cut down from the insert top")
        _require(abs(invert_cutter.BoundBox.ZMin) <= EPSILON_LENGTH and
                 abs(invert_cutter.BoundBox.ZMax - 5.0) <= EPSILON_LENGTH,
                 "invert pocket does not cut upward from the insert bottom")
        _require(through_cutter.BoundBox.ZMin < 0.0 and
                 through_cutter.BoundBox.ZMax > 20.0,
                 "through SVG does not cross the full insert height")
        _require(clear_cutter.Volume > top_cutter.Volume and
                 clear_cutter.BoundBox.XLength > top_cutter.BoundBox.XLength and
                 clear_cutter.BoundBox.YLength > top_cutter.BoundBox.YLength,
                 "SVG clearance did not expand the cutter")
        return {
            "top_z": [top_cutter.BoundBox.ZMin, top_cutter.BoundBox.ZMax],
            "invert_z": [invert_cutter.BoundBox.ZMin,
                         invert_cutter.BoundBox.ZMax],
            "through_z": [through_cutter.BoundBox.ZMin,
                          through_cutter.BoundBox.ZMax],
            "clearance_volume_gain_mm3": (
                clear_cutter.Volume - top_cutter.Volume),
        }

    def svg_evenodd_and_nonzero_areas_are_distinct(self):
        expected = {"evenodd": 1200.0, "nonzero": 1600.0}
        actual = {}
        for fill_rule in ("evenodd", "nonzero"):
            svg_path = self.temp / (fill_rule + ".svg")
            _write_svg(svg_path, fill_rule)
            faces, open_count = self.api["_import_svg_faces"](
                str(svg_path), 1.0, 0.0, 0.0, 0.0, 0.0
            )
            _require(open_count == 0, "%s fixture unexpectedly reported open geometry" % fill_rule)
            area = sum(face.Area for face in faces)
            actual[fill_rule] = area
            _require(
                abs(area - expected[fill_rule]) <= 1.0e-5,
                "%s compound area was %.6f mm^2; expected %.6f mm^2"
                % (fill_rule, area, expected[fill_rule]),
            )
        _require(actual["evenodd"] != actual["nonzero"],
                 "evenodd and nonzero fill rules produced identical geometry")
        return {key + "_area_mm2": value for key, value in actual.items()}


TEST_METHODS = (
    "generate_project_returns_generation_result",
    "preset_project_resolves_catalog_depth",
    "all_six_object_types_generate_valid_geometry",
    "rounded_contour_never_accepts_outside_bin_geometry",
    "over_height_object_is_rejected_instead_of_shortened",
    "split_outputs_fit_the_usable_bed",
    "shared_panel_split_outputs_fit_the_usable_bed",
    "automatic_layout_respects_real_case_contour",
    "stl_step_and_fcstd_exports_reopen",
    "individual_retention_has_no_unintended_overlap",
    "shared_retention_has_no_unintended_overlap",
    "project_json_survives_save_close_reopen",
    "generated_objects_are_namespaced_in_existing_documents",
    "every_loose_storage_type_warns_without_containment",
    "two_layer_keys_clear_contents_and_upper_has_lift_access",
    "lid_panel_unknown_clearance_previews_persists_and_blocks_printing",
    "lid_panel_patterns_grid_bounds_keepouts_and_valid_solids",
    "lid_panel_keyed_split_parts_fit_and_do_not_overlap",
    "lid_panel_stl_step_fcstd_exports_and_settings_reopen",
    "per_object_fit_clearance_and_finger_scoop_modify_geometry",
    "svg_pocket_through_invert_and_clearance_behaviors",
    "existing_svg_temp_document_is_preserved",
    "svg_evenodd_and_nonzero_areas_are_distinct",
)


def run_integration_tests():
    previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
    previous_sys_path = list(sys.path)
    import freecad as freecad_namespace

    namespace_prefix = "freecad.CaseInsertGenerator"
    previous_freecad_path = list(freecad_namespace.__path__)
    previous_namespace_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == namespace_prefix or name.startswith(namespace_prefix + ".")
    }
    report = {
        "suite": "case-insert-generator-freecad-integration",
        "freecad_version": ".".join(str(item) for item in App.Version()[:3]),
        "tests": [],
    }
    try:
        sys.path.insert(0, str(ROOT))
        freecad_namespace.__path__.insert(0, str(ROOT / "freecad"))
        for name in previous_namespace_modules:
            sys.modules.pop(name, None)
        with tempfile.TemporaryDirectory(prefix="cig-freecad-integration-") as directory:
            launcher_namespace = runpy.run_path(
                str(MACRO_PATH), run_name="cig_freecad_integration")
            engine = importlib.import_module(ENGINE_MODULE)
            _require(
                Path(engine.__file__).resolve() == ENGINE_PATH,
                "integration suite imported the wrong engine checkout: %s"
                % engine.__file__,
            )
            namespace = dict(vars(engine))
            for public_name in (
                    "generate_insert", "generate_lid_panel_project",
                    "generate_project", "load_case_catalog", "load_project",
                    "preview_lid_panel_project", "show_dialog"):
                public_api = launcher_namespace.get(public_name)
                _require(callable(public_api),
                         "compatibility launcher does not expose %s" % public_name)
                namespace[public_name] = public_api
            contracts = IntegrationContracts(namespace, directory)
            for method_name in TEST_METHODS:
                try:
                    detail = getattr(contracts, method_name)()
                    report["tests"].append(
                        {"name": method_name, "status": "pass", "detail": detail}
                    )
                except ContractSkip as exc:
                    report["tests"].append(
                        {"name": method_name, "status": "skip", "message": str(exc)}
                    )
                except Exception as exc:
                    report["tests"].append(
                        {
                            "name": method_name,
                            "status": "fail",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc().splitlines(),
                        }
                    )
    finally:
        for name in list(sys.modules):
            if name == namespace_prefix or name.startswith(namespace_prefix + "."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_namespace_modules)
        freecad_namespace.__path__[:] = previous_freecad_path
        sys.path[:] = previous_sys_path
        if previous_active and previous_active in App.listDocuments():
            App.setActiveDocument(previous_active)
    passed = sum(item["status"] == "pass" for item in report["tests"])
    failed = sum(item["status"] == "fail" for item in report["tests"])
    skipped = sum(item["status"] == "skip" for item in report["tests"])
    report["summary"] = {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(report["tests"]),
    }
    report["ok"] = failed == 0 and skipped == 0
    return report


if __name__ == "__main__":
    print(json.dumps(run_integration_tests(), sort_keys=True))
