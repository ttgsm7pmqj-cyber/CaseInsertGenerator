# SPDX-License-Identifier: LGPL-2.1-or-later
"""Generate, reopen, audit, and render the 23 generic themed examples.

Run this script inside FreeCAD.  FCStd generation works through FreeCADCmd or
the FreeCAD MCP server; native PNG rendering additionally requires one running
FreeCAD GUI process.  Every pack uses original synthetic custom-case geometry.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
from typing import Any, Mapping

import FreeCAD as App

from scripts.artifact_audit import (
    EXAMPLE_LICENSE, EXAMPLE_LICENSE_URL, scan_fcstd, text_findings,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "examples" / "themed-packs"
CATALOG_PATH = ROOT / "scripts" / "themed_example_catalog.py"
EXPECTED_COUNT = 23
RENDER_WIDTH = 1400
RENDER_HEIGHT = 1000
SOURCE_RELATIVE_PATHS = (
    "freecad/CaseInsertGenerator/engine.py",
    "freecad/CaseInsertGenerator/project_model.py",
    "freecad/CaseInsertGenerator/svg_import.py",
    "scripts/artifact_audit.py",
    "scripts/generate_themed_examples.py",
    "scripts/themed_example_catalog.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_mapping(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    for name in ("to_mapping", "to_dict"):
        method = getattr(result, name, None)
        if callable(method):
            value = method()
            if isinstance(value, Mapping):
                return dict(value)
    raise TypeError("Generation result is not mapping-compatible")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _close_document(document: Any) -> None:
    if document is None:
        return
    try:
        name = document.Name
    except Exception:
        return
    if name in App.listDocuments():
        App.closeDocument(name)


def _remove_own_freecad_backups(fcstd_path: Path) -> None:
    """Remove timestamped backups created by overwriting this one output."""
    pattern = f"{fcstd_path.stem}.*.FCBak"
    for backup in fcstd_path.parent.glob(pattern):
        if backup.is_file():
            backup.unlink()


def _new_document(number: int) -> Any:
    stem = f"ThemedExample{number:02d}"
    name = stem
    suffix = 2
    while name in App.listDocuments():
        name = f"{stem}_{suffix:02d}"
        suffix += 1
    return App.newDocument(name)


def _git_state(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        result["git_head"] = head.stdout.strip()
        result["tracked_worktree_dirty"] = bool(status.stdout.strip())
    except Exception as exc:
        result["git_state_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _publishable_git_state(root: Path) -> dict[str, Any]:
    """Require a committed, clean source snapshot for public artifact generation."""

    state = _git_state(root)
    if "git_state_error" in state or not state.get("git_head"):
        raise RuntimeError("Public examples require a committed Git source snapshot")
    if state.get("tracked_worktree_dirty"):
        raise RuntimeError("Public examples require a clean tracked worktree")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *SOURCE_RELATIVE_PATHS],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    if tracked.returncode != 0:
        raise RuntimeError("One or more example-generator source files are untracked")
    return {
        "source_commit_verified": True,
        "tracked_worktree_dirty": False,
    }


def _source_hashes(root: Path) -> dict[str, str]:
    return {
        relative: _sha256(root / relative)
        for relative in SOURCE_RELATIVE_PATHS
    }


def _clear_generated_targets(destination: Path, packs: list[Mapping[str, Any]]) -> None:
    """Remove only files owned by this generator so reruns cannot look current."""

    for filename in ("manifest.json", "contact-sheet.png", "exploded-contact-sheet.png"):
        target = destination / filename
        if target.is_file():
            target.unlink()
    for pack in packs:
        number = int(pack["number"])
        slug = str(pack["slug"])
        pack_dir = destination / f"{number:02d}-{slug}"
        for filename in (
            f"{slug}.FCStd",
            f"{slug}-exploded.FCStd",
            f"{slug}.json",
            f"{slug}.png",
            f"{slug}-exploded.png",
        ):
            target = pack_dir / filename
            if target.is_file():
                target.unlink()
        for backup in pack_dir.glob(f"{slug}*.FCBak"):
            if backup.is_file():
                backup.unlink()


def _safe_error_message(error: Exception, root: Path, destination: Path) -> str:
    """Return an actionable failure without writing workstation paths publicly."""

    message = str(error)
    for location, replacement in (
        (str(destination), "<output-root>"),
        (str(root), "<repo-root>"),
    ):
        message = message.replace(location, replacement)
    message = re.sub(r"/(?:Users|home)/[^\s<>'\"]+", "<local-path>", message,
                     flags=re.I)
    message = re.sub(r"\bfile://[^\s<>'\"]+", "<file-uri>", message,
                     flags=re.I)
    message = re.sub(r"\b[a-z]:[\\/][^\s<>'\"]+", "<local-path>", message,
                     flags=re.I)
    return message


def _portable_text_scan(fcstd_path: Path) -> dict[str, Any]:
    return scan_fcstd(fcstd_path, require_example_license=True)


def _audit_results(objects: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise RuntimeError(f"{obj.Name} has a null shape")
        if not shape.isValid() or shape.Volume <= 0.0 or len(shape.Solids) < 1:
            raise RuntimeError(f"{obj.Name} is not a positive valid solid")
        bounds = shape.BoundBox
        records.append(
            {
                "name": obj.Name,
                "label": obj.Label,
                "solid_count": len(shape.Solids),
                "volume_mm3": round(shape.Volume, 6),
                "bounds_mm": [
                    round(bounds.XLength, 6),
                    round(bounds.YLength, 6),
                    round(bounds.ZLength, 6),
                ],
            }
        )
    if not records:
        raise RuntimeError("Generated project contains no printable result objects")
    return records


def _qt_modules():
    try:
        from PySide import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _generator_role(obj: Any) -> str:
    return str(getattr(obj, "CaseInsertGeneratorRole", "") or "")


def _style_document(document: Any, project: Mapping[str, Any]) -> None:
    """Apply consistent presentation colours without changing geometry placement."""

    project_root = document.getObject("CaseInsertGeneratorProject")
    exploded_presentation = bool(
        project_root is not None
        and getattr(project_root, "ExplodedPresentation", False)
    )
    for obj in document.Objects:
        view = getattr(obj, "ViewObject", None)
        shape = getattr(obj, "Shape", None)
        if view is None or shape is None or shape.isNull():
            continue
        name = str(obj.Name)
        role = _generator_role(obj)
        if role == "project-root":
            try:
                view.Visibility = False
            except Exception:
                pass
            continue
        styled_name = (
            name[len("ExplodedPreview_"):]
            if name.startswith("ExplodedPreview_") else name
        )
        colour = (0.42, 0.63, 0.86)
        transparency = 0
        if styled_name.startswith("LowerCarrier"):
            colour = (0.25, 0.48, 0.78)
        elif styled_name.startswith("UpperCarrier"):
            colour = (0.38, 0.72, 0.55)
        elif styled_name.startswith("Bin_"):
            colour = (0.96, 0.58, 0.22)
        elif styled_name.startswith("Lid_"):
            colour = (0.98, 0.78, 0.24)
            transparency = 12
        elif styled_name.startswith("SharedRetentionPanel"):
            colour = (0.25, 0.76, 0.78)
            transparency = 58
        elif styled_name.startswith("SharedPanelClips"):
            colour = (0.84, 0.32, 0.25)
        elif role == "rim-reference" or name.startswith("CaseRimPlane"):
            colour = (0.96, 0.55, 0.12)
            transparency = 82
        elif role == "lid-reference" or name.startswith("ClosedLidCeiling"):
            colour = (0.25, 0.70, 0.95)
            transparency = 86
        try:
            if exploded_presentation and role == "result":
                view.Visibility = False
            else:
                view.Visibility = True
            view.ShapeColor = colour
            view.LineColor = tuple(max(0.0, component * 0.62) for component in colour)
            view.Transparency = transparency
            if "Flat Lines" in list(view.getDisplayModes()):
                view.DisplayMode = "Flat Lines"
        except Exception:
            pass
    document.recompute()


def _placement_copy(obj: Any) -> Any:
    """Return a detached placement copy that can safely be restored later."""

    placement = obj.Placement
    return App.Placement(placement.Base, placement.Rotation)


def _placement_snapshot(document: Any) -> dict[str, Any]:
    return {
        str(obj.Name): _placement_copy(obj)
        for obj in document.Objects
        if hasattr(obj, "Placement")
    }


def _restore_placements(document: Any, snapshot: Mapping[str, Any]) -> None:
    for name, placement in snapshot.items():
        obj = document.getObject(name)
        if obj is not None and hasattr(obj, "Placement"):
            obj.Placement = App.Placement(placement.Base, placement.Rotation)
    document.recompute()


def _presentation_reference_offset(document: Any) -> None:
    """Lift only the rim reference enough to avoid viewport z-fighting."""

    for obj in document.Objects:
        role = _generator_role(obj)
        if role == "rim-reference" or str(obj.Name).startswith("CaseRimPlane"):
            placement = _placement_copy(obj)
            placement.Base = placement.Base + App.Vector(0.0, 0.0, 1.0)
            obj.Placement = placement
    document.recompute()


def _create_exploded_previews(document: Any) -> int:
    """Copy printable shapes so presentation spacing cannot affect export results."""

    project_root = document.getObject("CaseInsertGeneratorProject")
    source_objects = [
        obj for obj in list(document.Objects)
        if _generator_role(obj) == "result"
        and getattr(obj, "Shape", None) is not None
        and not obj.Shape.isNull()
    ]
    if not source_objects:
        raise RuntimeError("Exploded presentation has no printable source objects")
    for source in source_objects:
        preview = document.addObject(
            "Part::Feature",
            "ExplodedPreview_%s" % source.Name,
        )
        preview.Label = "%s — exploded preview" % source.Label
        preview.Shape = source.Shape.copy()
        preview.addProperty(
            "App::PropertyString",
            "CaseInsertGeneratorRole",
            "Case Insert Generator",
        )
        preview.CaseInsertGeneratorRole = "exploded-preview"
        preview.addProperty(
            "App::PropertyString",
            "SourceResultObject",
            "Case Insert Generator",
        )
        preview.SourceResultObject = str(source.Name)
        preview.addProperty(
            "App::PropertyBool",
            "ExportEnabled",
            "Case Insert Generator",
        )
        preview.ExportEnabled = False
        if project_root is not None and hasattr(project_root, "addObject"):
            project_root.addObject(preview)
        source_view = getattr(source, "ViewObject", None)
        if source_view is not None:
            try:
                source_view.Visibility = False
            except Exception:
                pass
    if project_root is not None:
        if not hasattr(project_root, "ExplodedPresentation"):
            project_root.addProperty(
                "App::PropertyBool",
                "ExplodedPresentation",
                "Case Insert Generator",
            )
        project_root.ExplodedPresentation = True
        if not hasattr(project_root, "ExplodedPresentationNote"):
            project_root.addProperty(
                "App::PropertyString",
                "ExplodedPresentationNote",
                "Case Insert Generator",
            )
        project_root.ExplodedPresentationNote = (
            "Presentation spacing only. Export-enabled result objects remain assembled."
        )
    document.recompute()
    return len(source_objects)


def _part_tier(name: str, upper_ids: set[str]) -> tuple[int, str] | None:
    if name.startswith("LowerCarrier"):
        return 0, "lower carrier"
    if name.startswith("Bin_"):
        token = name[len("Bin_"):]
        return (4, "upper bins") if token in upper_ids else (1, "lower bins")
    if name.startswith("Lid_"):
        token = name[len("Lid_"):]
        return (5, "upper lids") if token in upper_ids else (2, "lower lids")
    if name.startswith("UpperCarrier"):
        return 3, "upper carrier"
    if name.startswith("SharedRetentionPanel"):
        return 6, "shared panel"
    if name.startswith("SharedPanelClips"):
        return 7, "retention clips"
    return None


def _presentation_source_name(obj: Any) -> str:
    source = str(getattr(obj, "SourceResultObject", "") or "")
    if source:
        return source
    name = str(obj.Name)
    if name.startswith("ExplodedPreview_"):
        return name[len("ExplodedPreview_"):]
    return name


def _shape_z_bounds(obj: Any) -> tuple[float, float]:
    """Return presentation Z bounds for the translation-only result objects."""

    bounds = obj.Shape.BoundBox
    return float(bounds.ZMin), float(bounds.ZMax)


def _spread_split_groups(tier_objects: list[Any], gap: float) -> int:
    """Reveal split seams while preserving the parts' assembly relationship."""

    moved = 0
    prefixes = ("LowerCarrierPart", "UpperCarrierPart", "SharedRetentionPanelPart")
    for prefix in prefixes:
        group = [
            obj for obj in tier_objects
            if _presentation_source_name(obj).startswith(prefix)
        ]
        if len(group) < 2:
            continue
        centres = [
            (
                (float(obj.Shape.BoundBox.XMin) +
                 float(obj.Shape.BoundBox.XMax)) * 0.5,
                (float(obj.Shape.BoundBox.YMin) +
                 float(obj.Shape.BoundBox.YMax)) * 0.5,
            )
            for obj in group
        ]
        centre_x = sum(item[0] for item in centres) / len(centres)
        centre_y = sum(item[1] for item in centres) / len(centres)
        for obj, (part_x, part_y) in zip(group, centres):
            delta_x, delta_y = part_x - centre_x, part_y - centre_y
            if abs(delta_x) >= abs(delta_y):
                direction_x = -1.0 if delta_x < 0.0 else 1.0
                direction_y = 0.0
            else:
                direction_x = 0.0
                direction_y = -1.0 if delta_y < 0.0 else 1.0
            placement = _placement_copy(obj)
            placement.Base = placement.Base + App.Vector(
                direction_x * gap * 0.5,
                direction_y * gap * 0.5,
                0.0,
            )
            obj.Placement = placement
            moved += 1
    return moved


def _apply_exploded_layout(document: Any, project: Mapping[str, Any]) -> dict[str, Any]:
    """Stack printable result tiers using their real bounds and a bounded gap."""

    upper_ids = {
        "".join(character if character.isalnum() else "_" for character in item["id"]).strip("_")
        for item in project.get("objects", [])
        if item.get("layer") == "upper"
    }
    case = dict(project.get("case") or {})
    short_side = min(
        float(case.get("internal_length", 200.0)),
        float(case.get("internal_width", 150.0)),
    )
    gap = max(14.0, min(20.0, short_side * 0.08))
    has_previews = any(
        _generator_role(obj) == "exploded-preview" for obj in document.Objects
    )
    tiers: dict[int, dict[str, Any]] = {}
    unassigned: list[str] = []
    for obj in document.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        name = str(obj.Name)
        role = _generator_role(obj)
        if has_previews and role == "result":
            continue
        tier = _part_tier(_presentation_source_name(obj), upper_ids)
        if tier is None:
            if role not in {"project-root", "rim-reference", "lid-reference"}:
                unassigned.append(name)
            continue
        index, label = tier
        record = tiers.setdefault(index, {"index": index, "label": label, "objects": []})
        record["objects"].append(obj)
    if unassigned:
        raise RuntimeError("Exploded presentation has unassigned shaped objects: %s" %
                           ", ".join(sorted(unassigned)))
    if not tiers:
        raise RuntimeError("Exploded presentation contains no printable tiers")

    moved_parts = 0
    spread_parts = 0
    previous_top = None
    tier_records: list[dict[str, Any]] = []
    for index in sorted(tiers):
        record = tiers[index]
        objects = record["objects"]
        original_bounds = [_shape_z_bounds(obj) for obj in objects]
        tier_min = min(item[0] for item in original_bounds)
        tier_max = max(item[1] for item in original_bounds)
        target_min = tier_min if previous_top is None else previous_top + gap
        z_offset = target_min - tier_min
        for obj in objects:
            if abs(z_offset) > 1e-7:
                placement = _placement_copy(obj)
                placement.Base = placement.Base + App.Vector(0.0, 0.0, z_offset)
                obj.Placement = placement
                moved_parts += 1
        spread_parts += _spread_split_groups(objects, gap)
        target_max = tier_max + z_offset
        tier_records.append(
            {
                "index": index,
                "label": record["label"],
                "objects": [str(obj.Name) for obj in objects],
                "placements_mm": {
                    str(obj.Name): [
                        round(float(obj.Placement.Base.x), 6),
                        round(float(obj.Placement.Base.y), 6),
                        round(float(obj.Placement.Base.z), 6),
                    ]
                    for obj in objects
                },
                "z_min_mm": round(target_min, 6),
                "z_max_mm": round(target_max, 6),
                "z_offset_mm": round(z_offset, 6),
            }
        )
        previous_top = target_max
    _presentation_reference_offset(document)
    document.recompute()
    observed_gaps = [
        current["z_min_mm"] - previous["z_max_mm"]
        for previous, current in zip(tier_records, tier_records[1:])
    ]
    minimum_gap = min(observed_gaps) if observed_gaps else None
    if minimum_gap is not None and minimum_gap + 0.001 < gap:
        raise RuntimeError("Exploded presentation tier spacing is below the requested gap")
    return {
        "gap_mm": round(gap, 6),
        "minimum_observed_gap_mm": (
            round(minimum_gap, 6) if minimum_gap is not None else None
        ),
        "tier_count": len(tier_records),
        "moved_part_count": moved_parts,
        "spread_part_count": spread_parts,
        "tiers": tier_records,
    }


def _audit_exploded_reopen(document: Any, layout: Mapping[str, Any]) -> dict[str, Any]:
    """Confirm the saved presentation placements survived a cold reopen."""

    observed: list[dict[str, Any]] = []
    placement_checks = 0
    for tier in layout.get("tiers", []):
        objects = []
        for name in tier["objects"]:
            obj = document.getObject(name)
            if obj is None:
                raise RuntimeError("Exploded FCStd lost printable object %s" % name)
            expected_placement = tier.get("placements_mm", {}).get(name)
            if expected_placement is None:
                raise RuntimeError("Exploded layout omitted placement evidence for %s" % name)
            actual_placement = (
                float(obj.Placement.Base.x),
                float(obj.Placement.Base.y),
                float(obj.Placement.Base.z),
            )
            if any(
                    abs(actual - float(expected)) > 0.01
                    for actual, expected in zip(actual_placement, expected_placement)):
                raise RuntimeError("Exploded FCStd changed the placement of %s" % name)
            placement_checks += 1
            objects.append(obj)
        z_bounds = [_shape_z_bounds(obj) for obj in objects]
        z_min = min(item[0] for item in z_bounds)
        z_max = max(item[1] for item in z_bounds)
        if abs(z_min - float(tier["z_min_mm"])) > 0.01:
            raise RuntimeError("Exploded FCStd changed tier %s lower bound" % tier["label"])
        if abs(z_max - float(tier["z_max_mm"])) > 0.01:
            raise RuntimeError("Exploded FCStd changed tier %s upper bound" % tier["label"])
        observed.append(
            {
                "label": tier["label"],
                "z_min_mm": round(z_min, 6),
                "z_max_mm": round(z_max, 6),
            }
        )
    observed_gaps = [
        current["z_min_mm"] - previous["z_max_mm"]
        for previous, current in zip(observed, observed[1:])
    ]
    minimum_gap = min(observed_gaps) if observed_gaps else None
    requested = float(layout["gap_mm"])
    if minimum_gap is not None and minimum_gap + 0.001 < requested:
        raise RuntimeError("Exploded FCStd lost its tier spacing after cold reopen")
    previews = [
        obj for obj in document.Objects
        if _generator_role(obj) == "exploded-preview"
    ]
    originals = {
        str(obj.Name): obj for obj in document.Objects
        if _generator_role(obj) == "result"
    }
    expected_preview_count = int(layout.get("preview_part_count", 0))
    if len(previews) != expected_preview_count or len(originals) != expected_preview_count:
        raise RuntimeError("Exploded FCStd changed its preview/source part count")
    source_names: set[str] = set()
    visibility_checks = 0
    for preview in previews:
        source_name = str(getattr(preview, "SourceResultObject", "") or "")
        source = originals.get(source_name)
        if source is None or source_name in source_names:
            raise RuntimeError("Exploded FCStd has an invalid preview source map")
        source_names.add(source_name)
        if bool(getattr(preview, "ExportEnabled", True)):
            raise RuntimeError("Exploded preview unexpectedly became export-enabled")
        preview_view = getattr(preview, "ViewObject", None)
        source_view = getattr(source, "ViewObject", None)
        if preview_view is not None and source_view is not None:
            if not bool(preview_view.Visibility) or bool(source_view.Visibility):
                raise RuntimeError(
                    "Exploded FCStd did not preserve preview/source visibility"
                )
            visibility_checks += 1
    return {
        "ok": True,
        "tier_count": len(observed),
        "placement_checks": placement_checks,
        "preview_count": len(previews),
        "source_map_count": len(source_names),
        "visibility_checks": visibility_checks,
        "minimum_observed_gap_mm": (
            round(minimum_gap, 6) if minimum_gap is not None else None
        ),
    }


def _annotate_render(
    raw_path: Path,
    final_path: Path,
    number: int,
    title: str,
    view_label: str,
) -> None:
    QtCore, QtGui, _QtWidgets = _qt_modules()
    raw = QtGui.QImage(str(raw_path))
    if raw.isNull():
        raise RuntimeError(f"FreeCAD render could not be decoded: {raw_path}")
    canvas = QtGui.QImage(
        RENDER_WIDTH,
        RENDER_HEIGHT,
        QtGui.QImage.Format_ARGB32,
    )
    canvas.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.fillRect(0, 0, RENDER_WIDTH, 94, QtGui.QColor(28, 33, 40))
        painter.setPen(QtGui.QColor(255, 255, 255))
        title_font = QtGui.QFont("Sans Serif", 24)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            34,
            10,
            RENDER_WIDTH - 68,
            48,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            f"{number:02d}  {title}",
        )
        painter.setFont(QtGui.QFont("Sans Serif", 13))
        painter.setPen(QtGui.QColor(194, 205, 219))
        painter.drawText(
            34,
            55,
            RENDER_WIDTH - 68,
            28,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            "%s · synthetic custom-case example · physical fit unverified · lid clearance unknown"
            % view_label,
        )
        scaled = raw.scaled(
            RENDER_WIDTH,
            RENDER_HEIGHT - 94,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        x = (RENDER_WIDTH - scaled.width()) // 2
        y = 94 + (RENDER_HEIGHT - 94 - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    finally:
        painter.end()
    if not canvas.save(str(final_path), "PNG"):
        raise RuntimeError(f"Could not save annotated render: {final_path}")


def _render_prepared(
    document: Any,
    output: Path,
    number: int,
    title: str,
    view_label: str,
) -> dict[str, Any]:
    import FreeCADGui as Gui

    _QtCore, QtGui, QtWidgets = _qt_modules()
    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError("Native example rendering requires a running FreeCAD GUI")
    App.setActiveDocument(document.Name)
    gui_document = Gui.activeDocument()
    if gui_document is None:
        raise RuntimeError("FreeCAD has no active GUI document for rendering")
    view = gui_document.activeView()
    view.viewAxonometric()
    view.fitAll()
    QtWidgets.QApplication.processEvents()
    with tempfile.TemporaryDirectory(prefix="cig-themed-render-") as temporary:
        raw_path = Path(temporary) / "raw.png"
        view.saveImage(str(raw_path), RENDER_WIDTH, RENDER_HEIGHT - 94, "White")
        QtWidgets.QApplication.processEvents()
        if not raw_path.is_file() or raw_path.stat().st_size <= 0:
            raise RuntimeError("FreeCAD did not write the requested PNG render")
        raw = QtGui.QImage(str(raw_path))
        if raw.isNull():
            raise RuntimeError("FreeCAD wrote an unreadable viewport PNG")
        foreground_samples = 0
        for x_index in range(1, 32):
            x = int(x_index * (raw.width() - 1) / 32)
            for y_index in range(1, 24):
                y = int(y_index * (raw.height() - 1) / 24)
                pixel = QtGui.QColor(raw.pixel(x, y))
                if min(pixel.red(), pixel.green(), pixel.blue()) < 238:
                    foreground_samples += 1
        if foreground_samples < 12:
            raise RuntimeError("FreeCAD viewport PNG appears blank")
        _annotate_render(raw_path, output, number, title, view_label)
    image = QtGui.QImage(str(output))
    if image.isNull() or image.width() != RENDER_WIDTH or image.height() != RENDER_HEIGHT:
        raise RuntimeError("Annotated render has the wrong dimensions or is unreadable")
    sampled = {
        int(image.pixel(x, y))
        for x in range(0, image.width(), max(1, image.width() // 12))
        for y in range(0, image.height(), max(1, image.height() // 9))
    }
    if len(sampled) < 4:
        raise RuntimeError("Rendered PNG appears blank or effectively single-colour")
    return {
        "path": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "width": image.width(),
        "height": image.height(),
        "nonblank_sample_colours": len(sampled),
        "viewport_foreground_samples": foreground_samples,
        "view": "native FreeCAD axonometric %s" % view_label.lower(),
    }


def _render_pair_difference(assembled_path: Path, exploded_path: Path) -> dict[str, Any]:
    """Prove viewport geometry changed, excluding the different text header."""

    _QtCore, QtGui, _QtWidgets = _qt_modules()
    assembled = QtGui.QImage(str(assembled_path))
    exploded = QtGui.QImage(str(exploded_path))
    if assembled.isNull() or exploded.isNull() or assembled.size() != exploded.size():
        raise RuntimeError("Assembled/exploded render pair is missing or mismatched")
    changed = 0
    compared = 0
    for x_index in range(1, 65):
        x = int(x_index * (assembled.width() - 1) / 65)
        for y_index in range(1, 49):
            y = 94 + int(y_index * (assembled.height() - 95) / 49)
            first = QtGui.QColor(assembled.pixel(x, y))
            second = QtGui.QColor(exploded.pixel(x, y))
            delta = max(
                abs(first.red() - second.red()),
                abs(first.green() - second.green()),
                abs(first.blue() - second.blue()),
            )
            compared += 1
            if delta >= 12:
                changed += 1
    ratio = changed / float(compared)
    if ratio < 0.025:
        raise RuntimeError(
            "Exploded viewport is not visibly distinct from the assembled viewport"
        )
    return {
        "sampled_viewport_pixels": compared,
        "changed_viewport_pixels": changed,
        "changed_ratio": round(ratio, 6),
        "minimum_changed_ratio": 0.025,
        "headers_excluded": True,
    }


def _contact_sheet(
    output_root: Path,
    entries: list[dict[str, Any]],
    *,
    render_key: str = "render",
    filename: str = "contact-sheet.png",
) -> dict[str, Any]:
    QtCore, QtGui, _QtWidgets = _qt_modules()
    columns = 4
    rows = max(1, (len(entries) + columns - 1) // columns)
    cell_width, cell_height = 420, 315
    canvas = QtGui.QImage(
        columns * cell_width,
        rows * cell_height,
        QtGui.QImage.Format_ARGB32,
    )
    canvas.fill(QtGui.QColor(238, 241, 245))
    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        for index, entry in enumerate(entries):
            row, column = divmod(index, columns)
            left, top = column * cell_width, row * cell_height
            painter.fillRect(left + 5, top + 5, cell_width - 10, cell_height - 10, QtGui.QColor("white"))
            painter.setPen(QtGui.QPen(QtGui.QColor(196, 203, 213), 1))
            painter.drawRoundedRect(left + 5, top + 5, cell_width - 10, cell_height - 10, 8, 8)
            render_path = output_root / entry[render_key]["path"]
            image = QtGui.QImage(str(render_path))
            if image.isNull():
                raise RuntimeError(f"Contact sheet could not decode {render_path}")
            scaled = image.scaled(390, 245, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            image_x = left + (cell_width - scaled.width()) // 2
            painter.drawImage(image_x, top + 18, scaled)
            painter.setPen(QtGui.QColor(31, 37, 46))
            font = QtGui.QFont("Sans Serif", 11)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                left + 18,
                top + 270,
                cell_width - 36,
                28,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                f"{entry['number']:02d}  {entry['title']}",
            )
    finally:
        painter.end()
    path = output_root / filename
    if not canvas.save(str(path), "PNG"):
        raise RuntimeError("Could not save themed-example contact sheet")
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "width": canvas.width(),
        "height": canvas.height(),
        "cells": len(entries),
        "layout": f"{columns} columns x {rows} rows",
    }


def generate_themed_examples(
    repo_root: str | Path = ROOT,
    *,
    output_root: str | Path | None = None,
    render: bool = True,
) -> dict[str, Any]:
    """Generate all examples and return the machine-readable manifest."""

    root = Path(repo_root).resolve()
    destination = Path(output_root).resolve() if output_root else root / "examples" / "themed-packs"
    catalog_namespace = runpy.run_path(
        str(root / "scripts" / "themed_example_catalog.py"),
        run_name="cig_themed_example_catalog",
    )
    packs = catalog_namespace["themed_packs"]()
    if len(packs) != EXPECTED_COUNT:
        raise RuntimeError(f"Expected {EXPECTED_COUNT} themed examples; found {len(packs)}")
    git_state = _publishable_git_state(root)
    destination.mkdir(parents=True, exist_ok=True)
    _clear_generated_targets(destination, packs)
    engine_path = root / "freecad" / "CaseInsertGenerator" / "engine.py"
    engine = importlib.import_module("freecad.CaseInsertGenerator.engine")
    if Path(engine.__file__).resolve() != engine_path:
        raise RuntimeError("Themed generation imported a different engine checkout")
    namespace = vars(engine)
    project_model = namespace["_project_module"]()
    previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_on": datetime.now(timezone.utc).date().isoformat(),
        "generator": "Case Insert Generator",
        "source_sha256": _source_hashes(root),
        "source_inputs_tracked": True,
        "freecad_version": ".".join(str(item) for item in App.Version()[:3]),
        "geometry_provenance": "synthetic-demonstration",
        "physical_fit_status": "unverified",
        "compatibility_claim": "none",
        "render_requested": bool(render),
        "examples": [],
        **git_state,
    }
    for pack in packs:
        number = int(pack["number"])
        slug = str(pack["slug"])
        directory_name = f"{number:02d}-{slug}"
        pack_dir = destination / directory_name
        pack_dir.mkdir(parents=True, exist_ok=True)
        fcstd_path = pack_dir / f"{slug}.FCStd"
        exploded_fcstd_path = pack_dir / f"{slug}-exploded.FCStd"
        spec_path = pack_dir / f"{slug}.json"
        render_path = pack_dir / f"{slug}.png"
        exploded_render_path = pack_dir / f"{slug}-exploded.png"
        entry: dict[str, Any] = {
            "number": number,
            "id": pack["id"],
            "slug": slug,
            "title": pack["title"],
            "description": pack["description"],
            "safety_note": pack["safety_note"],
            "directory": directory_name,
            "status": "failed",
        }
        generated_document = None
        reopened_document = None
        exploded_document = None
        try:
            project = project_model.validate_project(pack["project"])
            source_ids = [item["id"] for item in project["objects"]]
            generated_document = _new_document(number)
            generated_document.License = EXAMPLE_LICENSE
            generated_document.LicenseURL = EXAMPLE_LICENSE_URL
            App.setActiveDocument(generated_document.Name)
            generation = namespace["generate_project"](project, document=generated_document)
            result = _result_mapping(generation)
            if result.get("valid") is not True:
                raise RuntimeError("generate_project did not report valid geometry")
            if result.get("unplaced"):
                raise RuntimeError(f"objects were unplaced: {result['unplaced']}")
            generated_document.recompute()
            pre_save_parts = _audit_results(namespace["active_results"](generated_document))
            namespace["save_fcstd"](str(fcstd_path), generated_document)
            if not fcstd_path.is_file() or fcstd_path.stat().st_size <= 0:
                raise RuntimeError("FCStd save produced no file")
            _remove_own_freecad_backups(fcstd_path)
            _close_document(generated_document)
            generated_document = None

            reopened_document = App.openDocument(str(fcstd_path))
            reopened_document.recompute()
            persisted = namespace["load_project"](reopened_document)
            persisted_ids = [item["id"] for item in persisted["objects"]]
            if persisted_ids != source_ids:
                raise RuntimeError("Cold reopen changed the stable object IDs")
            reopened_parts = namespace["active_results"](reopened_document)
            part_records = _audit_results(reopened_parts)
            if [item["name"] for item in part_records] != [item["name"] for item in pre_save_parts]:
                raise RuntimeError("Cold reopen changed the printable part list")
            if reopened_document.getObject("CaseRimPlane") is None:
                raise RuntimeError("Case rim reference is missing after reopen")
            if reopened_document.getObject("ClosedLidCeiling") is not None:
                raise RuntimeError("Unknown lid clearance unexpectedly created a usable ceiling")
            portable_scan = _portable_text_scan(fcstd_path)
            if not portable_scan["ok"]:
                raise RuntimeError(
                    "Restricted-source marker found in FCStd: "
                    f"{portable_scan['findings']}"
                )

            example_spec = {
                "schema_version": 1,
                "id": pack["id"],
                "number": number,
                "slug": slug,
                "title": pack["title"],
                "description": pack["description"],
                "geometry_provenance": pack["geometry_provenance"],
                "physical_fit_status": pack["physical_fit_status"],
                "compatibility_claim": "none",
                "safety_note": pack["safety_note"],
                "project": project,
                "resolved_project": persisted,
            }
            serialized = json.dumps(example_spec, sort_keys=True).lower()
            source_findings = text_findings(serialized, "example-spec")
            if source_findings:
                raise RuntimeError(
                    "Restricted-source marker found in example spec: "
                    f"{source_findings}"
                )
            _write_json(spec_path, example_spec)

            entry.update(
                {
                    "status": "pass",
                    "case_envelope_mm": [
                        project["case"]["internal_length"],
                        project["case"]["internal_width"],
                        project["case"]["insert_depth"],
                    ],
                    "layers": project["layers"],
                    "containment": project["containment"]["mode"],
                    "object_count": len(project["objects"]),
                    "object_types": sorted({item["type"] for item in project["objects"]}),
                    "part_count": len(part_records),
                    "solid_count": sum(item["solid_count"] for item in part_records),
                    "volume_mm3": round(sum(item["volume_mm3"] for item in part_records), 6),
                    "parts": part_records,
                    "warnings": list(result.get("warnings", [])),
                    "fcstd": {
                        "path": f"{directory_name}/{fcstd_path.name}",
                        "bytes": fcstd_path.stat().st_size,
                        "sha256": _sha256(fcstd_path),
                        "cold_reopen": True,
                        "project_schema_version": persisted["schema_version"],
                        "portable_source_scan": portable_scan,
                    },
                    "spec": {
                        "path": f"{directory_name}/{spec_path.name}",
                        "bytes": spec_path.stat().st_size,
                        "sha256": _sha256(spec_path),
                    },
                }
            )

            placement_snapshot = _placement_snapshot(reopened_document)
            _style_document(reopened_document, persisted)
            if render:
                _presentation_reference_offset(reopened_document)
                render_record = _render_prepared(
                    reopened_document,
                    render_path,
                    number,
                    str(pack["title"]),
                    "ASSEMBLED VIEW",
                )
                render_record["path"] = f"{directory_name}/{render_path.name}"
                entry["render"] = render_record
                _restore_placements(reopened_document, placement_snapshot)

            preview_part_count = _create_exploded_previews(reopened_document)
            if preview_part_count != len(part_records):
                raise RuntimeError("Exploded preview count does not match printable results")
            _style_document(reopened_document, persisted)
            exploded_layout = _apply_exploded_layout(reopened_document, persisted)
            exploded_layout["preview_part_count"] = preview_part_count
            namespace["save_fcstd"](str(exploded_fcstd_path), reopened_document)
            if (not exploded_fcstd_path.is_file() or
                    exploded_fcstd_path.stat().st_size <= 0):
                raise RuntimeError("Exploded FCStd save produced no file")
            _remove_own_freecad_backups(exploded_fcstd_path)
            _close_document(reopened_document)
            reopened_document = None

            exploded_document = App.openDocument(str(exploded_fcstd_path))
            exploded_document.recompute()
            exploded_project = namespace["load_project"](exploded_document)
            if [item["id"] for item in exploded_project["objects"]] != source_ids:
                raise RuntimeError("Exploded FCStd changed the stable object IDs")
            exploded_parts = _audit_results(namespace["active_results"](exploded_document))
            if [item["name"] for item in exploded_parts] != [item["name"] for item in part_records]:
                raise RuntimeError("Exploded FCStd changed the printable part list")
            exploded_reopen = _audit_exploded_reopen(exploded_document, exploded_layout)
            exploded_scan = _portable_text_scan(exploded_fcstd_path)
            if not exploded_scan["ok"]:
                raise RuntimeError(
                    "Restricted-source marker found in exploded FCStd: "
                    f"{exploded_scan['findings']}"
                )
            entry["exploded_fcstd"] = {
                "path": f"{directory_name}/{exploded_fcstd_path.name}",
                "bytes": exploded_fcstd_path.stat().st_size,
                "sha256": _sha256(exploded_fcstd_path),
                "cold_reopen": True,
                "presentation_only": True,
                "portable_source_scan": exploded_scan,
                "layout": exploded_layout,
                "reopen_audit": exploded_reopen,
            }
            if render:
                exploded_render_record = _render_prepared(
                    exploded_document,
                    exploded_render_path,
                    number,
                    str(pack["title"]),
                    "EXPLODED VIEW — presentation spacing only",
                )
                exploded_render_record["path"] = (
                    f"{directory_name}/{exploded_render_path.name}"
                )
                entry["exploded_render"] = exploded_render_record
                if entry["render"]["sha256"] == entry["exploded_render"]["sha256"]:
                    raise RuntimeError("Assembled and exploded PNG hashes unexpectedly match")
                entry["render_pair_difference"] = _render_pair_difference(
                    render_path,
                    exploded_render_path,
                )
        except Exception as exc:
            entry["status"] = "failed"
            entry["error_type"] = type(exc).__name__
            entry["message"] = _safe_error_message(exc, root, destination)
        finally:
            _close_document(generated_document)
            _close_document(reopened_document)
            _close_document(exploded_document)
        manifest["examples"].append(entry)
        _write_json(destination / "manifest.json", manifest)

    manifest["summary"] = {
        "total": len(manifest["examples"]),
        "passed": sum(item["status"] == "pass" for item in manifest["examples"]),
        "failed": sum(item["status"] == "failed" for item in manifest["examples"]),
        "exploded_models": sum("exploded_fcstd" in item for item in manifest["examples"]),
        "rendered": sum("render" in item for item in manifest["examples"]),
        "exploded_rendered": sum("exploded_render" in item for item in manifest["examples"]),
    }
    if (render and manifest["summary"]["rendered"] == EXPECTED_COUNT and
            manifest["summary"]["exploded_rendered"] == EXPECTED_COUNT):
        manifest["contact_sheet"] = _contact_sheet(
            destination,
            manifest["examples"],
        )
        manifest["exploded_contact_sheet"] = _contact_sheet(
            destination,
            manifest["examples"],
            render_key="exploded_render",
            filename="exploded-contact-sheet.png",
        )
    manifest["ok"] = (
        manifest["summary"]["total"] == EXPECTED_COUNT
        and manifest["summary"]["passed"] == EXPECTED_COUNT
        and manifest["summary"]["exploded_models"] == EXPECTED_COUNT
        and (
            not render or (
                manifest["summary"]["rendered"] == EXPECTED_COUNT and
                manifest["summary"]["exploded_rendered"] == EXPECTED_COUNT
            )
        )
    )
    _write_json(destination / "manifest.json", manifest)
    if previous_active and previous_active in App.listDocuments():
        App.setActiveDocument(previous_active)
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--output-dir")
    parser.add_argument("--no-render", action="store_true")
    arguments = parser.parse_args()
    report = generate_themed_examples(
        arguments.repo_root,
        output_root=arguments.output_dir,
        render=not arguments.no_render,
    )
    print(json.dumps(report["summary"] | {"ok": report["ok"]}, sort_keys=True))
    if not report["ok"]:
        raise SystemExit(1)
