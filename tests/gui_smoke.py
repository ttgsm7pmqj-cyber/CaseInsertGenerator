#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Clean-profile GUI smoke check for the installed workbench package."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


EXPECTED_ROOT = Path(__file__).resolve().parents[1]
RESULT_PREFIX = "CIG_GUI_SMOKE="


def _finish(report, exit_code):
    result_path = EXPECTED_ROOT / "artifacts" / "gui-smoke-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(RESULT_PREFIX + json.dumps(report, sort_keys=True))
    application = QtWidgets.QApplication.instance()
    if application is not None:
        application.exit(exit_code)
    else:
        QtCore.QCoreApplication.exit(exit_code)


def _inspect(controller, startup_was_lazy):
    try:
        tabs = controller.workflow_tabs
        labels = [tabs.tabText(index) for index in range(tabs.count())]
        expected_labels = [
            "1  Case + fit",
            "2  Insert design",
            "3  Print + export",
        ]
        if labels != expected_labels:
            raise RuntimeError("The public dialog does not expose the expected three tabs")
        if not controller.dialog.isVisible():
            raise RuntimeError("The public dialog did not become visible")
        controller.dialog.resize(1040, 900)
        controller._set_case_selection("Custom Case")
        controller._load_case()
        controller.lid_envelope_source.setCurrentIndex(
            controller.lid_envelope_source.findData("measured"))
        controller.lid_length.setValue(300.0)
        controller.lid_width.setValue(200.0)
        controller.lid_clearance_source.setCurrentIndex(
            controller.lid_clearance_source.findData("unknown"))
        controller.mode_combo.setCurrentIndex(3)
        controller.panel_pattern.setCurrentIndex(
            controller.panel_pattern.findData("slot_grid"))
        tabs.setCurrentIndex(1)
        QtWidgets.QApplication.processEvents()
        gate_text = controller.lid_generation_gate.text()
        if "Closed-lid clearance is Unknown" not in gate_text:
            raise RuntimeError("The lid-panel page does not explain the unknown-clearance print block")
        pattern_values = [
            controller.panel_pattern.itemData(index)
            for index in range(controller.panel_pattern.count())
        ]
        if pattern_values != ["solid", "slot_grid", "perforated_grid"]:
            raise RuntimeError("The lid-panel page does not expose all three panel patterns")
        if controller.export_stl_button.isEnabled() or controller.export_step_button.isEnabled():
            raise RuntimeError("Printable exports remain enabled with unknown lid clearance")
        if "Preview configuration" not in controller.generate_button.text():
            raise RuntimeError("Unknown clearance does not switch generation to preview-only")
        if (not controller.mounting_advanced.isCheckable() or
                controller.mounting_advanced.isChecked()):
            raise RuntimeError("Detailed mounting controls are not collapsed under Advanced")
        screenshot = EXPECTED_ROOT / "artifacts" / "gui-smoke.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        pixmap = controller.dialog.grab()
        if not pixmap.save(str(screenshot), "PNG"):
            raise RuntimeError("Could not save the GUI smoke screenshot")
        controls_screenshot = EXPECTED_ROOT / "artifacts" / "gui-lid-panel-controls.png"
        if not pixmap.save(str(controls_screenshot), "PNG"):
            raise RuntimeError("Could not save the lid-panel controls screenshot")
        if screenshot.stat().st_size <= 1024:
            raise RuntimeError("GUI smoke screenshot is unexpectedly small")
        controller.lid_clearance_source.setCurrentIndex(
            controller.lid_clearance_source.findData("measured"))
        controller.lid_clearance.setValue(22.0)
        controller._update_lid_generation_gate()
        QtWidgets.QApplication.processEvents()
        if "Ready for printable generation" not in controller.lid_generation_gate.text():
            raise RuntimeError("Measured clearance did not unlock printable generation")
        if (not controller.export_stl_button.isEnabled() or
                not controller.export_step_button.isEnabled()):
            raise RuntimeError("Measured clearance did not enable printable exports")
        controller._generate()
        QtWidgets.QApplication.processEvents()
        document = App.ActiveDocument
        results = [] if document is None else [
            obj for obj in document.Objects
            if str(getattr(obj, "CaseInsertGeneratorRole", "") or "") == "result"
        ]
        if not results:
            raise RuntimeError("Measured-clearance GUI generation created no printable parts")
        if any(
                getattr(obj, "Shape", None) is None or obj.Shape.isNull() or
                not obj.Shape.isValid() or obj.Shape.Volume <= 0.0
                for obj in results):
            raise RuntimeError("Measured-clearance GUI generation created an invalid part")
        report = {
            "ok": True,
            "workbench": "CaseInsertGeneratorWorkbench",
            "startup_engine_import_was_lazy": startup_was_lazy,
            "tab_count": tabs.count(),
            "tab_labels": labels,
            "dialog_visible": True,
            "lid_panel_patterns": pattern_values,
            "lid_clearance_explanation": gate_text,
            "print_exports_blocked": True,
            "advanced_mounting_collapsed": True,
            "measured_clearance_generation": True,
            "measured_result_count": len(results),
            "measured_result_names": [str(obj.Name) for obj in results],
            "screenshot": "artifacts/gui-smoke.png",
            "screenshot_bytes": screenshot.stat().st_size,
            "lid_panel_controls_screenshot": "artifacts/gui-lid-panel-controls.png",
            "lid_panel_controls_screenshot_bytes": controls_screenshot.stat().st_size,
        }
        controller.dialog.close()
        _finish(report, 0)
    except Exception as error:
        _finish(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc().splitlines(),
            },
            1,
        )


def _start():
    try:
        workbenches = Gui.listWorkbenches()
        if "CaseInsertGeneratorWorkbench" not in workbenches:
            raise RuntimeError("Case Insert Generator workbench was not registered")
        startup_was_lazy = "freecad.CaseInsertGenerator.engine" not in sys.modules
        if not startup_was_lazy:
            raise RuntimeError("Workbench startup imported the geometry engine eagerly")
        Gui.activateWorkbench("CaseInsertGeneratorWorkbench")
        from freecad.CaseInsertGenerator import bridge

        imported_root = Path(bridge.addon_directory()).resolve()
        if imported_root != EXPECTED_ROOT:
            raise RuntimeError("GUI smoke check imported a different add-on checkout")
        controller = bridge.show_dialog()
        QtCore.QTimer.singleShot(500, lambda: _inspect(controller, startup_was_lazy))
    except Exception as error:
        _finish(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc().splitlines(),
            },
            1,
        )


QtCore.QTimer.singleShot(0, _start)
