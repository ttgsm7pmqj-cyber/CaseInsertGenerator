# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run review regressions inside FreeCAD with ``run()``; never launch the app.

The caller loads this checkout's engine. All geometry uses synthetic fixtures
and only documents created by this runner are closed. The report separates
contract results from the number of cases exercised within each contract.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
from pathlib import Path
import runpy
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[1]


def run():
    import FreeCAD as App

    engine = importlib.import_module("freecad.CaseInsertGenerator.engine")
    if Path(engine.__file__).resolve() != ROOT / "freecad/CaseInsertGenerator/engine.py":
        raise RuntimeError("CAD validation runner imported another engine checkout")
    support = runpy.run_path(str(ROOT / "tests/freecad_integration.py"))
    base_spec = support["_base_spec"]
    object_spec = support["_object"]
    previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
    created_documents = []
    tests = []

    def new_document():
        doc = App.newDocument("CIGValidationRegression")
        created_documents.append(doc.Name)
        return doc

    def snapshot(doc):
        return [
            (
                item.Name, item.TypeId,
                # OpenCascade hashCode includes topology identity, which can
                # change when FreeCAD refreshes a shape wrapper. Compare the
                # saved geometry content for this preservation contract.
                hashlib.sha256(item.Shape.exportBrepToString().encode("utf-8")).hexdigest()
                if hasattr(item, "Shape") else None,
                getattr(item, "ProjectJSON", None),
                getattr(item, "ParameterJSON", None),
            )
            for item in doc.Objects
        ]

    def expect_unchanged_failure(doc, spec, message, generator=None):
        before = snapshot(doc)
        try:
            (generator or engine.generate_project)(spec, document=doc)
        except ValueError as exc:
            assert message in str(exc), str(exc)
        else:
            raise AssertionError("Invalid geometry input was accepted")
        assert snapshot(doc) == before, "Rejected input changed the prior editable model"

    with tempfile.TemporaryDirectory(prefix="cig-validation-") as directory:
        scratch = Path(directory)

        def svg_project(body, name):
            path = scratch / (name + ".svg")
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'width="20mm" height="10mm" viewBox="0 0 20 10">'
                + body + "</svg>", encoding="utf-8",
            )
            return base_spec([object_spec(
                "svg-pocket", "svg_pocket", 20, 20, 10, 20,
                extra={"svg_path": str(path), "scale": 1.0},
            )])

        def svg_effects_rejected_before_replacement():
            doc = new_document()
            engine.generate_project(base_spec(), document=doc)
            cases = 0
            for effect in ("clip-path", "mask", "filter"):
                for kind in ("group", "inline"):
                    rect = '<rect width="20" height="10"/>'
                    body = (
                        f'<g {effect}="url(#cut)">{rect}</g>' if kind == "group" else
                        f'<rect width="20" height="10" style="{effect}:url(#cut)"/>'
                    )
                    spec = svg_project(body, effect + "-" + kind)
                    expect_unchanged_failure(doc, spec, "UNSUPPORTED_GEOMETRY_EFFECT")
                    cases += 1
            return {"cases": cases, "prior_model_preserved": True}

        def ordinary_svg_remains_importable():
            doc = new_document()
            spec = svg_project('<rect width="20" height="10"/>', "ordinary")
            result = engine.generate_project(spec, document=doc)
            assert result["valid"] and result["parts"] == 1
            faces, open_count = engine._import_svg_faces(
                spec["objects"][0]["svg_path"], 1.0, 0.0, 0.0, 0.0, 0.0,
            )
            area = sum(face.Area for face in faces)
            assert abs(area - 200.0) < 1e-5, area
            assert open_count == 0
            return {"face_area_mm2": area, "parts": result["parts"]}

        def invalid_numbers_and_metadata_preserve_the_model():
            doc = new_document()
            engine.generate_project(base_spec(), document=doc)
            spec = svg_project('<path d="M0 0 L1e309 0 L10 10 Z"/>', "nonfinite")
            expect_unchanged_failure(doc, spec, "finite")
            for name, body in (
                ("transformed-overflow",
                 '<rect x="1e308" width="10" height="5" transform="scale(10)"/>'),
                ("control-overflow",
                 '<path d="M1e308 0 c1e308 0 1e308 10 0 10 L0 10 L0 0 Z"/>'),
            ):
                expect_unchanged_failure(doc, svg_project(body, name), "finite")
            spec = base_spec([object_spec(
                "circle", "circular_pocket", 20, 20, 20, 20,
                extra={"diameter": float("nan")},
            )])
            expect_unchanged_failure(doc, spec, "objects[0].diameter")
            spec = base_spec()
            spec["case"]["internal_length"] = 10 ** 400
            expect_unchanged_failure(doc, spec, "case.internal_length")

            lid_spec = support["_lid_panel_spec"]()
            engine.generate_lid_panel_project(lid_spec, document=doc)
            invalid_lid = copy.deepcopy(lid_spec)
            invalid_lid["verification"] = []
            expect_unchanged_failure(
                doc, invalid_lid, "verification must be a mapping",
                engine.generate_lid_panel_project,
            )
            return {"cases": 6, "prior_model_preserved": True}

        def containment_covers_only_supported_storage():
            doc = new_document()
            cases = []
            for kind in ("removable_bin", "existing_container_bay", "divider_region"):
                for mode in ("none", "individual_lids", "shared_panel"):
                    for clearance in (None, 4.0):
                        spec = base_spec([object_spec(
                            "storage", kind, 25, 25, 35, 45, height=15,
                            extra={"wall": 1.8, "rows": 2, "columns": 2},
                        )], containment=mode)
                        if clearance is not None:
                            spec["lid"] = {"source": "measured", "clearance_mm": clearance}
                        result = engine.generate_project(spec, document=doc)
                        assert result["valid"], (kind, mode, clearance)
                        uncovered = mode == "none" or (
                            mode == "individual_lids" and kind != "removable_bin"
                        )
                        warnings = [item for item in result["warnings"] if "no containment" in item]
                        assert len(warnings) == int(uncovered), (kind, mode, warnings)
                        if uncovered:
                            assert "'storage'" in warnings[0]
                            assert kind.replace("_", " ") in warnings[0]
                        result_names = result["results"]
                        assert any("Lid_" in name for name in result_names) == (
                            kind == "removable_bin" and mode == "individual_lids"
                        ), result_names
                        cases.append({
                            "type": kind, "mode": mode, "clearance_mm": clearance,
                            "warning_count": len(warnings), "parts": result["parts"],
                        })
            return {"cases": cases}

        def mixed_bins_and_dividers_keep_the_uncovered_warning():
            doc = new_document()
            spec = base_spec([
                object_spec("bin", "removable_bin", 20, 25, 30, 40, height=15),
                object_spec("divider", "divider_region", 90, 25, 30, 40, height=15),
            ], containment="individual_lids")
            result = engine.generate_project(spec, document=doc)
            warnings = [item for item in result["warnings"] if "no containment" in item]
            assert result["valid"] and result["parts"] == 3, result
            assert len(warnings) == 1 and "'divider'" in warnings[0], warnings
            return {"warnings": warnings, "parts": result["parts"]}

        try:
            for test in (
                svg_effects_rejected_before_replacement,
                ordinary_svg_remains_importable,
                invalid_numbers_and_metadata_preserve_the_model,
                containment_covers_only_supported_storage,
                mixed_bins_and_dividers_keep_the_uncovered_warning,
            ):
                try:
                    tests.append({"name": test.__name__, "status": "pass", "detail": test()})
                except Exception as exc:
                    tests.append({
                        "name": test.__name__, "status": "fail", "message": str(exc),
                        "traceback": traceback.format_exc().splitlines(),
                    })
        finally:
            for name in created_documents:
                if name in App.listDocuments():
                    App.closeDocument(name)
            if previous_active and previous_active in App.listDocuments():
                App.setActiveDocument(previous_active)

    failed = sum(item["status"] == "fail" for item in tests)
    return {
        "ok": failed == 0,
        "summary": {"passed": len(tests) - failed, "failed": failed, "total": len(tests)},
        "tests": tests,
    }
