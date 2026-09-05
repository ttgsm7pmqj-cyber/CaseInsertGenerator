# SPDX-License-Identifier: LGPL-2.1-or-later
"""Safe SVG preflight and coordinate normalization for Case Insert Generator.

This canonical add-on module validates an SVG before the FreeCAD-facing
importer is allowed to open it.  It does not execute scripts, fetch resources,
expand entities, or silently discard visible artwork.  The resulting metadata
is dependency-free and deliberately contains no FreeCAD objects, so it can be
tested headlessly.

The later geometry adapter is expected to use FreeCAD's LGPL importer rather
than reimplementing SVG curves here:

* Source: https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Draft/importSVG.py
* SPDX-License-Identifier: LGPL-2.1-or-later

No FreeCAD source code is copied into this module.

SPDX-License-Identifier: LGPL-2.1-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Sequence
import xml.etree.ElementTree as ET


PX_TO_MM = 25.4 / 96.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_ELEMENT_DEPTH = 128
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

FATAL = "fatal"
WARNING = "warning"

Matrix = tuple[float, float, float, float, float, float]
IDENTITY_MATRIX: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_NUMBER_PATTERN = r"[-+]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:[eE][-+]?\d+)?"
_NUMBER_RE = re.compile(_NUMBER_PATTERN)
_LENGTH_RE = re.compile(
    rf"^\s*({_NUMBER_PATTERN})\s*(mm|cm|in|pt|pc|px)?\s*$",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(rf"[AaCcHhLlMmQqSsTtVvZz]|{_NUMBER_PATTERN}")
_COMMAND_RE = re.compile(r"^[AaCcHhLlMmQqSsTtVvZz]$")
_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_SAFE_SEPARATORS_RE = re.compile(r"^[\s,]*$")
_FORBIDDEN_XML_RE = re.compile(
    br"(?:<!\s*(?:DOCTYPE|ENTITY)\b|<\?\s*xml-stylesheet\b)",
    re.IGNORECASE,
)

_LENGTH_FACTORS = {
    "": PX_TO_MM,
    "px": PX_TO_MM,
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
}

_GEOMETRY_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_CONTAINER_TAGS = {"svg", "g", "a", "switch"}
_DEFINITION_TAGS = {
    "defs",
    "clipPath",
    "mask",
    "marker",
    "pattern",
    "symbol",
    "linearGradient",
    "radialGradient",
    "meshgradient",
    "filter",
}
_PASSIVE_TAGS = {"title", "desc", "metadata", "namedview", "style", "stop"}
_EXPLICITLY_UNSUPPORTED = {
    "text": "Convert text to paths before importing.",
    "textPath": "Convert text to paths before importing.",
    "tspan": "Convert text to paths before importing.",
    "image": "Embed or trace the image as closed vector paths before importing.",
    "use": "Expand cloned <use> elements into ordinary paths before importing.",
    "foreignObject": "Remove foreignObject content or convert it to closed SVG paths.",
    "script": "Remove scripts; executable SVG content is not accepted.",
}

_PATH_ARITY = {
    "M": 2,
    "L": 2,
    "H": 1,
    "V": 1,
    "C": 6,
    "S": 4,
    "Q": 4,
    "T": 2,
    "A": 7,
}


@dataclass(frozen=True)
class SvgDiagnostic:
    """One actionable preflight finding."""

    severity: str
    code: str
    message: str
    element_path: Optional[str] = None
    element_id: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "element_path": self.element_path,
            "element_id": self.element_id,
        }


@dataclass(frozen=True)
class SvgViewport:
    """Root viewport expressed in millimetres and its user-space mapping."""

    width_mm: float
    height_mm: float
    view_box: Optional[tuple[float, float, float, float]]
    preserve_aspect_ratio: str
    user_to_mm: Matrix

    def to_dict(self) -> dict[str, object]:
        return {
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "view_box": list(self.view_box) if self.view_box is not None else None,
            "preserve_aspect_ratio": self.preserve_aspect_ratio,
            "user_to_mm": list(self.user_to_mm),
        }


@dataclass(frozen=True)
class SvgCandidate:
    """A visible, filled, closed geometry candidate for FreeCAD import."""

    tag: str
    element_path: str
    element_id: Optional[str]
    subpath_count: int
    fill_rule: str
    transform_depth: int
    local_to_user: Matrix
    local_to_mm: Matrix
    attributes: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "element_path": self.element_path,
            "element_id": self.element_id,
            "subpath_count": self.subpath_count,
            "fill_rule": self.fill_rule,
            "transform_depth": self.transform_depth,
            "local_to_user": list(self.local_to_user),
            "local_to_mm": list(self.local_to_mm),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class SvgMetadata:
    """Normalized document information consumed by the FreeCAD wrapper."""

    viewport: SvgViewport
    candidate_count: int
    has_nested_transforms: bool
    nested_transform_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "viewport": self.viewport.to_dict(),
            "candidate_count": self.candidate_count,
            "has_nested_transforms": self.has_nested_transforms,
            "nested_transform_paths": list(self.nested_transform_paths),
        }


@dataclass(frozen=True)
class SvgPreflightResult:
    """Complete fail-closed result for one SVG source."""

    source_name: str
    metadata: Optional[SvgMetadata]
    candidates: tuple[SvgCandidate, ...]
    diagnostics: tuple[SvgDiagnostic, ...]

    @property
    def fatal_diagnostics(self) -> tuple[SvgDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == FATAL)

    @property
    def warning_diagnostics(self) -> tuple[SvgDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == WARNING)

    @property
    def is_importable(self) -> bool:
        return self.metadata is not None and bool(self.candidates) and not self.fatal_diagnostics

    def require_importable(self) -> "SvgPreflightResult":
        """Raise with all fatal findings instead of allowing a partial import."""

        if not self.is_importable:
            findings = self.fatal_diagnostics
            if findings:
                detail = "; ".join(f"{item.code}: {item.message}" for item in findings)
            else:
                detail = "SVG contains no visible, filled, closed geometry"
            raise SvgPreflightError(f"{self.source_name} is not importable: {detail}")
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "is_importable": self.is_importable,
            "metadata": self.metadata.to_dict() if self.metadata is not None else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


class SvgPreflightError(ValueError):
    """Raised when a caller attempts to import a failed preflight result."""


@dataclass
class _PathSubpath:
    points: list[tuple[float, float]]
    closed: bool = False
    drawable_segments: int = 0
    linear_only: bool = True


@dataclass(frozen=True)
class _PathValidation:
    subpath_count: int
    open_subpaths: int
    errors: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _PaintContext:
    fill: str = "black"
    stroke: str = "none"
    fill_opacity: float = 1.0
    stroke_opacity: float = 1.0
    opacity_product: float = 1.0
    visibility: str = "visible"
    displayed: bool = True
    fill_rule: str = "nonzero"
    geometry_effects: tuple[str, ...] = ()


def preflight_svg_file(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> SvgPreflightResult:
    """Read and preflight an SVG file without resolving external resources."""

    svg_path = Path(path)
    try:
        data = svg_path.read_bytes()
    except OSError as exc:
        return _failed_result(
            str(svg_path),
            "SVG_READ_ERROR",
            f"Could not read SVG: {exc}",
        )
    return preflight_svg(data, source_name=str(svg_path), max_bytes=max_bytes)


def preflight_svg(
    svg: str | bytes,
    *,
    source_name: str = "<memory>",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> SvgPreflightResult:
    """Validate SVG XML and return normalized, FreeCAD-independent metadata.

    Fatal diagnostics mean the wrapper must not invoke FreeCAD's importer.  A
    warning is reserved for a safe, explicit normalization or ignored hidden
    content; warnings never mean that visible artwork was silently discarded.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    raw = svg.encode("utf-8") if isinstance(svg, str) else bytes(svg)
    if len(raw) > max_bytes:
        return _failed_result(
            source_name,
            "SVG_TOO_LARGE",
            f"SVG is {len(raw)} bytes; the preflight limit is {max_bytes} bytes.",
        )
    # Removing NUL code units lets the security probe recognize declarations
    # in UTF-16/UTF-32 input before ElementTree sees them.
    security_probe = raw.replace(b"\x00", b"")
    if _FORBIDDEN_XML_RE.search(security_probe):
        return _failed_result(
            source_name,
            "UNSAFE_XML_DECLARATION",
            "DOCTYPE, ENTITY, and external stylesheet declarations are not accepted; export plain SVG XML.",
        )

    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        return _failed_result(
            source_name,
            "MALFORMED_XML",
            f"SVG XML could not be parsed: {exc}",
        )

    root_namespace, root_tag = _split_tag(root.tag)
    if root_tag != "svg" or root_namespace not in {"", SVG_NAMESPACE}:
        return _failed_result(
            source_name,
            "NOT_AN_SVG_ROOT",
            "The document root must be an <svg> element in the SVG namespace.",
        )

    diagnostics: list[SvgDiagnostic] = []
    viewport = _parse_viewport(root, diagnostics)
    if viewport is None:
        return SvgPreflightResult(source_name, None, (), tuple(diagnostics))

    candidates: list[SvgCandidate] = []
    nested_paths: list[str] = []
    _walk_svg(
        root,
        path="/svg[1]",
        viewport=viewport,
        parent_transform=IDENTITY_MATRIX,
        transform_depth=0,
        paint=_PaintContext(),
        inside_definitions=False,
        is_root=True,
        element_depth=0,
        candidates=candidates,
        diagnostics=diagnostics,
        nested_paths=nested_paths,
    )

    if not candidates and not any(item.severity == FATAL for item in diagnostics):
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "NO_IMPORTABLE_GEOMETRY",
                "No visible, filled, closed SVG geometry was found. Add a closed filled path or shape.",
                "/svg[1]",
            )
        )

    metadata = SvgMetadata(
        viewport=viewport,
        candidate_count=len(candidates),
        has_nested_transforms=bool(nested_paths),
        nested_transform_paths=tuple(nested_paths),
    )
    return SvgPreflightResult(source_name, metadata, tuple(candidates), tuple(diagnostics))


def parse_length_mm(value: str) -> float:
    """Convert an absolute SVG/CSS length to millimetres at 96 CSS px/in."""

    match = _LENGTH_RE.match(value)
    if not match:
        raise ValueError(
            f"Unsupported length {value!r}; use an absolute mm, cm, in, pt, pc, px, or unitless value."
        )
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    millimetres = number * _LENGTH_FACTORS[unit]
    if not math.isfinite(millimetres):
        raise ValueError(f"Length {value!r} is not finite")
    return millimetres


def apply_matrix(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    """Apply an SVG affine matrix to a point."""

    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def path_winding_signs(raw: str) -> tuple[int, ...]:
    """Return source winding signs for a closed linear compound path.

    FreeCAD's ``importSVG`` turns nested path wires into faces and may reverse
    inner wires while doing so.  The geometry wrapper uses these source signs
    to restore SVG's ``nonzero`` rule.  Curved, compound nonzero paths are
    rejected explicitly instead of guessing; ordinary curved paths and
    ``evenodd`` compounds continue through FreeCAD's importer.
    """

    tokens = _tokenize_path(raw)
    signs: list[int] = []
    points: list[tuple[float, float]] = []
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    command: Optional[str] = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _COMMAND_RE.match(token):
            command = token
            index += 1
            if command.upper() == "Z":
                if not points:
                    raise ValueError("Close-path Z appears before an active subpath.")
                if points[-1] != start:
                    points.append(start)
                area = _signed_area(points[:-1])
                if abs(area) <= 1e-12:
                    raise ValueError("A compound path contour has zero enclosed area.")
                signs.append(1 if area > 0.0 else -1)
                current = start
                points = []
                command = None
                continue
        elif command is None:
            raise ValueError("Unexpected coordinate without an active path command.")

        assert command is not None
        upper = command.upper()
        if upper not in {"M", "L", "H", "V"}:
            raise ValueError(
                "Compound nonzero paths containing curves are not yet safe; "
                "use fill-rule=evenodd or separate the closed paths.")
        arity = _PATH_ARITY[upper]
        if index + arity > len(tokens) or any(
            _COMMAND_RE.match(item) for item in tokens[index : index + arity]
        ):
            raise ValueError("Malformed compound path coordinate group.")
        values = [float(item) for item in tokens[index : index + arity]]
        index += arity
        endpoint = _endpoint_for_command(
            upper, values, current, command.islower())
        if upper == "M":
            if points:
                raise ValueError("Every compound-path subpath must be closed with Z.")
            start = endpoint
            points = [endpoint]
            command = "l" if command.islower() else "L"
        else:
            if not points:
                raise ValueError("Path drawing command appears before moveto.")
            points.append(endpoint)
        current = endpoint
    if points:
        raise ValueError("Every compound-path subpath must be closed with Z.")
    return tuple(signs)


def _failed_result(source_name: str, code: str, message: str) -> SvgPreflightResult:
    return SvgPreflightResult(
        source_name,
        None,
        (),
        (SvgDiagnostic(FATAL, code, message),),
    )


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _parse_viewport(root: ET.Element, diagnostics: list[SvgDiagnostic]) -> Optional[SvgViewport]:
    path = "/svg[1]"
    element_id = root.get("id")
    view_box: Optional[tuple[float, float, float, float]] = None
    raw_view_box = root.get("viewBox")
    if raw_view_box is not None:
        try:
            values = _parse_number_list(raw_view_box)
            if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                raise ValueError("viewBox must contain min-x, min-y, positive width, and positive height")
            view_box = (values[0], values[1], values[2], values[3])
        except ValueError as exc:
            diagnostics.append(SvgDiagnostic(FATAL, "INVALID_VIEWBOX", str(exc), path, element_id))

    width_mm = _parse_viewport_length(root.get("width"), "width", path, element_id, diagnostics)
    height_mm = _parse_viewport_length(root.get("height"), "height", path, element_id, diagnostics)

    if any(item.severity == FATAL for item in diagnostics):
        return None

    if view_box is not None:
        if width_mm is None and height_mm is None:
            width_mm = view_box[2] * PX_TO_MM
            height_mm = view_box[3] * PX_TO_MM
            diagnostics.append(
                SvgDiagnostic(
                    WARNING,
                    "VIEWPORT_DERIVED_FROM_VIEWBOX",
                    "width and height were absent; viewBox dimensions were normalized as 96 dpi CSS pixels.",
                    path,
                    element_id,
                )
            )
        elif width_mm is None:
            width_mm = height_mm * view_box[2] / view_box[3]  # type: ignore[operator]
            diagnostics.append(
                SvgDiagnostic(
                    WARNING,
                    "VIEWPORT_WIDTH_DERIVED",
                    "width was absent and was derived from height and the viewBox aspect ratio.",
                    path,
                    element_id,
                )
            )
        elif height_mm is None:
            height_mm = width_mm * view_box[3] / view_box[2]
            diagnostics.append(
                SvgDiagnostic(
                    WARNING,
                    "VIEWPORT_HEIGHT_DERIVED",
                    "height was absent and was derived from width and the viewBox aspect ratio.",
                    path,
                    element_id,
                )
            )
    elif width_mm is None or height_mm is None:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "MISSING_VIEWPORT",
                "SVG needs positive width and height, or a valid viewBox from which both can be derived.",
                path,
                element_id,
            )
        )
        return None

    assert width_mm is not None and height_mm is not None
    if not all(math.isfinite(value) and value > 0 for value in (width_mm, height_mm)):
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "INVALID_VIEWPORT_SIZE",
                "SVG width and height must both be finite and greater than zero.",
                path,
                element_id,
            )
        )
        return None

    raw_par = root.get("preserveAspectRatio", "xMidYMid meet").strip() or "xMidYMid meet"
    try:
        canonical_par, matrix = _viewbox_matrix(width_mm, height_mm, view_box, raw_par)
        if not all(math.isfinite(value) for value in matrix):
            raise ValueError("Viewport normalization must produce finite coordinates.")
    except ValueError as exc:
        diagnostics.append(SvgDiagnostic(FATAL, "INVALID_ASPECT_RATIO", str(exc), path, element_id))
        return None
    return SvgViewport(width_mm, height_mm, view_box, canonical_par, matrix)


def _parse_viewport_length(
    raw: Optional[str],
    name: str,
    path: str,
    element_id: Optional[str],
    diagnostics: list[SvgDiagnostic],
) -> Optional[float]:
    if raw is None or not raw.strip():
        return None
    if "%" in raw:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "PERCENT_VIEWPORT_UNSUPPORTED",
                f"Root {name}={raw!r} is relative; export an absolute size such as mm or px.",
                path,
                element_id,
            )
        )
        return None
    try:
        return parse_length_mm(raw)
    except ValueError as exc:
        diagnostics.append(SvgDiagnostic(FATAL, "INVALID_VIEWPORT_LENGTH", str(exc), path, element_id))
        return None


def _viewbox_matrix(
    width_mm: float,
    height_mm: float,
    view_box: Optional[tuple[float, float, float, float]],
    preserve_aspect_ratio: str,
) -> tuple[str, Matrix]:
    if view_box is None:
        if preserve_aspect_ratio != "xMidYMid meet":
            raise ValueError("preserveAspectRatio requires a viewBox")
        return preserve_aspect_ratio, (PX_TO_MM, 0.0, 0.0, PX_TO_MM, 0.0, 0.0)

    tokens = preserve_aspect_ratio.split()
    if tokens and tokens[0] == "defer":
        tokens = tokens[1:]
    if not tokens:
        tokens = ["xMidYMid", "meet"]

    align = tokens[0]
    mode = tokens[1] if len(tokens) > 1 else "meet"
    if len(tokens) > 2:
        raise ValueError(f"Unsupported preserveAspectRatio value {preserve_aspect_ratio!r}")
    allowed_align = {
        "none",
        "xMinYMin",
        "xMidYMin",
        "xMaxYMin",
        "xMinYMid",
        "xMidYMid",
        "xMaxYMid",
        "xMinYMax",
        "xMidYMax",
        "xMaxYMax",
    }
    if align not in allowed_align or mode not in {"meet", "slice"}:
        raise ValueError(f"Unsupported preserveAspectRatio value {preserve_aspect_ratio!r}")

    min_x, min_y, box_width, box_height = view_box
    scale_x = width_mm / box_width
    scale_y = height_mm / box_height
    if align == "none":
        canonical = "none"
        return canonical, (
            scale_x,
            0.0,
            0.0,
            scale_y,
            -min_x * scale_x,
            -min_y * scale_y,
        )

    scale = min(scale_x, scale_y) if mode == "meet" else max(scale_x, scale_y)
    spare_x = width_mm - box_width * scale
    spare_y = height_mm - box_height * scale
    x_factor = 0.0 if align.startswith("xMin") else 0.5 if align.startswith("xMid") else 1.0
    y_factor = 0.0 if align.endswith("YMin") else 0.5 if align.endswith("YMid") else 1.0
    translate_x = spare_x * x_factor - min_x * scale
    translate_y = spare_y * y_factor - min_y * scale
    canonical = f"{align} {mode}"
    return canonical, (scale, 0.0, 0.0, scale, translate_x, translate_y)


def _walk_svg(
    element: ET.Element,
    *,
    path: str,
    viewport: SvgViewport,
    parent_transform: Matrix,
    transform_depth: int,
    paint: _PaintContext,
    inside_definitions: bool,
    is_root: bool,
    element_depth: int,
    candidates: list[SvgCandidate],
    diagnostics: list[SvgDiagnostic],
    nested_paths: list[str],
) -> None:
    namespace, tag = _split_tag(element.tag)
    element_id = element.get("id")
    if element_depth > MAX_ELEMENT_DEPTH:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "SVG_NESTING_TOO_DEEP",
                f"SVG element nesting exceeds the safe limit of {MAX_ELEMENT_DEPTH}; flatten nested groups.",
                path,
                element_id,
            )
        )
        return
    if namespace not in {"", SVG_NAMESPACE} and (inside_definitions or tag == "namedview"):
        diagnostics.append(
            SvgDiagnostic(
                WARNING,
                "FOREIGN_METADATA_IGNORED",
                f"Non-rendering metadata element <{tag}> was explicitly ignored.",
                path,
                element_id,
            )
        )
        return
    if namespace not in {"", SVG_NAMESPACE}:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "FOREIGN_NAMESPACE",
                f"Foreign element <{tag}> is not supported; convert visible content to ordinary SVG paths.",
                path,
                element_id,
            )
        )
        return

    local_transform = IDENTITY_MATRIX
    has_local_transform = bool(element.get("transform", "").strip())
    if has_local_transform:
        try:
            local_transform = _parse_transform(element.get("transform", ""))
        except ValueError as exc:
            diagnostics.append(SvgDiagnostic(FATAL, "INVALID_TRANSFORM", str(exc), path, element_id))
    current_transform = _matrix_multiply(parent_transform, local_transform)
    if not all(math.isfinite(value) for value in current_transform):
        diagnostics.append(SvgDiagnostic(
            FATAL, "INVALID_TRANSFORM", "Combined transforms must be finite.", path, element_id))
        current_transform = IDENTITY_MATRIX
    current_depth = transform_depth + int(has_local_transform)
    if has_local_transform and transform_depth > 0:
        nested_paths.append(path)

    try:
        current_paint = _merge_paint(paint, element)
    except ValueError as exc:
        diagnostics.append(SvgDiagnostic(FATAL, "INVALID_STYLE", str(exc), path, element_id))
        current_paint = paint

    if not is_root and tag == "svg":
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "NESTED_VIEWPORT_UNSUPPORTED",
                "Nested <svg> viewports are not normalized; flatten the artwork to one root SVG viewport.",
                path,
                element_id,
            )
        )

    definitions_here = inside_definitions or tag in _DEFINITION_TAGS or tag == "metadata"
    if tag == "style" and (element.text or "").strip():
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "STYLESHEET_UNSUPPORTED",
                "Embedded CSS is not evaluated; apply fill, stroke, and visibility as inline SVG attributes.",
                path,
                element_id,
            )
        )
    elif tag in _EXPLICITLY_UNSUPPORTED and not definitions_here and current_paint.displayed:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                f"UNSUPPORTED_{_diagnostic_name(tag)}",
                f"Visible <{tag}> content is unsupported. {_EXPLICITLY_UNSUPPORTED[tag]}",
                path,
                element_id,
            )
        )
    elif tag in _GEOMETRY_TAGS and not definitions_here:
        _inspect_geometry(
            element,
            tag=tag,
            path=path,
            paint=current_paint,
            transform=current_transform,
            transform_depth=current_depth,
            viewport=viewport,
            candidates=candidates,
            diagnostics=diagnostics,
        )
    elif (
        tag not in _GEOMETRY_TAGS
        and tag not in _CONTAINER_TAGS
        and tag not in _DEFINITION_TAGS
        and tag not in _PASSIVE_TAGS
        and tag not in _EXPLICITLY_UNSUPPORTED
        and not definitions_here
    ):
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "UNSUPPORTED_ELEMENT",
                f"Element <{tag}> is unsupported; convert it to ordinary closed paths.",
                path,
                element_id,
            )
        )

    tag_counts: dict[str, int] = {}
    for child in list(element):
        _, child_tag = _split_tag(child.tag)
        tag_counts[child_tag] = tag_counts.get(child_tag, 0) + 1
        _walk_svg(
            child,
            path=f"{path}/{child_tag}[{tag_counts[child_tag]}]",
            viewport=viewport,
            parent_transform=current_transform,
            transform_depth=current_depth,
            paint=current_paint,
            inside_definitions=definitions_here,
            is_root=False,
            element_depth=element_depth + 1,
            candidates=candidates,
            diagnostics=diagnostics,
            nested_paths=nested_paths,
        )


def _inspect_geometry(
    element: ET.Element,
    *,
    tag: str,
    path: str,
    paint: _PaintContext,
    transform: Matrix,
    transform_depth: int,
    viewport: SvgViewport,
    candidates: list[SvgCandidate],
    diagnostics: list[SvgDiagnostic],
) -> None:
    element_id = element.get("id")
    if not paint.displayed or paint.visibility in {"hidden", "collapse"} or paint.opacity_product <= 0:
        diagnostics.append(
            SvgDiagnostic(
                WARNING,
                "HIDDEN_GEOMETRY_IGNORED",
                "Hidden geometry was explicitly ignored.",
                path,
                element_id,
            )
        )
        return

    if paint.geometry_effects:
        diagnostics.append(
            SvgDiagnostic(
                FATAL,
                "UNSUPPORTED_GEOMETRY_EFFECT",
                "Clipping, masks, and filters on geometry or its ancestors are not normalized; "
                "flatten the visible result to paths.",
                path,
                element_id,
            )
        )

    fill = paint.fill.strip().lower()
    stroke = paint.stroke.strip().lower()
    has_fill = fill not in {"none", "transparent"} and paint.fill_opacity * paint.opacity_product > 0
    has_stroke = stroke not in {"none", "transparent"} and paint.stroke_opacity * paint.opacity_product > 0
    if not has_fill:
        if has_stroke:
            diagnostics.append(
                SvgDiagnostic(
                    FATAL,
                    "STROKE_ONLY_GEOMETRY",
                    "Stroke-only artwork has no solid pocket area; expand the stroke to a filled closed path.",
                    path,
                    element_id,
                )
            )
        else:
            diagnostics.append(
                SvgDiagnostic(
                    WARNING,
                    "UNPAINTED_GEOMETRY_IGNORED",
                    "Geometry with no visible fill or stroke was explicitly ignored.",
                    path,
                    element_id,
                )
            )
        return

    local_to_mm = _matrix_multiply(viewport.user_to_mm, transform)
    if not all(math.isfinite(value) for value in local_to_mm):
        diagnostics.append(SvgDiagnostic(
            FATAL, "INVALID_TRANSFORM", "Normalized transforms must be finite.", path, element_id))
        return

    errors: list[tuple[str, str]] = []
    points_to_check: list[tuple[float, float]] = []
    subpath_count = 1
    if tag == "path":
        validation = _validate_path_data(element.get("d", ""), local_to_mm)
        errors.extend(validation.errors)
        subpath_count = validation.subpath_count
        if validation.open_subpaths:
            errors.append(
                (
                    "OPEN_PATH",
                    f"Path contains {validation.open_subpaths} open subpath(s); "
                    "close each contour with Z before importing.",
                )
            )
    elif tag in {"line", "polyline"}:
        errors.append(
            (
                "OPEN_PATH",
                f"<{tag}> is open geometry; convert it to a filled closed path or polygon.",
            )
        )
        if tag == "polyline":
            _validate_points_attribute(element.get("points", ""), closed=False, errors=errors)
    elif tag == "polygon":
        points = _validate_points_attribute(element.get("points", ""), closed=True, errors=errors)
        points_to_check = points
        if points and _polygon_has_self_intersection(points):
            errors.append(
                (
                    "SELF_INTERSECTING_GEOMETRY",
                    "Polygon edges cross; simplify it into one or more non-self-intersecting closed contours.",
                )
            )
        elif points and abs(_signed_area(points)) <= 1e-12:
            errors.append(("ZERO_AREA_GEOMETRY", "Polygon has zero enclosed area."))
    elif tag == "rect":
        _validate_positive_geometry_length(element, "width", errors)
        _validate_positive_geometry_length(element, "height", errors)
        for name in ("x", "y", "rx", "ry"):
            _validate_optional_geometry_length(element, name, errors)
    elif tag == "circle":
        _validate_positive_geometry_length(element, "r", errors)
        for name in ("cx", "cy"):
            _validate_optional_geometry_length(element, name, errors)
    elif tag == "ellipse":
        _validate_positive_geometry_length(element, "rx", errors)
        _validate_positive_geometry_length(element, "ry", errors)
        for name in ("cx", "cy"):
            _validate_optional_geometry_length(element, name, errors)

    if not errors:
        try:
            if tag in {"rect", "circle", "ellipse"}:
                points_to_check = _primitive_bound_points(element, tag)
            _require_finite_geometry_points(points_to_check, local_to_mm)
        except ValueError as exc:
            errors.append(("INVALID_GEOMETRY_VALUE", str(exc)))

    fill_rule = paint.fill_rule
    if fill_rule not in {"nonzero", "evenodd"}:
        errors.append(
            (
                "UNSUPPORTED_FILL_RULE",
                f"fill-rule={fill_rule!r} is unsupported; use 'nonzero' or 'evenodd'.",
            )
        )

    for code, message in _deduplicate(errors):
        diagnostics.append(SvgDiagnostic(FATAL, code, message, path, element_id))
    if errors:
        return

    raw_attributes = {
        _split_tag(key)[1]: value
        for key, value in sorted(element.attrib.items())
        if _split_tag(key)[1] not in {"style"}
    }
    candidates.append(
        SvgCandidate(
            tag=tag,
            element_path=path,
            element_id=element_id,
            subpath_count=subpath_count,
            fill_rule=fill_rule,
            transform_depth=transform_depth,
            local_to_user=transform,
            local_to_mm=local_to_mm,
            attributes=MappingProxyType(raw_attributes),
        )
    )


def _primitive_bound_points(element: ET.Element, tag: str) -> list[tuple[float, float]]:
    def value(name: str) -> float:
        # Absolute element lengths resolve to CSS user units before the
        # viewport and element transforms are applied.
        return parse_length_mm(element.get(name, "0")) / PX_TO_MM

    if tag == "rect":
        left, bottom = value("x"), value("y")
        right, top = left + value("width"), bottom + value("height")
    else:
        cx, cy = value("cx"), value("cy")
        rx = value("r" if tag == "circle" else "rx")
        ry = value("r" if tag == "circle" else "ry")
        left, right, bottom, top = cx - rx, cx + rx, cy - ry, cy + ry
    return [(left, bottom), (right, bottom), (right, top), (left, top)]


def _require_finite_geometry_points(
    points: Iterable[tuple[float, float]], transform: Matrix,
) -> None:
    for point in points:
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Resolved geometry coordinates must be finite.")
        if not all(math.isfinite(value) for value in apply_matrix(transform, *point)):
            raise ValueError("Transformed geometry coordinates must be finite.")


def _validate_optional_geometry_length(
    element: ET.Element,
    name: str,
    errors: list[tuple[str, str]],
) -> None:
    raw = element.get(name)
    if raw is not None:
        try:
            parse_length_mm(raw)
        except ValueError as exc:
            errors.append(("INVALID_GEOMETRY_VALUE", f"Invalid {name}: {exc}"))


def _validate_positive_geometry_length(
    element: ET.Element,
    name: str,
    errors: list[tuple[str, str]],
) -> None:
    raw = element.get(name)
    if raw is None or not raw.strip():
        errors.append(("MISSING_GEOMETRY_VALUE", f"<{_split_tag(element.tag)[1]}> requires {name}."))
        return
    if "%" in raw:
        errors.append(
            (
                "RELATIVE_GEOMETRY_UNSUPPORTED",
                f"Geometry value {name}={raw!r} is relative; convert it to an absolute user-space value.",
            )
        )
        return
    try:
        value_mm = parse_length_mm(raw)
    except ValueError as exc:
        errors.append(("INVALID_GEOMETRY_VALUE", str(exc)))
        return
    if value_mm <= 0:
        errors.append(("ZERO_AREA_GEOMETRY", f"Geometry value {name} must be greater than zero."))


def _validate_points_attribute(
    raw: str,
    *,
    closed: bool,
    errors: list[tuple[str, str]],
) -> list[tuple[float, float]]:
    try:
        values = _parse_number_list(raw)
    except ValueError as exc:
        errors.append(("MALFORMED_POINTS", str(exc)))
        return []
    minimum = 6 if closed else 4
    if len(values) < minimum or len(values) % 2:
        errors.append(
            (
                "MALFORMED_POINTS",
                f"points must contain at least {minimum // 2} coordinate pairs and an even number of values.",
            )
        )
        return []
    points = list(zip(values[0::2], values[1::2]))
    if closed and len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _validate_path_data(raw: str, transform: Matrix = IDENTITY_MATRIX) -> _PathValidation:
    if not raw.strip():
        return _PathValidation(0, 0, (("MALFORMED_PATH_DATA", "Path data is empty."),))
    try:
        tokens = _tokenize_path(raw)
    except ValueError as exc:
        return _PathValidation(0, 0, (("MALFORMED_PATH_DATA", str(exc)),))

    errors: list[tuple[str, str]] = []
    subpaths: list[_PathSubpath] = []
    active: Optional[_PathSubpath] = None
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    command: Optional[str] = None
    previous_curve: Optional[str] = None
    previous_control: Optional[tuple[float, float]] = None
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if _COMMAND_RE.match(token):
            command = token
            index += 1
            if command.upper() == "Z":
                if active is None:
                    errors.append(("MALFORMED_PATH_DATA", "Close-path Z appears before an active subpath."))
                    command = None
                    continue
                active.closed = True
                if active.points[-1] != start:
                    active.points.append(start)
                current = start
                subpaths.append(active)
                active = None
                command = None
                previous_curve = None
                previous_control = None
                continue
            if index >= len(tokens) or _COMMAND_RE.match(tokens[index]):
                errors.append(("MALFORMED_PATH_DATA", f"Command {command} has no coordinates."))
                continue
        elif command is None:
            errors.append(("MALFORMED_PATH_DATA", f"Unexpected number {token!r} without a path command."))
            index += 1
            continue

        assert command is not None
        upper = command.upper()
        arity = _PATH_ARITY[upper]
        if index + arity > len(tokens) or any(
            _COMMAND_RE.match(item) for item in tokens[index : index + arity]
        ):
            errors.append(
                (
                    "MALFORMED_PATH_DATA",
                    f"Command {command} needs groups of {arity} numeric value(s).",
                )
            )
            while index < len(tokens) and not _COMMAND_RE.match(tokens[index]):
                index += 1
            command = None
            continue

        values = [float(item) for item in tokens[index : index + arity]]
        index += arity
        if upper == "A" and (values[3] not in {0.0, 1.0} or values[4] not in {0.0, 1.0}):
            errors.append(
                (
                    "MALFORMED_PATH_DATA",
                    "Arc large-arc and sweep flags must each be 0 or 1.",
                )
            )

        relative = command.islower()
        if upper == "M":
            if active is not None:
                subpaths.append(active)
            try:
                point = _endpoint_for_command(upper, values, current, relative)
                _require_finite_geometry_points([point], transform)
            except ValueError as exc:
                return _PathValidation(0, 0, (("MALFORMED_PATH_DATA", str(exc)),))
            current = point
            start = point
            active = _PathSubpath(points=[point])
            command = "l" if relative else "L"
            previous_curve = None
            previous_control = None
            continue

        if active is None:
            errors.append(
                (
                    "MALFORMED_PATH_DATA",
                    f"Command {command} appears before a moveto command starts a subpath.",
                )
            )
            continue

        try:
            endpoint = _endpoint_for_command(upper, values, current, relative)
            controls = []
            if upper in {"C", "S", "Q"}:
                controls = [
                    (values[i] + (current[0] if relative else 0.0),
                     values[i + 1] + (current[1] if relative else 0.0))
                    for i in range(0, len(values) - 2, 2)
                ]
            curve = "cubic" if upper in {"C", "S"} else "quadratic" if upper in {"Q", "T"} else None
            if upper in {"S", "T"}:
                reflected = current
                if previous_curve == curve and previous_control is not None:
                    reflected = (
                        current[0] + (current[0] - previous_control[0]),
                        current[1] + (current[1] - previous_control[1]),
                    )
                controls.insert(0, reflected)
            _require_finite_geometry_points([endpoint, *controls], transform)
            previous_curve = curve
            previous_control = controls[-1] if controls else None
        except ValueError as exc:
            return _PathValidation(0, 0, (("MALFORMED_PATH_DATA", str(exc)),))
        active.drawable_segments += 1
        if upper not in {"L", "H", "V"}:
            active.linear_only = False
        active.points.append(endpoint)
        current = endpoint

    if active is not None:
        subpaths.append(active)

    if not subpaths:
        errors.append(("MALFORMED_PATH_DATA", "Path has no moveto subpath."))
    for subpath in subpaths:
        if subpath.drawable_segments == 0:
            errors.append(("PATH_NO_DRAWABLE_SEGMENTS", "A subpath contains no drawable segments."))
        if subpath.closed and subpath.linear_only and len(subpath.points) >= 4:
            polygon = subpath.points[:-1] if subpath.points[0] == subpath.points[-1] else subpath.points
            if _polygon_has_self_intersection(polygon):
                errors.append(
                    (
                        "SELF_INTERSECTING_GEOMETRY",
                        "A linear path contour crosses itself; split or simplify the contour before importing.",
                    )
                )
            elif abs(_signed_area(polygon)) <= 1e-12:
                errors.append(("ZERO_AREA_GEOMETRY", "A closed path contour has zero enclosed area."))

    linear_subpaths = [
        item for item in subpaths if item.closed and item.linear_only and len(item.points) >= 4
    ]
    if _subpaths_intersect(linear_subpaths):
        errors.append(
            (
                "INTERSECTING_SUBPATHS",
                "Two path contours cross or touch; create clean nested or disjoint contours before importing.",
            )
        )

    open_count = sum(not subpath.closed for subpath in subpaths)
    return _PathValidation(len(subpaths), open_count, tuple(_deduplicate(errors)))


def _tokenize_path(raw: str) -> list[str]:
    tokens: list[str] = []
    end = 0
    for match in _PATH_TOKEN_RE.finditer(raw):
        gap = raw[end : match.start()]
        if not _SAFE_SEPARATORS_RE.match(gap):
            raise ValueError(f"Unexpected path data near {gap!r}.")
        token = match.group(0)
        if not _COMMAND_RE.match(token) and not math.isfinite(float(token)):
            raise ValueError("Path coordinates must be finite.")
        tokens.append(token)
        end = match.end()
    tail = raw[end:]
    if not _SAFE_SEPARATORS_RE.match(tail):
        raise ValueError(f"Unexpected path data near {tail!r}.")
    if not tokens:
        raise ValueError("Path data has no commands or coordinates.")
    return tokens


def _endpoint_for_command(
    command: str,
    values: Sequence[float],
    current: tuple[float, float],
    relative: bool,
) -> tuple[float, float]:
    x, y = current
    if command == "H":
        endpoint = (values[0], y)
    elif command == "V":
        endpoint = (x, values[0])
    else:
        endpoint = (values[-2], values[-1])
    if relative:
        if command == "H":
            endpoint = (x + values[0], y)
        elif command == "V":
            endpoint = (x, y + values[0])
        else:
            endpoint = (x + values[-2], y + values[-1])
    if not all(math.isfinite(value) for value in endpoint):
        raise ValueError("Resolved path coordinates must be finite.")
    return endpoint


def _parse_transform(raw: str) -> Matrix:
    if not raw.strip():
        return IDENTITY_MATRIX
    result = IDENTITY_MATRIX
    end = 0
    found = False
    for match in _TRANSFORM_RE.finditer(raw):
        found = True
        gap = raw[end : match.start()]
        if not _SAFE_SEPARATORS_RE.match(gap):
            raise ValueError(f"Unexpected transform syntax near {gap!r}.")
        name = match.group(1)
        values = _parse_number_list(match.group(2))
        operation = _transform_operation(name, values)
        result = _matrix_multiply(result, operation)
        if not all(math.isfinite(value) for value in result):
            raise ValueError("Combined transforms must be finite.")
        end = match.end()
    if not found or not _SAFE_SEPARATORS_RE.match(raw[end:]):
        raise ValueError(f"Malformed transform {raw!r}.")
    return result


def _transform_operation(name: str, values: Sequence[float]) -> Matrix:
    if name == "matrix" and len(values) == 6:
        return tuple(values)  # type: ignore[return-value]
    if name == "translate" and len(values) in {1, 2}:
        return (1.0, 0.0, 0.0, 1.0, values[0], values[1] if len(values) == 2 else 0.0)
    if name == "scale" and len(values) in {1, 2}:
        return (values[0], 0.0, 0.0, values[1] if len(values) == 2 else values[0], 0.0, 0.0)
    if name == "rotate" and len(values) in {1, 3}:
        angle = math.radians(values[0])
        cosine = math.cos(angle)
        sine = math.sin(angle)
        rotation: Matrix = (cosine, sine, -sine, cosine, 0.0, 0.0)
        if len(values) == 1:
            return rotation
        cx, cy = values[1], values[2]
        return _matrix_multiply(
            _matrix_multiply((1.0, 0.0, 0.0, 1.0, cx, cy), rotation),
            (1.0, 0.0, 0.0, 1.0, -cx, -cy),
        )
    if name in {"skewX", "skewY"} and len(values) == 1:
        tangent = math.tan(math.radians(values[0]))
        return (1.0, 0.0, tangent, 1.0, 0.0, 0.0) if name == "skewX" else (
            1.0,
            tangent,
            0.0,
            1.0,
            0.0,
            0.0,
        )
    raise ValueError(f"Unsupported or malformed transform {name}({', '.join(map(str, values))}).")


def _matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re_, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re_ + lc * rf + le,
        lb * re_ + ld * rf + lf,
    )


def _merge_paint(parent: _PaintContext, element: ET.Element) -> _PaintContext:
    inline = _parse_inline_style(element.get("style", ""))

    def property_value(name: str, inherited: str) -> str:
        value = inline.get(name, element.get(name, inherited)).strip()
        return inherited if value.lower() == "inherit" else value

    fill = property_value("fill", parent.fill)
    stroke = property_value("stroke", parent.stroke)
    fill_opacity = _parse_opacity(property_value("fill-opacity", str(parent.fill_opacity)), "fill-opacity")
    stroke_opacity = _parse_opacity(
        property_value("stroke-opacity", str(parent.stroke_opacity)), "stroke-opacity"
    )
    local_opacity = _parse_opacity(property_value("opacity", "1"), "opacity")
    visibility = property_value("visibility", parent.visibility).lower()
    display = property_value("display", "inline").lower()
    fill_rule = property_value("fill-rule", parent.fill_rule).lower()
    local_effects = tuple(
        name for name in ("clip-path", "mask", "filter")
        if property_value(name, "none").lower() != "none"
    )
    return _PaintContext(
        fill=fill,
        stroke=stroke,
        fill_opacity=fill_opacity,
        stroke_opacity=stroke_opacity,
        opacity_product=parent.opacity_product * local_opacity,
        visibility=visibility,
        displayed=parent.displayed and display != "none",
        fill_rule=fill_rule,
        # A child's 'none' cannot cancel an effect applied to its parent's
        # rendered group. Keep ancestor effects until visible geometry is found.
        geometry_effects=tuple(dict.fromkeys(parent.geometry_effects + local_effects)),
    )


def _parse_inline_style(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    result: dict[str, str] = {}
    important_properties: set[str] = set()
    for declaration in raw.split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            raise ValueError(f"Malformed inline style declaration {declaration!r}.")
        name, value = declaration.split(":", 1)
        name = name.strip().lower()
        value = value.strip()
        priority = re.search(r"!\s*important\s*$", value, re.IGNORECASE)
        if priority:
            value = value[:priority.start()].rstrip()
        if not name or not value:
            raise ValueError(f"Malformed inline style declaration {declaration!r}.")
        if priority or name not in important_properties:
            result[name] = value
        if priority:
            important_properties.add(name)
    return result


def _parse_opacity(raw: str, name: str) -> float:
    try:
        if raw.endswith("%"):
            value = float(raw[:-1]) / 100.0
        else:
            value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _parse_number_list(raw: str) -> list[float]:
    values: list[float] = []
    end = 0
    for match in _NUMBER_RE.finditer(raw):
        gap = raw[end : match.start()]
        if not _SAFE_SEPARATORS_RE.match(gap):
            raise ValueError(f"Unexpected numeric-list syntax near {gap!r}.")
        value = float(match.group(0))
        if not math.isfinite(value):
            raise ValueError("Numeric values must be finite")
        values.append(value)
        end = match.end()
    if not _SAFE_SEPARATORS_RE.match(raw[end:]):
        raise ValueError(f"Unexpected numeric-list syntax near {raw[end:]!r}.")
    return values


def _polygon_has_self_intersection(points: Sequence[tuple[float, float]]) -> bool:
    if len(points) < 4:
        return False
    segments = [(points[index], points[(index + 1) % len(points)]) for index in range(len(points))]
    for first_index, first in enumerate(segments):
        for second_index in range(first_index + 1, len(segments)):
            if second_index in {first_index, first_index + 1}:
                continue
            if first_index == 0 and second_index == len(segments) - 1:
                continue
            if _segments_intersect(first[0], first[1], segments[second_index][0], segments[second_index][1]):
                return True
    return False


def _subpaths_intersect(subpaths: Sequence[_PathSubpath]) -> bool:
    for first_index, first in enumerate(subpaths):
        first_segments = list(zip(first.points, first.points[1:]))
        for second in subpaths[first_index + 1 :]:
            second_segments = list(zip(second.points, second.points[1:]))
            for first_segment in first_segments:
                for second_segment in second_segments:
                    if _segments_intersect(*first_segment, *second_segment):
                        return True
    return False


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    epsilon = 1e-12

    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> bool:
        return (
            min(p[0], r[0]) - epsilon <= q[0] <= max(p[0], r[0]) + epsilon
            and min(p[1], r[1]) - epsilon <= q[1] <= max(p[1], r[1]) + epsilon
        )

    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    if (o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon) and (
        o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon
    ):
        return True
    if abs(o1) <= epsilon and on_segment(a, c, b):
        return True
    if abs(o2) <= epsilon and on_segment(a, d, b):
        return True
    if abs(o3) <= epsilon and on_segment(c, a, d):
        return True
    if abs(o4) <= epsilon and on_segment(c, b, d):
        return True
    return False


def _signed_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        point[0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * point[1]
        for index, point in enumerate(points)
    )


def _deduplicate(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _diagnostic_name(tag: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", tag).upper()


__all__ = [
    "DEFAULT_MAX_BYTES",
    "FATAL",
    "MAX_ELEMENT_DEPTH",
    "PX_TO_MM",
    "WARNING",
    "SvgCandidate",
    "SvgDiagnostic",
    "SvgMetadata",
    "SvgPreflightError",
    "SvgPreflightResult",
    "SvgViewport",
    "apply_matrix",
    "parse_length_mm",
    "preflight_svg",
    "preflight_svg_file",
]
