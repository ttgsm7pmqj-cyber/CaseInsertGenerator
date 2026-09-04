# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI commands contributed by the Case Insert Generator workbench."""

import os

import FreeCADGui


COMMAND_NAME = "CaseInsertGenerator_Open"
_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_ICON_PATH = os.path.join(
    _ADDON_ROOT, "Resources", "icons", "CaseInsertGenerator.svg")


class OpenGeneratorCommand:
    """Open the generator's existing local-only dialog."""

    def GetResources(self):
        return {
            "Pixmap": _ICON_PATH,
            "MenuText": "Open Case Insert Generator",
            "ToolTip": "Create fitted case inserts and printable storage layouts",
        }

    def Activated(self):
        from .bridge import show_dialog

        return show_dialog()

    def IsActive(self):
        return True


FreeCADGui.addCommand(COMMAND_NAME, OpenGeneratorCommand())
