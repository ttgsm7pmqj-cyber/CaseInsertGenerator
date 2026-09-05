# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the CAD and GUI regression suites inside a FreeCAD GUI process."""

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sys
import traceback

import FreeCAD as App


ROOT = Path(__file__).resolve().parents[1]


def run(output_directory=None):
    """Write one report without exiting FreeCAD or closing user documents."""
    if not App.GuiUp:
        raise RuntimeError("CAD and GUI regression checks require the FreeCAD GUI")
    output = Path(output_directory or ROOT / "artifacts" / "regressions")
    output.mkdir(parents=True, exist_ok=True)
    from freecad.CaseInsertGenerator import engine
    if Path(engine.__file__).resolve() != ROOT / "freecad/CaseInsertGenerator/engine.py":
        raise RuntimeError("Regression checks imported a different add-on checkout")
    started = datetime.now(timezone.utc)
    run_directory = output / started.strftime("run-%Y%m%dT%H%M%S%fZ")
    run_directory.mkdir()
    report = {
        "started_at_utc": started.isoformat(),
        "run_directory": str(run_directory),
        "freecad_version": ".".join(App.Version()[:3]),
        "suites": {},
    }
    suites = (
        ("integration", "freecad_integration.py", "run_integration_tests", {}),
        ("validation", "cad_validation_regressions.py", "run", {}),
        ("recovery", "cad_recovery_regressions.py", "run", {}),
        ("gui_state", "gui_state_regressions.py", "run",
         {"output_directory": run_directory / "screenshots"}),
    )
    for name, filename, function, kwargs in suites:
        previous_path = list(sys.path)
        previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
        try:
            namespace = runpy.run_path(str(ROOT / "tests" / filename),
                                      run_name="cig_regression_" + name)
            result = namespace[function](**kwargs)
            summary = result["summary"]
            result["ok"] = (summary["failed"] == 0 and
                            summary.get("skipped", 0) == 0 and
                            summary["passed"] == summary["total"] and
                            summary["total"] > 0)
            report["suites"][name] = result
        except Exception as exc:
            report["suites"][name] = {
                "ok": False, "error": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            }
        finally:
            sys.path[:] = previous_path
            if previous_active and previous_active in App.listDocuments():
                App.setActiveDocument(previous_active)
        for directory in (output, run_directory):
            (directory / "results.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["ok"] = all(suite["ok"] for suite in report["suites"].values())
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    for directory in (output, run_directory):
        (directory / "results.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("CIG_REGRESSIONS=" + json.dumps(report, sort_keys=True))
    return report
