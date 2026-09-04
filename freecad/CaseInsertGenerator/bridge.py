# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stable namespaced API for the Case Insert Generator engine."""

import os


def addon_directory():
    """Return the installed add-on root."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def icon_path():
    """Return the absolute workbench icon path."""
    return os.path.join(
        addon_directory(), "Resources", "icons", "CaseInsertGenerator.svg"
    )


def load_engine():
    """Import the geometry engine only when a command is invoked."""
    from . import engine

    return engine


def show_dialog():
    """Open the generator dialog."""
    return load_engine().show_dialog()


def generate_insert(params, document=None):
    """Generate an insert through the stable compatibility API."""
    return load_engine().generate_insert(params, document=document)


def generate_project(spec, document=None):
    """Generate a schema-v1 composed project."""
    return load_engine().generate_project(spec, document=document)


def generate_lid_panel_project(spec, document=None):
    """Generate an evidenced schema-v1 inside-lid panel project."""
    return load_engine().generate_lid_panel_project(spec, document=document)


def preview_lid_panel_project(spec, document=None):
    """Store a non-printable lid-panel configuration preview."""
    return load_engine().preview_lid_panel_project(spec, document=document)


def load_project(document=None):
    """Load editable schema-v1 data stored in an FCStd document."""
    return load_engine().load_project(document=document)


def load_case_catalog(path=None):
    """Load the bundled synthetic preset catalog."""
    return load_engine().load_case_catalog(path)
