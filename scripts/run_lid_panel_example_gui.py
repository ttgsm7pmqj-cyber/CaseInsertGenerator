# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the lid-panel example renderer after FreeCAD's GUI event loop starts."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import traceback


ROOT = Path(__file__).resolve().parents[1]


def _qt_modules():
    try:
        from PySide import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


def _generate_and_quit():
    QtCore, QtWidgets = _qt_modules()
    exit_code = 1
    try:
        namespace = runpy.run_path(
            str(ROOT / "scripts" / "generate_lid_panel_example.py"),
            run_name="cig_lid_panel_example_gui_generation")
        report = namespace["generate_lid_panel_example"](ROOT, render=True)
        print("CIG_LID_PANEL_EXAMPLE=" + json.dumps({
            "ok": report["ok"],
            "parts": report["printable_parts"],
            "pattern_count": report["pattern_count"],
        }, sort_keys=True))
        exit_code = 0 if report["ok"] else 1
    except Exception:
        traceback.print_exc()
    finally:
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.exit(exit_code)
        else:
            QtCore.QCoreApplication.exit(exit_code)


def schedule():
    QtCore, _QtWidgets = _qt_modules()
    QtCore.QTimer.singleShot(0, _generate_and_quit)


schedule()
