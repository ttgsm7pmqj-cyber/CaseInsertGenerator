# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned project data and deterministic layout planning.

This dependency-free module is the stable boundary between the UI, saved JSON,
and later FreeCAD geometry. Schema version 1 uses millimetres. Case length and
object length run on X; case width and object width run on Y before rotation.
Object ``x`` and ``y`` identify the lower-left of its rotated, axis-aligned
planning footprint.

The planner operates on bounding rectangles only. FreeCAD geometry remains a
separate concern and must independently validate generated solids.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = 1
OBJECT_TYPES = (
    "svg_pocket",
    "circular_pocket",
    "rectangular_pocket",
    "removable_bin",
    "existing_container_bay",
    "divider_region",
)
LID_EVIDENCE_STATES = ("measured", "cad-derived", "unknown")
LID_PANEL_PATTERNS = ("solid", "slot_grid", "perforated_grid")
LID_PANEL_ORIENTATIONS = ("horizontal", "vertical")
LID_HINGE_EDGES = ("top", "bottom", "left", "right")
CONTAINMENT_MODES = ("none", "shared_panel", "individual_lids")
LAYOUT_STRATEGIES = ("balanced", "maximum_capacity", "fewest_layers")
OBJECT_LAYERS = ("lower", "upper")

_EPSILON = 1e-9
_STABLE_OBJECT_FIELDS = (
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
)
_STABLE_CASE_FIELDS = (
    "case_model",
    "internal_length",
    "internal_width",
    "insert_depth",
    "corner_radius",
    "side_clearance",
    "bottom_clearance",
    "taper_allowance",
    "layout_inset",
)
_PERSISTED_PROJECT_FIELDS = (
    "result",
    "results",
    "parts",
    "warnings",
    "unplaced",
    "layout_strategy",
    "lid_panel_report",
    "verification",
)
_LOOSE_STORAGE_TYPES = {
    "removable_bin",
    "existing_container_bay",
    "divider_region",
}


def default_lid_panel() -> dict[str, Any]:
    """Return the complete schema-v1 inside-lid panel defaults.

    The settings are present even when the feature is disabled so a project can
    be configured before its physical evidence is complete, saved, and resumed
    without losing any panel choices.
    """

    return {
        "enabled": False,
        "pattern": "solid",
        "thickness_mm": 3.0,
        "payload_thickness_mm": 0.0,
        "edge_inset_mm": 4.0,
        "corner_radius_mm": 6.0,
        "slot_grid": {
            "slot_length_mm": 25.0,
            "slot_width_mm": 4.0,
            "pitch_x_mm": 38.0,
            "pitch_y_mm": 25.0,
            "margin_x_mm": 10.0,
            "margin_y_mm": 10.0,
            "orientation": "horizontal",
        },
        "perforated_grid": {
            "diameter_mm": 5.0,
            "pitch_x_mm": 12.0,
            "pitch_y_mm": 12.0,
            "margin_x_mm": 10.0,
            "margin_y_mm": 10.0,
        },
        "keepouts": {
            "rim_mm": 4.0,
            "seal_mm": 2.0,
            "hinge_mm": 12.0,
            "hinge_edge": "top",
            "clearance_margin_mm": 3.0,
            "rectangles": [],
        },
        "mounting": {
            "perimeter_enabled": True,
            "retainers_enabled": True,
            "retainer_count": 4,
            "retainer_width_mm": 10.0,
            "retainer_projection_mm": 3.0,
            "retainer_clearance_mm": 0.35,
            "lift_access_enabled": True,
            "lift_access_diameter_mm": 18.0,
            "fastener_holes_enabled": False,
            "fastener_hole_diameter_mm": 3.5,
            "fastener_edge_offset_mm": 12.0,
            "custom_fastener_holes": [],
        },
        "splitting": {
            "keyed_alignment": True,
            "key_size_mm": 8.0,
            "key_clearance_mm": 0.25,
        },
    }


@dataclass(frozen=True)
class ValidationIssue:
    """One stable, machine-readable validation failure."""

    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


class ProjectValidationError(ValueError):
    """Raised when external data does not satisfy project schema version 1."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in self.issues
        )
        super().__init__(f"Invalid project specification: {details}")


@dataclass(frozen=True)
class Placement:
    """Resolved placement for one object's rectangular planning envelope."""

    object_id: str
    object_type: str
    name: str
    x: float
    y: float
    rotation: float
    layer: str
    width: float
    length: float
    height: float
    locked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_type": self.object_type,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "rotation": self.rotation,
            "layer": self.layer,
            "width": self.width,
            "length": self.length,
            "height": self.height,
            "locked": self.locked,
        }


@dataclass(frozen=True)
class UnplacedObject:
    """One object the planner could not place, with an actionable reason."""

    object_id: str
    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "code": self.code,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LayoutResult:
    """Pure-data result for one deterministic layout strategy."""

    strategy: str
    placements: tuple[Placement, ...]
    unplaced: tuple[UnplacedObject, ...]
    warnings: tuple[str, ...]
    layer_heights: tuple[tuple[str, float], ...]

    @property
    def placed_count(self) -> int:
        return len(self.placements)

    @property
    def unplaced_count(self) -> int:
        return len(self.unplaced)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable value with stable public field names."""

        return {
            "strategy": self.strategy,
            "placements": [placement.to_dict() for placement in self.placements],
            "unplaced": [item.to_dict() for item in self.unplaced],
            "warnings": list(self.warnings),
            "layer_heights": dict(self.layer_heights),
            "placed_count": self.placed_count,
            "unplaced_count": self.unplaced_count,
        }


_GENERATION_RESULT_FIELDS = (
    "document",
    "results",
    "parts",
    "mode",
    "valid",
    "solids",
    "volume",
    "warnings",
    "unplaced",
    "project",
)


@dataclass(frozen=True)
class GenerationResult(Mapping[str, Any]):
    """Geometry-generation report returned by ``generate_project``.

    The object behaves like a read-only mapping for compatibility with callers
    of the legacy dictionary API. ``from_mapping`` defensively snapshots the
    report, and ``to_mapping`` returns a fresh JSON-compatible copy.
    """

    document: str
    results: tuple[str, ...]
    parts: int
    mode: str
    valid: bool
    solids: int
    volume: float
    warnings: tuple[str, ...]
    unplaced: tuple[Any, ...]
    project: Mapping[str, Any]
    _extra: tuple[tuple[str, Any], ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def from_mapping(cls, report: Mapping[str, Any]) -> "GenerationResult":
        """Create an immutable report snapshot from macro output."""

        if not isinstance(report, Mapping):
            raise TypeError("Generation report must be a mapping.")
        document = _report_text(report, "document")
        results = _report_text_sequence(report, "results")
        parts = _report_integer(report, "parts", minimum=0)
        mode = _report_text(report, "mode")
        valid = _report_boolean(report, "valid")
        solids = _report_integer(report, "solids", minimum=0)
        volume = _report_number(report, "volume", minimum=0.0)
        warnings = _report_text_sequence(report, "warnings")
        unplaced_source = _report_sequence(report, "unplaced")
        project_source = report.get("project")
        if not isinstance(project_source, Mapping):
            raise ValueError("Generation report field 'project' must be a mapping.")
        extra = tuple(
            (key, _freeze_value(report[key]))
            for key in sorted(report)
            if key not in _GENERATION_RESULT_FIELDS
        )
        return cls(
            document=document,
            results=results,
            parts=parts,
            mode=mode,
            valid=valid,
            solids=solids,
            volume=volume,
            warnings=warnings,
            unplaced=tuple(_freeze_value(item) for item in unplaced_source),
            project=_freeze_value(project_source),
            _extra=extra,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a fresh mapping with the legacy geometry-report shape."""

        result = {
            "document": self.document,
            "results": list(self.results),
            "parts": self.parts,
            "mode": self.mode,
            "valid": self.valid,
            "solids": self.solids,
            "volume": self.volume,
            "warnings": list(self.warnings),
            "unplaced": [_thaw_value(item) for item in self.unplaced],
            "project": _thaw_value(self.project),
        }
        result.update({key: _thaw_value(value) for key, value in self._extra})
        return result

    def to_dict(self) -> dict[str, Any]:
        """Compatibility spelling for consumers that expect ``to_dict``."""

        return self.to_mapping()

    def __getitem__(self, key: str) -> Any:
        known_keys = _GENERATION_RESULT_FIELDS + tuple(
            extra_key for extra_key, _ in self._extra
        )
        if key not in known_keys:
            raise KeyError(key)
        return self.to_mapping()[key]

    def __iter__(self):
        yield from _GENERATION_RESULT_FIELDS
        yield from (key for key, _ in self._extra)

    def __len__(self) -> int:
        return len(_GENERATION_RESULT_FIELDS) + len(self._extra)


def validate_project(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a schema-version-1 project.

    The result is a deep copy in canonical field order. Type-specific object
    fields are retained after the stable fields. Every boundary failure is
    collected in one :class:`ProjectValidationError` for inline UI reporting.
    """

    issues: list[ValidationIssue] = []
    if not isinstance(spec, Mapping):
        raise ProjectValidationError(
            (
                ValidationIssue(
                    path="$",
                    code="invalid_project",
                    message="Project data must be a mapping.",
                ),
            )
        )

    version = spec.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        issues.append(
            ValidationIssue(
                path="schema_version",
                code="unsupported_schema_version",
                message=f"Expected schema version {SCHEMA_VERSION}; received {version!r}.",
            )
        )

    case_source = _mapping_field(spec, "case", "case", issues)
    case_model = _text_field(
        case_source, "case_model", "case.case_model", issues
    )
    internal_length = _number_field(
        case_source,
        "internal_length",
        "case.internal_length",
        issues,
        minimum=0.0,
        exclusive=True,
    )
    internal_width = _number_field(
        case_source,
        "internal_width",
        "case.internal_width",
        issues,
        minimum=0.0,
        exclusive=True,
    )
    insert_depth_source = case_source.get("insert_depth")
    if insert_depth_source in (None, ""):
        insert_depth: float | None = None
        if case_model == "Custom Case":
            issues.append(
                ValidationIssue(
                    path="case.insert_depth",
                    code="required_for_custom_case",
                    message="Custom cases require a measured insert depth.",
                )
            )
    else:
        insert_depth = _coerce_number(
            insert_depth_source,
            "case.insert_depth",
            issues,
            minimum=0.0,
            exclusive=True,
        )
    corner_radius = _number_field(
        case_source,
        "corner_radius",
        "case.corner_radius",
        issues,
        default=0.0,
        minimum=0.0,
    )
    side_clearance = _number_field(
        case_source,
        "side_clearance",
        "case.side_clearance",
        issues,
        default=0.0,
        minimum=0.0,
    )
    bottom_clearance = _number_field(
        case_source,
        "bottom_clearance",
        "case.bottom_clearance",
        issues,
        default=0.0,
        minimum=0.0,
    )
    taper_allowance = _number_field(
        case_source,
        "taper_allowance",
        "case.taper_allowance",
        issues,
        default=0.0,
        minimum=0.0,
    )
    layout_inset = _number_field(
        case_source,
        "layout_inset",
        "case.layout_inset",
        issues,
        default=0.0,
        minimum=0.0,
    )
    case_extra_fields = {
        key: copy.deepcopy(case_source[key])
        for key in sorted(case_source)
        if key not in _STABLE_CASE_FIELDS
    }
    per_side_clearance = side_clearance + taper_allowance
    profile_length = internal_length - (2.0 * per_side_clearance)
    profile_width = internal_width - (2.0 * per_side_clearance)
    usable_length = profile_length - (2.0 * layout_inset)
    usable_width = profile_width - (2.0 * layout_inset)
    if usable_length <= 0.0 or usable_width <= 0.0:
        issues.append(
            ValidationIssue(
                path="case.side_clearance",
                code="no_usable_case_area",
                message=(
                    "Side clearance plus taper allowance leaves no usable case area."
                ),
            )
        )
    if corner_radius > min(profile_length, profile_width) / 2.0 + _EPSILON:
        issues.append(
            ValidationIssue(
                path="case.corner_radius",
                code="corner_radius_exceeds_case",
                message="Corner radius cannot exceed half the usable short side.",
            )
        )

    lid_source = _mapping_field(spec, "lid", "lid", issues)
    lid_evidence = lid_source.get("source", "unknown")
    if lid_evidence not in LID_EVIDENCE_STATES:
        issues.append(
            ValidationIssue(
                path="lid.source",
                code="invalid_lid_evidence",
                message=(
                    "Lid source must be measured, cad-derived, or unknown; "
                    f"received {lid_evidence!r}."
                ),
            )
        )
        lid_evidence = "unknown"
    lid_clearance_source = lid_source.get("clearance_mm")
    lid_clearance: float | None
    if lid_evidence == "unknown":
        lid_clearance = None
        if lid_clearance_source is not None:
            issues.append(
                ValidationIssue(
                    path="lid.clearance_mm",
                    code="clearance_requires_evidence",
                    message=(
                        "Unknown lid evidence cannot claim usable clearance; "
                        "measure it or derive it from CAD first."
                    ),
                )
            )
    elif lid_clearance_source is None:
        lid_clearance = None
        issues.append(
            ValidationIssue(
                path="lid.clearance_mm",
                code="required_for_known_lid",
                message=(
                    f"A numeric clearance is required when lid source is {lid_evidence}."
                ),
            )
        )
    else:
        lid_clearance = _coerce_number(
            lid_clearance_source,
            "lid.clearance_mm",
            issues,
            minimum=0.0,
        )

    envelope_evidence = lid_source.get("envelope_source", "unknown")
    if envelope_evidence not in LID_EVIDENCE_STATES:
        issues.append(
            ValidationIssue(
                path="lid.envelope_source",
                code="invalid_lid_envelope_evidence",
                message=(
                    "Lid envelope source must be measured, cad-derived, or "
                    f"unknown; received {envelope_evidence!r}."
                ),
            )
        )
        envelope_evidence = "unknown"
    lid_length_source = lid_source.get("length_mm")
    lid_width_source = lid_source.get("width_mm")
    lid_length = (
        None
        if lid_length_source in (None, "")
        else _coerce_number(
            lid_length_source,
            "lid.length_mm",
            issues,
            minimum=0.0,
            exclusive=True,
        )
    )
    lid_width = (
        None
        if lid_width_source in (None, "")
        else _coerce_number(
            lid_width_source,
            "lid.width_mm",
            issues,
            minimum=0.0,
            exclusive=True,
        )
    )
    if envelope_evidence != "unknown":
        if lid_length is None:
            issues.append(
                ValidationIssue(
                    "lid.length_mm",
                    "required_for_known_lid_envelope",
                    "A numeric lid-panel length is required for evidenced lid geometry.",
                )
            )
        if lid_width is None:
            issues.append(
                ValidationIssue(
                    "lid.width_mm",
                    "required_for_known_lid_envelope",
                    "A numeric lid-panel width is required for evidenced lid geometry.",
                )
            )

    panel_defaults = default_lid_panel()
    lid_panel_source = _mapping_field(spec, "lid_panel", "lid_panel", issues)
    lid_panel_enabled = _bool_field(
        lid_panel_source,
        "enabled",
        "lid_panel.enabled",
        issues,
        default=panel_defaults["enabled"],
    )
    lid_panel_pattern = lid_panel_source.get(
        "pattern", panel_defaults["pattern"]
    )
    if lid_panel_pattern not in LID_PANEL_PATTERNS:
        issues.append(
            ValidationIssue(
                "lid_panel.pattern",
                "invalid_lid_panel_pattern",
                "Panel pattern must be solid, slot_grid, or perforated_grid.",
            )
        )
        lid_panel_pattern = panel_defaults["pattern"]
    lid_panel_thickness = _number_field(
        lid_panel_source,
        "thickness_mm",
        "lid_panel.thickness_mm",
        issues,
        default=panel_defaults["thickness_mm"],
        minimum=1.2,
    )
    payload_thickness = _number_field(
        lid_panel_source,
        "payload_thickness_mm",
        "lid_panel.payload_thickness_mm",
        issues,
        default=panel_defaults["payload_thickness_mm"],
        minimum=0.0,
    )
    edge_inset = _number_field(
        lid_panel_source,
        "edge_inset_mm",
        "lid_panel.edge_inset_mm",
        issues,
        default=panel_defaults["edge_inset_mm"],
        minimum=0.0,
    )
    panel_corner_radius = _number_field(
        lid_panel_source,
        "corner_radius_mm",
        "lid_panel.corner_radius_mm",
        issues,
        default=panel_defaults["corner_radius_mm"],
        minimum=0.0,
    )

    slot_defaults = panel_defaults["slot_grid"]
    slot_source = _mapping_field(
        lid_panel_source, "slot_grid", "lid_panel.slot_grid", issues
    )
    slot_length = _number_field(
        slot_source,
        "slot_length_mm",
        "lid_panel.slot_grid.slot_length_mm",
        issues,
        default=slot_defaults["slot_length_mm"],
        minimum=0.0,
        exclusive=True,
    )
    slot_width = _number_field(
        slot_source,
        "slot_width_mm",
        "lid_panel.slot_grid.slot_width_mm",
        issues,
        default=slot_defaults["slot_width_mm"],
        minimum=0.0,
        exclusive=True,
    )
    slot_pitch_x = _number_field(
        slot_source,
        "pitch_x_mm",
        "lid_panel.slot_grid.pitch_x_mm",
        issues,
        default=slot_defaults["pitch_x_mm"],
        minimum=0.0,
        exclusive=True,
    )
    slot_pitch_y = _number_field(
        slot_source,
        "pitch_y_mm",
        "lid_panel.slot_grid.pitch_y_mm",
        issues,
        default=slot_defaults["pitch_y_mm"],
        minimum=0.0,
        exclusive=True,
    )
    slot_margin_x = _number_field(
        slot_source,
        "margin_x_mm",
        "lid_panel.slot_grid.margin_x_mm",
        issues,
        default=slot_defaults["margin_x_mm"],
        minimum=0.0,
    )
    slot_margin_y = _number_field(
        slot_source,
        "margin_y_mm",
        "lid_panel.slot_grid.margin_y_mm",
        issues,
        default=slot_defaults["margin_y_mm"],
        minimum=0.0,
    )
    slot_orientation = slot_source.get(
        "orientation", slot_defaults["orientation"]
    )
    if slot_orientation not in LID_PANEL_ORIENTATIONS:
        issues.append(
            ValidationIssue(
                "lid_panel.slot_grid.orientation",
                "invalid_slot_orientation",
                "Slot orientation must be horizontal or vertical.",
            )
        )
        slot_orientation = slot_defaults["orientation"]
    realised_slot_x = slot_length if slot_orientation == "horizontal" else slot_width
    realised_slot_y = slot_width if slot_orientation == "horizontal" else slot_length
    if slot_pitch_x + _EPSILON < realised_slot_x:
        issues.append(
            ValidationIssue(
                "lid_panel.slot_grid.pitch_x_mm",
                "slot_pitch_too_small",
                "Slot X pitch must be at least the oriented slot X size.",
            )
        )
    if slot_pitch_y + _EPSILON < realised_slot_y:
        issues.append(
            ValidationIssue(
                "lid_panel.slot_grid.pitch_y_mm",
                "slot_pitch_too_small",
                "Slot Y pitch must be at least the oriented slot Y size.",
            )
        )

    perforation_defaults = panel_defaults["perforated_grid"]
    perforation_source = _mapping_field(
        lid_panel_source,
        "perforated_grid",
        "lid_panel.perforated_grid",
        issues,
    )
    perforation_diameter = _number_field(
        perforation_source,
        "diameter_mm",
        "lid_panel.perforated_grid.diameter_mm",
        issues,
        default=perforation_defaults["diameter_mm"],
        minimum=0.0,
        exclusive=True,
    )
    perforation_pitch_x = _number_field(
        perforation_source,
        "pitch_x_mm",
        "lid_panel.perforated_grid.pitch_x_mm",
        issues,
        default=perforation_defaults["pitch_x_mm"],
        minimum=0.0,
        exclusive=True,
    )
    perforation_pitch_y = _number_field(
        perforation_source,
        "pitch_y_mm",
        "lid_panel.perforated_grid.pitch_y_mm",
        issues,
        default=perforation_defaults["pitch_y_mm"],
        minimum=0.0,
        exclusive=True,
    )
    perforation_margin_x = _number_field(
        perforation_source,
        "margin_x_mm",
        "lid_panel.perforated_grid.margin_x_mm",
        issues,
        default=perforation_defaults["margin_x_mm"],
        minimum=0.0,
    )
    perforation_margin_y = _number_field(
        perforation_source,
        "margin_y_mm",
        "lid_panel.perforated_grid.margin_y_mm",
        issues,
        default=perforation_defaults["margin_y_mm"],
        minimum=0.0,
    )
    if perforation_pitch_x + _EPSILON < perforation_diameter:
        issues.append(
            ValidationIssue(
                "lid_panel.perforated_grid.pitch_x_mm",
                "perforation_pitch_too_small",
                "Perforation X pitch must be at least the hole diameter.",
            )
        )
    if perforation_pitch_y + _EPSILON < perforation_diameter:
        issues.append(
            ValidationIssue(
                "lid_panel.perforated_grid.pitch_y_mm",
                "perforation_pitch_too_small",
                "Perforation Y pitch must be at least the hole diameter.",
            )
        )

    keepout_defaults = panel_defaults["keepouts"]
    keepout_source = _mapping_field(
        lid_panel_source, "keepouts", "lid_panel.keepouts", issues
    )
    rim_keepout = _number_field(
        keepout_source,
        "rim_mm",
        "lid_panel.keepouts.rim_mm",
        issues,
        default=keepout_defaults["rim_mm"],
        minimum=0.0,
    )
    seal_keepout = _number_field(
        keepout_source,
        "seal_mm",
        "lid_panel.keepouts.seal_mm",
        issues,
        default=keepout_defaults["seal_mm"],
        minimum=0.0,
    )
    hinge_keepout = _number_field(
        keepout_source,
        "hinge_mm",
        "lid_panel.keepouts.hinge_mm",
        issues,
        default=keepout_defaults["hinge_mm"],
        minimum=0.0,
    )
    hinge_edge = keepout_source.get(
        "hinge_edge", keepout_defaults["hinge_edge"]
    )
    if hinge_edge not in LID_HINGE_EDGES:
        issues.append(
            ValidationIssue(
                "lid_panel.keepouts.hinge_edge",
                "invalid_hinge_edge",
                "Hinge edge must be top, bottom, left, or right.",
            )
        )
        hinge_edge = keepout_defaults["hinge_edge"]
    clearance_margin = _number_field(
        keepout_source,
        "clearance_margin_mm",
        "lid_panel.keepouts.clearance_margin_mm",
        issues,
        default=keepout_defaults["clearance_margin_mm"],
        minimum=0.0,
    )
    clearance_rectangles = _rectangle_list(
        keepout_source,
        "rectangles",
        "lid_panel.keepouts.rectangles",
        issues,
    )

    mounting_defaults = panel_defaults["mounting"]
    mounting_source = _mapping_field(
        lid_panel_source, "mounting", "lid_panel.mounting", issues
    )
    perimeter_enabled = _bool_field(
        mounting_source,
        "perimeter_enabled",
        "lid_panel.mounting.perimeter_enabled",
        issues,
        default=mounting_defaults["perimeter_enabled"],
    )
    retainers_enabled = _bool_field(
        mounting_source,
        "retainers_enabled",
        "lid_panel.mounting.retainers_enabled",
        issues,
        default=mounting_defaults["retainers_enabled"],
    )
    retainer_count = _integer_field(
        mounting_source,
        "retainer_count",
        "lid_panel.mounting.retainer_count",
        issues,
        default=mounting_defaults["retainer_count"],
        minimum=2,
        maximum=8,
    )
    retainer_width = _number_field(
        mounting_source,
        "retainer_width_mm",
        "lid_panel.mounting.retainer_width_mm",
        issues,
        default=mounting_defaults["retainer_width_mm"],
        minimum=4.0,
    )
    retainer_projection = _number_field(
        mounting_source,
        "retainer_projection_mm",
        "lid_panel.mounting.retainer_projection_mm",
        issues,
        default=mounting_defaults["retainer_projection_mm"],
        minimum=0.0,
    )
    retainer_clearance = _number_field(
        mounting_source,
        "retainer_clearance_mm",
        "lid_panel.mounting.retainer_clearance_mm",
        issues,
        default=mounting_defaults["retainer_clearance_mm"],
        minimum=0.0,
    )
    lift_access_enabled = _bool_field(
        mounting_source,
        "lift_access_enabled",
        "lid_panel.mounting.lift_access_enabled",
        issues,
        default=mounting_defaults["lift_access_enabled"],
    )
    lift_access_diameter = _number_field(
        mounting_source,
        "lift_access_diameter_mm",
        "lid_panel.mounting.lift_access_diameter_mm",
        issues,
        default=mounting_defaults["lift_access_diameter_mm"],
        minimum=4.0,
    )
    fastener_holes_enabled = _bool_field(
        mounting_source,
        "fastener_holes_enabled",
        "lid_panel.mounting.fastener_holes_enabled",
        issues,
        default=mounting_defaults["fastener_holes_enabled"],
    )
    fastener_hole_diameter = _number_field(
        mounting_source,
        "fastener_hole_diameter_mm",
        "lid_panel.mounting.fastener_hole_diameter_mm",
        issues,
        default=mounting_defaults["fastener_hole_diameter_mm"],
        minimum=0.5,
    )
    fastener_edge_offset = _number_field(
        mounting_source,
        "fastener_edge_offset_mm",
        "lid_panel.mounting.fastener_edge_offset_mm",
        issues,
        default=mounting_defaults["fastener_edge_offset_mm"],
        minimum=0.0,
    )
    custom_fastener_holes = _coordinate_list(
        mounting_source,
        "custom_fastener_holes",
        "lid_panel.mounting.custom_fastener_holes",
        issues,
    )

    splitting_defaults = panel_defaults["splitting"]
    splitting_source = _mapping_field(
        lid_panel_source, "splitting", "lid_panel.splitting", issues
    )
    keyed_alignment = _bool_field(
        splitting_source,
        "keyed_alignment",
        "lid_panel.splitting.keyed_alignment",
        issues,
        default=splitting_defaults["keyed_alignment"],
    )
    key_size = _number_field(
        splitting_source,
        "key_size_mm",
        "lid_panel.splitting.key_size_mm",
        issues,
        default=splitting_defaults["key_size_mm"],
        minimum=2.0,
    )
    key_clearance = _number_field(
        splitting_source,
        "key_clearance_mm",
        "lid_panel.splitting.key_clearance_mm",
        issues,
        default=splitting_defaults["key_clearance_mm"],
        minimum=0.0,
    )

    if (
        lid_panel_enabled
        and perimeter_enabled
        and retainers_enabled
        and retainer_projection < 1.2
    ):
        issues.append(
            ValidationIssue(
                "lid_panel.mounting.retainer_projection_mm",
                "retainer_projection_too_small",
                "Printable retainers need at least 1.2 mm projection below the panel.",
            )
        )

    if lid_length is not None and lid_width is not None:
        perimeter_margin = edge_inset + rim_keepout + seal_keepout
        left_margin = perimeter_margin + (hinge_keepout if hinge_edge == "left" else 0.0)
        right_margin = perimeter_margin + (hinge_keepout if hinge_edge == "right" else 0.0)
        bottom_margin = perimeter_margin + (hinge_keepout if hinge_edge == "bottom" else 0.0)
        top_margin = perimeter_margin + (hinge_keepout if hinge_edge == "top" else 0.0)
        panel_length = lid_length - left_margin - right_margin
        panel_width = lid_width - bottom_margin - top_margin
        if panel_length <= 0.0 or panel_width <= 0.0:
            issues.append(
                ValidationIssue(
                    "lid_panel.keepouts",
                    "no_usable_lid_panel_area",
                    "Edge, rim, seal, and hinge keep-outs leave no usable panel area.",
                )
            )
        elif panel_corner_radius > min(panel_length, panel_width) / 2.0 + _EPSILON:
            issues.append(
                ValidationIssue(
                    "lid_panel.corner_radius_mm",
                    "corner_radius_exceeds_lid_panel",
                    "Panel corner radius cannot exceed half the usable short side.",
                )
            )
        else:
            for index, rectangle in enumerate(clearance_rectangles):
                if (
                    rectangle["x_mm"] + rectangle["length_mm"] > panel_length + _EPSILON
                    or rectangle["y_mm"] + rectangle["width_mm"] > panel_width + _EPSILON
                ):
                    issues.append(
                        ValidationIssue(
                            f"lid_panel.keepouts.rectangles[{index}]",
                            "keepout_outside_panel",
                            "Clearance keep-out must remain inside the finished panel bounds.",
                        )
                    )
            for index, point in enumerate(custom_fastener_holes):
                radius = fastener_hole_diameter / 2.0
                if not (
                    radius <= point["x_mm"] <= panel_length - radius
                    and radius <= point["y_mm"] <= panel_width - radius
                ):
                    issues.append(
                        ValidationIssue(
                            f"lid_panel.mounting.custom_fastener_holes[{index}]",
                            "fastener_hole_outside_panel",
                            "Custom fastener hole must remain inside the finished panel.",
                        )
                    )

    layers_source = _mapping_field(spec, "layers", "layers", issues)
    layers_enabled = _bool_field(
        layers_source, "enabled", "layers.enabled", issues, default=False
    )
    layer_ratio = _number_field(
        layers_source,
        "ratio",
        "layers.ratio",
        issues,
        default=0.5,
        minimum=0.0,
        maximum=1.0,
        exclusive=True,
    )
    layer_floor = _number_field(
        layers_source,
        "floor_mm",
        "layers.floor_mm",
        issues,
        default=2.4,
        minimum=0.0,
        exclusive=True,
    )

    containment_source = _mapping_field(
        spec, "containment", "containment", issues
    )
    containment_mode = containment_source.get("mode", "none")
    if containment_mode not in CONTAINMENT_MODES:
        issues.append(
            ValidationIssue(
                path="containment.mode",
                code="invalid_containment_mode",
                message=(
                    "Containment must be none, shared_panel, or individual_lids; "
                    f"received {containment_mode!r}."
                ),
            )
        )
        containment_mode = "none"
    containment_clearance = _number_field(
        containment_source,
        "clearance_mm",
        "containment.clearance_mm",
        issues,
        default=0.4,
        minimum=0.0,
    )
    panel_thickness = _number_field(
        containment_source,
        "panel_thickness_mm",
        "containment.panel_thickness_mm",
        issues,
        default=2.0,
        minimum=0.0,
        exclusive=True,
    )

    printer_source = _mapping_field(spec, "printer", "printer", issues)
    bed_x = _number_field(
        printer_source,
        "bed_x",
        "printer.bed_x",
        issues,
        minimum=0.0,
        exclusive=True,
    )
    bed_y = _number_field(
        printer_source,
        "bed_y",
        "printer.bed_y",
        issues,
        minimum=0.0,
        exclusive=True,
    )
    bed_margin = _number_field(
        printer_source,
        "margin",
        "printer.margin",
        issues,
        default=5.0,
        minimum=0.0,
    )
    split_for_bed = _bool_field(
        printer_source, "split", "printer.split", issues, default=True
    )
    if bed_x - (2.0 * bed_margin) <= 0.0 or bed_y - (2.0 * bed_margin) <= 0.0:
        issues.append(
            ValidationIssue(
                path="printer.margin",
                code="no_usable_bed_area",
                message="Printer margin leaves no usable bed area.",
            )
        )

    objects_source = spec.get("objects", [])
    if (
        not isinstance(objects_source, Sequence)
        or isinstance(objects_source, (str, bytes, bytearray))
    ):
        issues.append(
            ValidationIssue(
                path="objects",
                code="invalid_object_list",
                message="Objects must be a list.",
            )
        )
        objects_source = []

    normalized_objects: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_object in enumerate(objects_source):
        object_path = f"objects[{index}]"
        if not isinstance(raw_object, Mapping):
            issues.append(
                ValidationIssue(
                    path=object_path,
                    code="invalid_object",
                    message="Each object must be a mapping.",
                )
            )
            continue

        object_id = _text_field(raw_object, "id", f"{object_path}.id", issues)
        if object_id:
            if object_id in seen_ids:
                issues.append(
                    ValidationIssue(
                        path=f"{object_path}.id",
                        code="duplicate_object_id",
                        message=f"Object id {object_id!r} is already in use.",
                    )
                )
            seen_ids.add(object_id)

        object_type = raw_object.get("type")
        if object_type not in OBJECT_TYPES:
            issues.append(
                ValidationIssue(
                    path=f"{object_path}.type",
                    code="invalid_object_type",
                    message=(
                        f"Object type must be one of {', '.join(OBJECT_TYPES)}; "
                        f"received {object_type!r}."
                    ),
                )
            )
            object_type = "rectangular_pocket"
        object_name = _text_field(
            raw_object,
            "name",
            f"{object_path}.name",
            issues,
            default=object_id or f"Object {index + 1}",
        )
        x = _number_field(
            raw_object, "x", f"{object_path}.x", issues, default=0.0
        )
        y = _number_field(
            raw_object, "y", f"{object_path}.y", issues, default=0.0
        )
        rotation = _number_field(
            raw_object,
            "rotation",
            f"{object_path}.rotation",
            issues,
            default=0.0,
        ) % 360.0
        object_layer = raw_object.get("layer", "lower")
        if object_layer not in OBJECT_LAYERS:
            issues.append(
                ValidationIssue(
                    path=f"{object_path}.layer",
                    code="unsupported_object_layer",
                    message="Schema version 1 supports only lower and upper layers.",
                )
            )
            object_layer = "lower"
        elif object_layer == "upper" and not layers_enabled:
            issues.append(
                ValidationIssue(
                    path=f"{object_path}.layer",
                    code="upper_layer_disabled",
                    message="Enable layers before assigning an object to upper.",
                )
            )
        locked = _bool_field(
            raw_object,
            "locked",
            f"{object_path}.locked",
            issues,
            default=False,
        )
        object_width = _number_field(
            raw_object,
            "width",
            f"{object_path}.width",
            issues,
            minimum=0.0,
            exclusive=True,
        )
        object_length = _number_field(
            raw_object,
            "length",
            f"{object_path}.length",
            issues,
            minimum=0.0,
            exclusive=True,
        )
        object_height = _number_field(
            raw_object,
            "height",
            f"{object_path}.height",
            issues,
            minimum=0.0,
            exclusive=True,
        )

        extra_fields = {
            key: copy.deepcopy(raw_object[key])
            for key in sorted(raw_object)
            if key not in _STABLE_OBJECT_FIELDS
        }
        if "priority" in extra_fields:
            extra_fields["priority"] = _coerce_number(
                extra_fields["priority"],
                f"{object_path}.priority",
                issues,
                default=0.0,
            )
        for option in ("diameter", "scale", "wall", "clearance"):
            if option in extra_fields:
                extra_fields[option] = _coerce_number(
                    extra_fields[option],
                    f"{object_path}.{option}",
                    issues,
                    minimum=0.0,
                    exclusive=option != "clearance",
                )
        for option in ("rows", "columns"):
            if option in extra_fields:
                value = _coerce_number(
                    extra_fields[option], f"{object_path}.{option}", issues,
                    default=2.0, minimum=1.0,
                )
                if not value.is_integer():
                    issues.append(ValidationIssue(
                        f"{object_path}.{option}", "invalid_integer",
                        f"{object_path}.{option} must be a whole number.",
                    ))
                extra_fields[option] = int(value)
        if "rotatable" in extra_fields and not isinstance(
            extra_fields["rotatable"], bool
        ):
            issues.append(
                ValidationIssue(
                    path=f"{object_path}.rotatable",
                    code="invalid_boolean",
                    message="rotatable must be true or false.",
                )
            )

        normalized_objects.append(
            {
                "id": object_id,
                "type": object_type,
                "name": object_name,
                "x": x,
                "y": y,
                "rotation": rotation,
                "layer": object_layer,
                "locked": locked,
                "width": object_width,
                "length": object_length,
                "height": object_height,
                **extra_fields,
            }
        )

    # Lid clearance is evidence for containment and carried contents, not
    # permission to make the printed carrier taller than the case bottom.
    if insert_depth is not None:
        carrier_count = 2 if layers_enabled else 1
        total_usable_height = insert_depth - bottom_clearance
        if containment_mode == "shared_panel":
            total_usable_height -= panel_thickness + containment_clearance
        total_clear_height = total_usable_height - (carrier_count * layer_floor)
        if total_clear_height <= 0.0:
            issues.append(
                ValidationIssue(
                    path="layers.floor_mm",
                    code="no_usable_insert_height",
                    message=(
                        "Bottom clearance, containment, and one floor per carrier "
                        "leave no clear object height."
                    ),
                )
            )

    verification = _mapping_field(spec, "verification", "verification", issues)

    if issues:
        raise ProjectValidationError(issues)

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "case": {
            "case_model": case_model,
            "internal_length": internal_length,
            "internal_width": internal_width,
            "insert_depth": insert_depth,
            "corner_radius": corner_radius,
            "side_clearance": side_clearance,
            "bottom_clearance": bottom_clearance,
            "taper_allowance": taper_allowance,
            "layout_inset": layout_inset,
            **case_extra_fields,
        },
        "lid": {
            "source": lid_evidence,
            "clearance_mm": lid_clearance,
            "envelope_source": envelope_evidence,
            "length_mm": lid_length,
            "width_mm": lid_width,
        },
        "lid_panel": {
            "enabled": lid_panel_enabled,
            "pattern": lid_panel_pattern,
            "thickness_mm": lid_panel_thickness,
            "payload_thickness_mm": payload_thickness,
            "edge_inset_mm": edge_inset,
            "corner_radius_mm": panel_corner_radius,
            "slot_grid": {
                "slot_length_mm": slot_length,
                "slot_width_mm": slot_width,
                "pitch_x_mm": slot_pitch_x,
                "pitch_y_mm": slot_pitch_y,
                "margin_x_mm": slot_margin_x,
                "margin_y_mm": slot_margin_y,
                "orientation": slot_orientation,
            },
            "perforated_grid": {
                "diameter_mm": perforation_diameter,
                "pitch_x_mm": perforation_pitch_x,
                "pitch_y_mm": perforation_pitch_y,
                "margin_x_mm": perforation_margin_x,
                "margin_y_mm": perforation_margin_y,
            },
            "keepouts": {
                "rim_mm": rim_keepout,
                "seal_mm": seal_keepout,
                "hinge_mm": hinge_keepout,
                "hinge_edge": hinge_edge,
                "clearance_margin_mm": clearance_margin,
                "rectangles": clearance_rectangles,
            },
            "mounting": {
                "perimeter_enabled": perimeter_enabled,
                "retainers_enabled": retainers_enabled,
                "retainer_count": retainer_count,
                "retainer_width_mm": retainer_width,
                "retainer_projection_mm": retainer_projection,
                "retainer_clearance_mm": retainer_clearance,
                "lift_access_enabled": lift_access_enabled,
                "lift_access_diameter_mm": lift_access_diameter,
                "fastener_holes_enabled": fastener_holes_enabled,
                "fastener_hole_diameter_mm": fastener_hole_diameter,
                "fastener_edge_offset_mm": fastener_edge_offset,
                "custom_fastener_holes": custom_fastener_holes,
            },
            "splitting": {
                "keyed_alignment": keyed_alignment,
                "key_size_mm": key_size,
                "key_clearance_mm": key_clearance,
            },
        },
        "layers": {
            "enabled": layers_enabled,
            "ratio": layer_ratio,
            "floor_mm": layer_floor,
        },
        "containment": {
            "mode": containment_mode,
            "clearance_mm": containment_clearance,
            "panel_thickness_mm": panel_thickness,
        },
        "printer": {
            "bed_x": bed_x,
            "bed_y": bed_y,
            "margin": bed_margin,
            "split": split_for_bed,
        },
        "objects": normalized_objects,
    }
    normalized.update(
        {
            field_name: copy.deepcopy(
                dict(verification) if field_name == "verification" else spec[field_name]
            )
            for field_name in _PERSISTED_PROJECT_FIELDS
            if field_name in spec
        }
    )
    return normalized


def lid_panel_plan(spec: Mapping[str, Any]) -> dict[str, float]:
    """Resolve the finished panel rectangle inside the evidenced lid envelope."""

    project = validate_project(spec)
    lid = project["lid"]
    panel = project["lid_panel"]
    length = lid.get("length_mm")
    width = lid.get("width_mm")
    if length is None or width is None:
        raise ValueError(
            "Enter the lid-panel envelope length and width before previewing its bounds."
        )
    keepouts = panel["keepouts"]
    perimeter = (
        panel["edge_inset_mm"] + keepouts["rim_mm"] + keepouts["seal_mm"]
    )
    margins = {
        "left": perimeter,
        "right": perimeter,
        "bottom": perimeter,
        "top": perimeter,
    }
    margins[keepouts["hinge_edge"]] += keepouts["hinge_mm"]
    panel_length = length - margins["left"] - margins["right"]
    panel_width = width - margins["bottom"] - margins["top"]
    if panel_length <= 0.0 or panel_width <= 0.0:
        raise ValueError(
            "Edge, rim, seal, and hinge keep-outs leave no usable lid-panel area."
        )
    return {
        "x_mm": _clean_float(margins["left"]),
        "y_mm": _clean_float(margins["bottom"]),
        "length_mm": _clean_float(panel_length),
        "width_mm": _clean_float(panel_width),
        "left_margin_mm": _clean_float(margins["left"]),
        "right_margin_mm": _clean_float(margins["right"]),
        "bottom_margin_mm": _clean_float(margins["bottom"]),
        "top_margin_mm": _clean_float(margins["top"]),
    }


def lid_panel_height_budget(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fail-closed printable gate for an inside-lid panel.

    Configuration remains valid while evidence is unknown.  Only this gate
    grants printable generation, after both the plan envelope and the lowest
    closed-lid clearance are measured or CAD-derived and the configured panel,
    payload, and retainers fit that height.
    """

    project = validate_project(spec)
    lid = project["lid"]
    panel = project["lid_panel"]
    mounting = panel["mounting"]
    retainer_projection = (
        mounting["retainer_projection_mm"]
        if mounting["perimeter_enabled"] and mounting["retainers_enabled"]
        else 0.0
    )
    proud_feature = max(panel["payload_thickness_mm"], retainer_projection)
    required = panel["thickness_mm"] + proud_feature
    available = (
        lid["clearance_mm"]
        if lid["source"] in ("measured", "cad-derived")
        else None
    )
    remaining = None if available is None else available - required
    reasons: list[str] = []
    if not panel["enabled"]:
        reasons.append("Inside-lid panel generation is not enabled in this project.")
    if lid["envelope_source"] not in ("measured", "cad-derived"):
        reasons.append(
            "Lid-panel envelope evidence is Unknown; record measured or CAD-derived length and width."
        )
    if lid.get("length_mm") is None or lid.get("width_mm") is None:
        reasons.append("Lid-panel envelope length and width are missing.")
    if lid["source"] not in ("measured", "cad-derived"):
        reasons.append(
            "Closed-lid clearance is Unknown; configuration and preview are allowed, but printable generation is blocked."
        )
    if remaining is not None and remaining < -_EPSILON:
        reasons.append(
            "Configured panel height %.2f mm exceeds the evidenced %.2f mm closed-lid clearance by %.2f mm."
            % (required, available, abs(remaining))
        )
    printable = not reasons
    return {
        "printable": printable,
        "status": "ready" if printable else "blocked",
        "required_height_mm": _clean_float(required),
        "panel_thickness_mm": _clean_float(panel["thickness_mm"]),
        "payload_thickness_mm": _clean_float(panel["payload_thickness_mm"]),
        "retainer_projection_mm": _clean_float(retainer_projection),
        "available_clearance_mm": (
            None if available is None else _clean_float(available)
        ),
        "remaining_clearance_mm": (
            None if remaining is None else _clean_float(remaining)
        ),
        "reasons": reasons,
    }


def layout_project(spec: Mapping[str, Any], strategy: str) -> LayoutResult:
    """Plan one deterministic layout without mutating ``spec``."""

    if strategy not in LAYOUT_STRATEGIES:
        raise ValueError(
            f"Unknown layout strategy {strategy!r}; expected one of "
            f"{', '.join(LAYOUT_STRATEGIES)}."
        )
    project = validate_project(spec)
    layer_heights = _layer_heights(project)
    return _generate_strategy(
        project, strategy, layer_heights, _project_warnings(project)
    )


def generate_layouts(spec: Mapping[str, Any]) -> tuple[LayoutResult, ...]:
    """Return the three public strategies in stable ranked order."""

    project = validate_project(spec)
    layer_heights = _layer_heights(project)
    warnings = _project_warnings(project)
    return tuple(
        _generate_strategy(project, strategy, layer_heights, warnings)
        for strategy in LAYOUT_STRATEGIES
    )


def _generate_strategy(
    project: Mapping[str, Any],
    strategy: str,
    layer_heights: tuple[tuple[str, float], ...],
    warnings: tuple[str, ...],
) -> LayoutResult:
    case_length, case_width = _usable_plan(project)
    layout_inset = project["case"].get("layout_inset", 0.0)
    planning_length = case_length - 2.0 * layout_inset
    planning_width = case_width - 2.0 * layout_inset
    available_height = dict(layer_heights)
    layer_names = tuple(layer for layer, _ in layer_heights)
    objects = project["objects"]
    placements: list[Placement] = []
    unplaced: list[UnplacedObject] = []

    locked_objects = sorted(
        (item for item in objects if item["locked"]), key=lambda item: item["id"]
    )
    for item in locked_objects:
        footprint_length, footprint_width = _footprint(item, item["rotation"])
        layer = item["layer"]
        if item["height"] > available_height[layer] + _EPSILON:
            unplaced.append(
                _height_failure(item, ((layer, available_height[layer]),), layer=layer)
            )
            continue
        if not _within_case(
            item["x"],
            item["y"],
            footprint_length,
            footprint_width,
            case_length,
            case_width,
            layout_inset,
        ):
            unplaced.append(
                UnplacedObject(
                    object_id=item["id"],
                    code="locked_out_of_bounds",
                    reason=(
                        f"The locked position ({item['x']:.1f}, {item['y']:.1f}) mm "
                        f"puts its {footprint_length:.1f} x {footprint_width:.1f} mm "
                        f"footprint outside the {planning_length:.1f} x {planning_width:.1f} mm "
                        "usable case area."
                    ),
                )
            )
            continue
        conflict = _first_conflict(
            item["x"],
            item["y"],
            footprint_length,
            footprint_width,
            layer,
            placements,
        )
        if conflict is not None:
            unplaced.append(
                UnplacedObject(
                    object_id=item["id"],
                    code="locked_collision",
                    reason=(
                        f"The locked position overlaps locked object "
                        f"{conflict.object_id!r} on the {layer} layer; unlock or move one."
                    ),
                )
            )
            continue
        placements.append(
            _placement(item, item["x"], item["y"], item["rotation"], layer)
        )

    unlocked_objects = [item for item in objects if not item["locked"]]
    unlocked_objects.sort(key=lambda item: _object_sort_key(item, strategy))
    for item in unlocked_objects:
        rotations = _candidate_rotations(item)
        layers = _candidate_layers(
            item,
            strategy,
            layer_names,
            placements,
            planning_length * planning_width,
        )
        layers_with_height = tuple(
            layer
            for layer in layers
            if item["height"] <= available_height[layer] + _EPSILON
        )
        if not layers_with_height:
            unplaced.append(_height_failure(item, layer_heights))
            continue

        fitting_rotations = tuple(
            rotation
            for rotation in rotations
            if _footprint(item, rotation)[0] <= planning_length + _EPSILON
            and _footprint(item, rotation)[1] <= planning_width + _EPSILON
        )
        if not fitting_rotations:
            sizes = [
                f"{length:.1f} x {width:.1f} mm"
                for length, width in (
                    _footprint(item, rotation) for rotation in rotations
                )
            ]
            unplaced.append(
                UnplacedObject(
                    object_id=item["id"],
                    code="footprint_exceeds_case",
                    reason=(
                        f"Footprint ({' or '.join(sizes)}) exceeds the "
                        f"{planning_length:.1f} x {planning_width:.1f} mm usable case area "
                        "in every allowed rotation."
                    ),
                )
            )
            continue

        resolved: Placement | None = None
        for layer in layers_with_height:
            resolved = _find_placement(
                item,
                fitting_rotations,
                layer,
                placements,
                case_length,
                case_width,
                strategy,
                layout_inset,
            )
            if resolved is not None:
                break
        if resolved is None:
            unplaced.append(
                UnplacedObject(
                    object_id=item["id"],
                    code="insufficient_area_or_collision",
                    reason=(
                        f"No collision-free position remains in the {planning_length:.1f} x "
                        f"{planning_width:.1f} mm conservative layout area on an eligible layer."
                    ),
                )
            )
        else:
            placements.append(resolved)

    return LayoutResult(
        strategy=strategy,
        placements=tuple(sorted(placements, key=lambda item: item.object_id)),
        unplaced=tuple(sorted(unplaced, key=lambda item: item.object_id)),
        warnings=warnings,
        layer_heights=layer_heights,
    )


def _find_placement(
    item: Mapping[str, Any],
    rotations: Sequence[float],
    layer: str,
    placements: Sequence[Placement],
    case_length: float,
    case_width: float,
    strategy: str,
    layout_inset: float,
) -> Placement | None:
    layer_placements = [placed for placed in placements if placed.layer == layer]
    x_candidates = {layout_inset}
    y_candidates = {layout_inset}
    for placed in layer_placements:
        x_candidates.add(_clean_float(placed.x + placed.length))
        y_candidates.add(_clean_float(placed.y + placed.width))

    candidates: list[tuple[tuple[float, ...], float, float, float]] = []
    for rotation in rotations:
        length, width = _footprint(item, rotation)
        for y in sorted(y_candidates):
            for x in sorted(x_candidates):
                if not _within_case(
                    x, y, length, width, case_length, case_width,
                    layout_inset,
                ) or _first_conflict(
                    x, y, length, width, layer, placements
                ) is not None:
                    continue
                if strategy == "balanced":
                    centre_offset = abs(
                        (x + length / 2.0) - case_length / 2.0
                    ) + abs((y + width / 2.0) - case_width / 2.0)
                    score = (centre_offset, y, x, rotation)
                elif strategy == "maximum_capacity":
                    score = (
                        max(x + length, y + width),
                        y + width,
                        x + length,
                        rotation,
                    )
                else:
                    score = (y, x, rotation)
                candidates.append((score, x, y, rotation))

    if not candidates:
        return None
    _, x, y, rotation = min(candidates, key=lambda candidate: candidate[0])
    return _placement(item, x, y, rotation, layer)


def _object_sort_key(item: Mapping[str, Any], strategy: str) -> tuple[Any, ...]:
    area = item["length"] * item["width"]
    volume = area * item["height"]
    priority = float(item.get("priority", 0.0))
    if strategy == "balanced":
        return (-priority, -area, -item["height"], item["id"])
    if strategy == "maximum_capacity":
        return (-volume, -area, -priority, item["id"])
    return (-area, -item["height"], -priority, item["id"])


def _candidate_layers(
    item: Mapping[str, Any],
    strategy: str,
    layers: tuple[str, ...],
    placements: Sequence[Placement],
    layer_area: float,
) -> tuple[str, ...]:
    if len(layers) == 1:
        return ("lower",)
    if strategy == "fewest_layers":
        return ("lower", "upper")
    if strategy == "balanced":
        preferred = item["layer"]
        return (preferred,) + tuple(layer for layer in layers if layer != preferred)

    used_area = {
        layer: sum(
            placed.length * placed.width
            for placed in placements
            if placed.layer == layer
        )
        for layer in layers
    }
    return tuple(
        sorted(
            layers,
            key=lambda layer: (-(layer_area - used_area[layer]), layers.index(layer)),
        )
    )


def _candidate_rotations(item: Mapping[str, Any]) -> tuple[float, ...]:
    original = _clean_float(item["rotation"] % 360.0)
    if item.get("rotatable", True) is False:
        return (original,)
    alternate = _clean_float((original + 90.0) % 360.0)
    if _footprint(item, original) == _footprint(item, alternate):
        return (original,)
    return (original, alternate)


def _placement(
    item: Mapping[str, Any], x: float, y: float, rotation: float, layer: str
) -> Placement:
    length, width = _footprint(item, rotation)
    return Placement(
        object_id=item["id"],
        object_type=item["type"],
        name=item["name"],
        x=_clean_float(x),
        y=_clean_float(y),
        rotation=_clean_float(rotation % 360.0),
        layer=layer,
        width=width,
        length=length,
        height=item["height"],
        locked=item["locked"],
    )


def _footprint(item: Mapping[str, Any], rotation: float) -> tuple[float, float]:
    radians = math.radians(rotation % 180.0)
    cosine = abs(math.cos(radians))
    sine = abs(math.sin(radians))
    return (
        _clean_float((item["length"] * cosine) + (item["width"] * sine)),
        _clean_float((item["length"] * sine) + (item["width"] * cosine)),
    )


def _within_case(
    x: float,
    y: float,
    length: float,
    width: float,
    case_length: float,
    case_width: float,
    layout_inset: float = 0.0,
) -> bool:
    return (
        x >= layout_inset - _EPSILON
        and y >= layout_inset - _EPSILON
        and x + length <= case_length - layout_inset + _EPSILON
        and y + width <= case_width - layout_inset + _EPSILON
    )


def _first_conflict(
    x: float,
    y: float,
    length: float,
    width: float,
    layer: str,
    placements: Sequence[Placement],
) -> Placement | None:
    for placed in placements:
        if placed.layer != layer:
            continue
        separated = (
            x + length <= placed.x + _EPSILON
            or placed.x + placed.length <= x + _EPSILON
            or y + width <= placed.y + _EPSILON
            or placed.y + placed.width <= y + _EPSILON
        )
        if not separated:
            return placed
    return None


def _height_failure(
    item: Mapping[str, Any],
    heights: Sequence[tuple[str, float]],
    *,
    layer: str | None = None,
) -> UnplacedObject:
    available = max(height for _, height in heights)
    layer_text = f" on the locked {layer} layer" if layer is not None else ""
    return UnplacedObject(
        object_id=item["id"],
        code="insufficient_height",
        reason=(
            f"Object height {item['height']:.1f} mm exceeds the {available:.1f} mm "
            f"available{layer_text}."
        ),
    )


def _usable_plan(project: Mapping[str, Any]) -> tuple[float, float]:
    case = project["case"]
    per_side = case["side_clearance"] + case["taper_allowance"]
    return (
        _clean_float(case["internal_length"] - (2.0 * per_side)),
        _clean_float(case["internal_width"] - (2.0 * per_side)),
    )


def _layer_heights(project: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    total_height = project["case"]["insert_depth"] - project["case"][
        "bottom_clearance"
    ]
    containment = project["containment"]
    if containment["mode"] == "shared_panel":
        total_height -= (
            containment["panel_thickness_mm"] + containment["clearance_mm"]
        )
    floor_mm = project["layers"]["floor_mm"]
    if not project["layers"]["enabled"]:
        return (("lower", _clean_float(total_height - floor_mm)),)
    total_clear_height = total_height - (2.0 * floor_mm)
    lower = total_clear_height * project["layers"]["ratio"]
    return (
        ("lower", _clean_float(lower)),
        ("upper", _clean_float(total_clear_height - lower)),
    )


def _project_warnings(project: Mapping[str, Any]) -> tuple[str, ...]:
    warnings: list[str] = []
    if project["lid"]["source"] == "unknown":
        warnings.append(
            "Lid clearance is unknown; layouts use only the case insert depth."
        )
    warnings.extend(uncovered_storage_warnings(project))
    return tuple(warnings)


def uncovered_storage_warnings(project: Mapping[str, Any]) -> tuple[str, ...]:
    """Explain which placed loose-storage objects lack generated containment.

    The caller supplies a normalized project. Individual lids cover removable
    bins only; selecting that mode cannot cover divider regions or container
    bays. A measured zero lid gap keeps the existing closed-lid exception.
    """

    mode = project["containment"]["mode"]
    if mode == "shared_panel" or project["lid"]["clearance_mm"] == 0.0:
        return ()
    unplaced_ids = {
        item.get("object_id") for item in project.get("unplaced", [])
        if isinstance(item, Mapping)
    }
    warnings = []
    for item in project["objects"]:
        kind = item["type"]
        if kind not in _LOOSE_STORAGE_TYPES or item["id"] in unplaced_ids:
            continue
        if mode == "individual_lids" and kind == "removable_bin":
            continue
        remedy = (
            "select a shared panel or individual bin lids"
            if kind == "removable_bin" else
            "select a shared panel; individual bin lids cover removable bins only"
        )
        warnings.append(
            f"Loose storage {kind.replace('_', ' ')} {item['id']!r} has no containment "
            f"while closed-lid clearance is unknown or positive; {remedy} "
            "before carrying the case."
        )
    return tuple(warnings)


def _integer_field(
    source: Mapping[str, Any],
    field_name: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _number_field(
        source,
        field_name,
        path,
        issues,
        default=float(default),
        minimum=float(minimum),
        maximum=float(maximum),
    )
    rounded = int(round(value))
    if abs(value - rounded) > _EPSILON:
        issues.append(
            ValidationIssue(path, "invalid_integer", f"{path} must be a whole number.")
        )
        return default
    return rounded


def _coordinate_list(
    source: Mapping[str, Any],
    field_name: str,
    path: str,
    issues: list[ValidationIssue],
) -> list[dict[str, float]]:
    raw = source.get(field_name, [])
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes, bytearray))
    ):
        issues.append(
            ValidationIssue(path, "invalid_coordinate_list", f"{path} must be a list.")
        )
        return []
    normalized: list[dict[str, float]] = []
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if isinstance(item, Mapping):
            x_source, y_source = item.get("x_mm"), item.get("y_mm")
        elif (
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes, bytearray))
            and len(item) == 2
        ):
            x_source, y_source = item
        else:
            issues.append(
                ValidationIssue(
                    item_path,
                    "invalid_coordinate",
                    f"{item_path} must contain x_mm and y_mm.",
                )
            )
            continue
        x = _coerce_number(x_source, f"{item_path}.x_mm", issues, minimum=0.0)
        y = _coerce_number(y_source, f"{item_path}.y_mm", issues, minimum=0.0)
        normalized.append({"x_mm": x, "y_mm": y})
    return normalized


def _rectangle_list(
    source: Mapping[str, Any],
    field_name: str,
    path: str,
    issues: list[ValidationIssue],
) -> list[dict[str, Any]]:
    raw = source.get(field_name, [])
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes, bytearray))
    ):
        issues.append(
            ValidationIssue(path, "invalid_rectangle_list", f"{path} must be a list.")
        )
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            issues.append(
                ValidationIssue(
                    item_path,
                    "invalid_rectangle",
                    f"{item_path} must be a mapping.",
                )
            )
            continue
        label = str(item.get("label") or f"Clearance keep-out {index + 1}").strip()
        x = _coerce_number(item.get("x_mm"), f"{item_path}.x_mm", issues, minimum=0.0)
        y = _coerce_number(item.get("y_mm"), f"{item_path}.y_mm", issues, minimum=0.0)
        length = _coerce_number(
            item.get("length_mm"),
            f"{item_path}.length_mm",
            issues,
            minimum=0.0,
            exclusive=True,
        )
        width = _coerce_number(
            item.get("width_mm"),
            f"{item_path}.width_mm",
            issues,
            minimum=0.0,
            exclusive=True,
        )
        normalized.append(
            {
                "label": label,
                "x_mm": x,
                "y_mm": y,
                "length_mm": length,
                "width_mm": width,
            }
        )
    return normalized


def _mapping_field(
    source: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ValidationIssue],
) -> Mapping[str, Any]:
    value = source.get(field, {})
    if not isinstance(value, Mapping):
        issues.append(
            ValidationIssue(path, "invalid_mapping", f"{path} must be a mapping.")
        )
        return {}
    return value


def _text_field(
    source: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: str | None = None,
) -> str:
    value = source.get(field, default)
    if not isinstance(value, str) or not value.strip():
        issues.append(
            ValidationIssue(path, "invalid_text", f"{path} must be a non-empty string.")
        )
        return default or ""
    return value.strip()


def _bool_field(
    source: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: bool,
) -> bool:
    value = source.get(field, default)
    if not isinstance(value, bool):
        issues.append(
            ValidationIssue(path, "invalid_boolean", f"{path} must be true or false.")
        )
        return default
    return value


def _number_field(
    source: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive: bool = False,
) -> float:
    return _coerce_number(
        source.get(field, default),
        path,
        issues,
        default=default,
        minimum=minimum,
        maximum=maximum,
        exclusive=exclusive,
    )


def _coerce_number(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(
            ValidationIssue(path, "invalid_number", f"{path} must be a finite number.")
        )
        return float(default or 0.0)
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        normalized = math.inf
    if not math.isfinite(normalized):
        issues.append(
            ValidationIssue(path, "invalid_number", f"{path} must be a finite number.")
        )
        return float(default or 0.0)
    if minimum is not None and (
        normalized < minimum or (exclusive and normalized <= minimum)
    ):
        comparison = "greater than" if exclusive else "at least"
        issues.append(
            ValidationIssue(
                path,
                "number_out_of_range",
                f"{path} must be {comparison} {minimum}.",
            )
        )
    if maximum is not None and (
        normalized > maximum or (exclusive and normalized >= maximum)
    ):
        comparison = "less than" if exclusive else "at most"
        issues.append(
            ValidationIssue(
                path,
                "number_out_of_range",
                f"{path} must be {comparison} {maximum}.",
            )
        )
    return normalized


def _report_text(report: Mapping[str, Any], field_name: str) -> str:
    value = report.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"Generation report field {field_name!r} must be a non-empty string."
        )
    return value


def _report_sequence(
    report: Mapping[str, Any], field_name: str
) -> Sequence[Any]:
    value = report.get(field_name)
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ValueError(
            f"Generation report field {field_name!r} must be a sequence."
        )
    return value


def _report_text_sequence(
    report: Mapping[str, Any], field_name: str
) -> tuple[str, ...]:
    values = _report_sequence(report, field_name)
    if any(not isinstance(value, str) for value in values):
        raise ValueError(
            f"Generation report field {field_name!r} must contain only strings."
        )
    return tuple(values)


def _report_integer(
    report: Mapping[str, Any], field_name: str, *, minimum: int
) -> int:
    value = report.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"Generation report field {field_name!r} must be an integer of at least {minimum}."
        )
    return value


def _report_boolean(report: Mapping[str, Any], field_name: str) -> bool:
    value = report.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(
            f"Generation report field {field_name!r} must be true or false."
        )
    return value


def _report_number(
    report: Mapping[str, Any], field_name: str, *, minimum: float
) -> float:
    value = report.get(field_name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ValueError(
            f"Generation report field {field_name!r} must be a finite number of at least {minimum}."
        )
    return float(value)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return tuple(_freeze_value(item) for item in value)
    return copy.deepcopy(value)


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return copy.deepcopy(value)


def _clean_float(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if abs(rounded) < _EPSILON else rounded


__all__ = [
    "CONTAINMENT_MODES",
    "GenerationResult",
    "LAYOUT_STRATEGIES",
    "LID_EVIDENCE_STATES",
    "LID_HINGE_EDGES",
    "LID_PANEL_ORIENTATIONS",
    "LID_PANEL_PATTERNS",
    "LayoutResult",
    "OBJECT_LAYERS",
    "OBJECT_TYPES",
    "Placement",
    "ProjectValidationError",
    "SCHEMA_VERSION",
    "UnplacedObject",
    "ValidationIssue",
    "default_lid_panel",
    "generate_layouts",
    "lid_panel_height_budget",
    "lid_panel_plan",
    "layout_project",
    "validate_project",
]
