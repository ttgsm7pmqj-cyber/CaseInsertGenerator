# SPDX-License-Identifier: LGPL-2.1-or-later

"""FreeCAD startup hook for the namespaced Case Insert Generator workbench."""

import os

import FreeCADGui


_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_ICON_PATH = os.path.join(
    _ADDON_ROOT, "Resources", "icons", "CaseInsertGenerator.svg")


class CaseInsertGeneratorWorkbench(FreeCADGui.Workbench):
    """Workbench registration for the local generator engine."""

    MenuText = "Case Insert Generator"
    ToolTip = "Generate fitted case inserts and printable storage layouts"
    Icon = _ICON_PATH

    def Initialize(self):
        from .commands import COMMAND_NAME

        self.appendToolbar("Case Insert Generator", [COMMAND_NAME])
        self.appendMenu("Case Insert Generator", [COMMAND_NAME])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(CaseInsertGeneratorWorkbench())
