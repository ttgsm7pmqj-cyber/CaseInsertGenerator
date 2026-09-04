# SPDX-License-Identifier: LGPL-2.1-or-later

"""Public Python entry points for the Case Insert Generator add-on."""

from .project_model import (
    GenerationResult,
    LID_HINGE_EDGES,
    LID_PANEL_ORIENTATIONS,
    LID_PANEL_PATTERNS,
    LayoutResult,
    ProjectValidationError,
    generate_layouts,
    lid_panel_height_budget,
    lid_panel_plan,
    layout_project,
    validate_project,
)


def _bridge():
    from . import bridge

    return bridge


def generate_insert(params, document=None):
    return _bridge().generate_insert(params, document=document)


def generate_project(spec, document=None):
    return _bridge().generate_project(spec, document=document)


def generate_lid_panel_project(spec, document=None):
    return _bridge().generate_lid_panel_project(spec, document=document)


def preview_lid_panel_project(spec, document=None):
    return _bridge().preview_lid_panel_project(spec, document=document)


def load_case_catalog(path=None):
    return _bridge().load_case_catalog(path)


def load_project(document=None):
    return _bridge().load_project(document=document)


def load_engine():
    return _bridge().load_engine()


def show_dialog():
    return _bridge().show_dialog()


__all__ = [
    "generate_insert",
    "generate_lid_panel_project",
    "generate_project",
    "generate_layouts",
    "GenerationResult",
    "LID_HINGE_EDGES",
    "LID_PANEL_ORIENTATIONS",
    "LID_PANEL_PATTERNS",
    "LayoutResult",
    "ProjectValidationError",
    "layout_project",
    "lid_panel_height_budget",
    "lid_panel_plan",
    "load_case_catalog",
    "load_project",
    "load_engine",
    "preview_lid_panel_project",
    "show_dialog",
    "validate_project",
]
__version__ = "0.1.0"
