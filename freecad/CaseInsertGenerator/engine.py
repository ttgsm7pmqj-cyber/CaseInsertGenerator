# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD geometry engine and three-tab dialog for Case Insert Generator."""

import hashlib
import importlib
import json
import math
import os
import shutil
import tempfile
from contextlib import contextmanager

import FreeCAD as App
import Part


PROJECT_GROUP = "CaseInsertGeneratorProject"
PARAM_OBJECT = "CaseInsertGeneratorParameters"
GENERATOR_ROLE_PROPERTY = "CaseInsertGeneratorRole"
MIN_WALL = 1.6
DEFAULT_BED = 256.0
CATALOG_FILENAME = "case_catalog.json"
_dialog = None
_LAYOUT_INSET_CACHE = {}


def _addon_module(module_name):
    """Load another module from this installed namespaced add-on."""
    try:
        return importlib.import_module(".%s" % module_name, __package__)
    except ImportError as exc:
        raise RuntimeError("Add-on module %s is missing: %s" %
                           (module_name, exc))


def _project_module():
    """Load the dependency-free project contract from the packaged add-on."""
    module = _addon_module("project_model")
    result_type = getattr(module, "GenerationResult", None)
    if result_type is not None and not hasattr(result_type, "from_mapping"):
        module = importlib.reload(module)
    return module


def macro_directory():
    """Return the installed add-on root used by file dialogs and examples."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def load_case_catalog(path=None):
    """Load and validate the bundled synthetic demonstration presets."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            CATALOG_FILENAME)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Case catalog must contain a JSON object")

    if data.get("schema_version") != 1:
        raise ValueError("Unsupported case catalog schema version")
    presets = data.get("presets")
    if not isinstance(presets, list):
        raise ValueError("Case catalog presets must be a list")

    required = ("preset_id", "brand", "series", "model", "display_name",
                "verification", "geometry")
    geometry_required = ("internal_length", "internal_width", "internal_depth",
                         "bottom_depth", "bottom_corner_radius",
                         "floor_fillet_radius", "draft_angle_degrees",
                         "profile_reference_height")
    models = {}
    normalized = []
    preset_ids = set()
    for index, preset in enumerate(presets):
        if not isinstance(preset, dict):
            raise ValueError("Case catalog preset %d must be an object" % index)
        missing = [key for key in required if key not in preset]
        if missing:
            raise ValueError("Case catalog preset %d is missing %s" %
                             (index, ", ".join(missing)))
        preset_id = str(preset["preset_id"]).strip()
        display_name = str(preset["display_name"]).strip()
        if not preset_id or preset_id in preset_ids:
            raise ValueError("Case preset IDs must be non-empty and unique")
        if not display_name or display_name in models:
            raise ValueError("Case display names must be non-empty and unique")
        geometry = preset["geometry"]
        if not isinstance(geometry, dict):
            raise ValueError("Geometry for %s must be an object" % display_name)
        missing = [key for key in geometry_required if key not in geometry]
        if missing:
            raise ValueError("Geometry for %s is missing %s" %
                             (display_name, ", ".join(missing)))
        for key in ("internal_length", "internal_width", "internal_depth",
                    "bottom_depth"):
            if float(geometry[key]) <= 0:
                raise ValueError("%s must be positive for %s" %
                                 (key.replace("_", " "), display_name))
        verification = preset["verification"]
        if not isinstance(verification, dict) or not verification.get("level"):
            raise ValueError("Verification metadata is missing for %s" % display_name)
        flat = dict(geometry)
        flat.update({
            "_preset_id": preset_id,
            "_brand": str(preset["brand"]),
            "_series": str(preset["series"]),
            "_model": str(preset["model"]),
            "_display_name": display_name,
            "_verification": dict(verification),
            "_lid": dict(preset.get("lid") or {}),
        })
        models[display_name] = flat
        preset_ids.add(preset_id)
        normalized.append(preset)
    if not models:
        raise ValueError("Case catalog contains no presets")
    return {
        "schema_version": int(data.get("schema_version", 0)),
        "catalog_name": data.get("catalog_name", "Synthetic case presets"),
        "models": models,
        "presets": normalized,
    }


def load_case_models(path=None):
    """Return the catalog's display-name-to-geometry lookup."""
    return load_case_catalog(path)["models"]


def rounded_prism(length, width, height, radius=0.0, z=0.0):
    """Return a rounded rectangle prism with lower-left corner at (0, 0)."""
    length, width, height = float(length), float(width), float(height)
    if min(length, width, height) <= 0:
        raise ValueError("Length, width, and height must be positive")
    radius = max(0.0, min(float(radius), length / 2.0, width / 2.0))
    if radius < 0.001:
        return Part.makeBox(length, width, height, App.Vector(0, 0, z))
    shapes = []
    if length - 2.0 * radius > 0.001:
        shapes.append(Part.makeBox(length - 2.0 * radius, width, height,
                                   App.Vector(radius, 0, z)))
    if width - 2.0 * radius > 0.001:
        shapes.append(Part.makeBox(length, width - 2.0 * radius, height,
                                   App.Vector(0, radius, z)))
    for x in (radius, length - radius):
        for y in (radius, width - radius):
            shapes.append(Part.makeCylinder(radius, height, App.Vector(x, y, z)))
    return shapes[0].multiFuse(shapes[1:]).removeSplitter()


def _as_float(params, key, minimum=None):
    try:
        value = float(params[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError("%s must be a number" % key.replace("_", " "))
    if minimum is not None and value < minimum:
        raise ValueError("%s must be at least %.2f mm" %
                         (key.replace("_", " "), minimum))
    return value


def _case_model(params):
    name = str(params.get("case_model", "Custom Case"))
    if name == "Custom Case":
        return None
    models = load_case_models()
    if name not in models:
        raise ValueError("Unknown case preset: %s" % name)
    return models[name]


def _resolved_params(params):
    """Return a copy with stored values enforced for named presets."""
    resolved = dict(params)
    model = _case_model(resolved)
    if model:
        resolved["internal_length"] = float(model["internal_length"])
        resolved["internal_width"] = float(model["internal_width"])
        resolved["corner_radius"] = float(model["bottom_corner_radius"])
        resolved["floor_fillet_radius"] = float(model["floor_fillet_radius"])
        resolved["draft_angle_degrees"] = float(model["draft_angle_degrees"])
        resolved["profile_reference_height"] = float(model["profile_reference_height"])
        for key in ("profile_type", "floor_length", "floor_width",
                    "floor_profile_length", "floor_profile_width",
                    "floor_corner_radius"):
            if key in model:
                resolved[key] = model[key]
        bottom_depth = float(model.get("bottom_depth") or model["internal_depth"])
        # Presets own their demonstration depth. Callers do not need to copy
        # that value into an otherwise redundant API parameter.
        if resolved.get("insert_depth") in (None, ""):
            resolved["insert_depth"] = bottom_depth
        if _as_float(resolved, "insert_depth", 1.0) > bottom_depth + 0.001:
            raise ValueError("Insert depth exceeds the preset %.3f mm case-bottom depth" % bottom_depth)
    return resolved


def _profile_at_height(model, height, clearance):
    """Return a rounded preset section after draft and fit clearances."""
    nominal_l = float(model["internal_length"])
    nominal_w = float(model["internal_width"])
    plan_r = float(model["bottom_corner_radius"])
    reference = float(model["profile_reference_height"])
    draft = math.tan(math.radians(float(model["draft_angle_degrees"])))
    growth = (max(0.0, float(height)) - reference) * draft
    length = nominal_l + 2.0 * growth - 2.0 * clearance
    width = nominal_w + 2.0 * growth - 2.0 * clearance
    radius = max(0.0, plan_r + growth - clearance)
    if min(length, width) <= 0:
        raise ValueError("Case clearances leave no usable insert profile")
    return length, width, min(radius, length / 2.0, width / 2.0)


def _rounded_wire(length, width, radius, z):
    slab = rounded_prism(length, width, 0.05, max(0.01, radius), z)
    horizontal = [item for item in slab.Faces if item.BoundBox.ZLength < 0.001]
    if not horizontal:
        raise RuntimeError("Could not construct a horizontal rounded profile")
    face = min(horizontal, key=lambda item: item.BoundBox.ZMin)
    return face.OuterWire.copy()

def _case_blank(params, height=None):
    """Create a custom or rounded-preset insert envelope."""
    params = _resolved_params(params)
    model = _case_model(params)
    if not model:
        length, width, available, radius = _effective_case(params)
        target = available if height is None else float(height)
        if target <= 0 or target > available + 0.001:
            raise ValueError("Requested insert envelope height is outside the case bottom")
        return rounded_prism(length, width, target, radius)

    bottom = _as_float(params, "bottom_clearance", 0.0)
    insert_depth = _as_float(params, "insert_depth", 1.0)
    target = insert_depth - bottom if height is None else float(height)
    if target <= 0 or target > insert_depth - bottom + 0.001:
        raise ValueError("Requested insert envelope height is outside the case bottom")
    clearance = (_as_float(params, "side_clearance", 0.0) +
                 _as_float(params, "taper_allowance", 0.0))
    samples = sorted({bottom, bottom + target})
    wires = []
    for absolute_z in samples:
        length, width, radius = _profile_at_height(
            model, absolute_z, clearance)
        wire = _rounded_wire(length, width, radius, absolute_z - bottom)
        wire.translate(App.Vector(-length / 2.0, -width / 2.0, 0))
        wires.append(wire)
    shape = Part.makeLoft(wires, True, False)
    shape.translate(App.Vector(-shape.BoundBox.XMin, -shape.BoundBox.YMin, 0))
    _valid_solid(shape, "case insert envelope")
    return shape

def _effective_case(params):
    params = _resolved_params(params)
    length = _as_float(params, "internal_length", 1.0)
    width = _as_float(params, "internal_width", 1.0)
    depth = _as_float(params, "insert_depth", 1.0)
    side = _as_float(params, "side_clearance", 0.0)
    bottom = _as_float(params, "bottom_clearance", 0.0)
    taper = _as_float(params, "taper_allowance", 0.0)
    radius = _as_float(params, "corner_radius", 0.0)
    per_side = side + taper
    length -= 2.0 * per_side
    width -= 2.0 * per_side
    depth -= bottom
    if min(length, width, depth) <= 0:
        raise ValueError("Case clearances leave no usable insert volume")
    radius = min(max(0.0, radius - per_side), length / 2.0, width / 2.0)
    return length, width, depth, radius


def _verified_lid_dimensions(model):
    """Return a verified lid-panel envelope, or None when it is unavailable."""
    if not model:
        return None
    lid_metadata = model.get("_lid")
    if isinstance(lid_metadata, dict) and not lid_metadata.get("available", False):
        return None
    try:
        length = float(model.get("lid_length") or 0.0)
        width = float(model.get("lid_width") or 0.0)
    except (TypeError, ValueError):
        return None
    if length <= 0.0 or width <= 0.0:
        return None
    return length, width


def _lid_panel_dimensions(params):
    """Resolve only explicitly verified or user-measured lid dimensions."""
    model = _case_model(params)
    if model:
        dimensions = _verified_lid_dimensions(model)
        if not dimensions:
            raise ValueError(
                "Lid panel is unavailable for %s: the model data has no "
                "verified lid-panel length and width." %
                str(params.get("case_model", "this case")))
        return dimensions
    length = _as_float(params, "lid_length", 0.0)
    width = _as_float(params, "lid_width", 0.0)
    if length <= 0.0 or width <= 0.0:
        raise ValueError(
            "Enter measured lid-panel length and width for the custom case")
    return length, width


def _document_objects(doc):
    try:
        return list(doc.Objects)
    except Exception:
        return list(getattr(doc, "_objects", {}).values())


def _generator_role(obj):
    return str(getattr(obj, GENERATOR_ROLE_PROPERTY, "") or "")


def _mark_generator_object(obj, role):
    try:
        if GENERATOR_ROLE_PROPERTY not in list(getattr(obj, "PropertiesList", [])):
            obj.addProperty("App::PropertyString", GENERATOR_ROLE_PROPERTY,
                            "Case Insert Generator")
        setattr(obj, GENERATOR_ROLE_PROPERTY, str(role))
    except Exception:
        # The marker is an identity safeguard in FreeCAD documents. Test
        # doubles and older object types may not expose dynamic properties.
        try:
            setattr(obj, GENERATOR_ROLE_PROPERTY, str(role))
        except Exception:
            pass
    return obj


def _looks_like_generator_parameters(obj, require_project=False):
    if not obj:
        return False
    if require_project:
        return bool(getattr(obj, "ProjectJSON", ""))
    return bool(
        getattr(obj, "ProjectJSON", "") or
        getattr(obj, "ParameterJSON", "") or
        getattr(obj, "GeneratedResults", None) or
        getattr(obj, "GeneratedResult", "")
    )


def _find_parameter_object(doc, require_project=False):
    for obj in _document_objects(doc):
        if (_generator_role(obj) == "parameters" and
                _looks_like_generator_parameters(obj, require_project)):
            return obj
    obj = doc.getObject(PARAM_OBJECT)
    if _looks_like_generator_parameters(obj, require_project):
        return obj
    for obj in _document_objects(doc):
        if _looks_like_generator_parameters(obj, require_project):
            return obj
    return None


def _find_project_group(doc):
    for obj in _document_objects(doc):
        if _generator_role(obj) == "project-root":
            return obj
    named = doc.getObject(PROJECT_GROUP)
    for child in list(getattr(named, "Group", []) or []):
        if _looks_like_generator_parameters(child):
            return named
    return None


def _safe_remove_group(doc):
    group = _find_project_group(doc)
    if not group:
        return
    for obj in list(group.Group):
        if doc.getObject(obj.Name):
            doc.removeObject(obj.Name)
    if doc.getObject(group.Name):
        doc.removeObject(group.Name)
    doc.recompute()


@contextmanager
def _generation_transaction(doc, label):
    """Replace a project atomically, enabling document Undo after success."""
    if doc.HasPendingTransaction:
        raise RuntimeError(
            "Finish the active FreeCAD operation before generating an insert.")
    # Headless callers can start with Undo disabled. Transactions must record
    # changes there too so an abort restores the last editable project.
    undo_was_enabled = bool(doc.UndoMode)
    if not undo_was_enabled:
        doc.UndoMode = 1
    doc.openTransaction(label)
    try:
        yield
        doc.commitTransaction()
    except BaseException:
        doc.abortTransaction()
        doc.recompute()
        if not undo_was_enabled:
            doc.UndoMode = 0
        raise


def _add_parameter_object(doc, group, params, result_name):
    parameter_json = json.dumps(params, sort_keys=True, allow_nan=False)
    obj = _mark_generator_object(
        doc.addObject("App::FeaturePython", PARAM_OBJECT), "parameters")
    group.addObject(obj)
    obj.addProperty("App::PropertyString", "InsertType", "Generator")
    obj.InsertType = str(params.get("insert_type", ""))
    obj.addProperty("App::PropertyString", "CaseModel", "Generator")
    obj.CaseModel = str(params.get("case_model", "Custom Case"))
    obj.addProperty("App::PropertyString", "GeneratedResult", "Generator")
    result_names = list(result_name) if isinstance(result_name, (list, tuple)) else [result_name]
    result_names = [str(item) for item in result_names if str(item)]
    obj.GeneratedResult = result_names[0] if result_names else ""
    obj.addProperty("App::PropertyStringList", "GeneratedResults", "Generator")
    obj.GeneratedResults = result_names
    obj.addProperty("App::PropertyString", "ParameterJSON", "Generator")
    obj.ParameterJSON = parameter_json
    length_keys = (
        "internal_length", "internal_width", "insert_depth", "corner_radius",
        "lid_length", "lid_width", "lid_clearance",
        "side_clearance", "bottom_clearance", "taper_allowance",
        "base_thickness", "outer_wall", "divider_wall", "divider_height",
        "edge_inset", "panel_thickness", "panel_corner_radius", "slot_width",
        "slot_height", "slot_pitch_x", "slot_pitch_y", "hole_diameter",
        "hole_edge_offset", "bed_x", "bed_y", "bed_margin", "svg_x", "svg_y",
        "cutout_depth", "svg_clearance")
    for key in length_keys:
        if key in params and params[key] is not None:
            prop = "P_" + "".join(part.title() for part in key.split("_"))
            obj.addProperty("App::PropertyLength", prop, "Last build")
            setattr(obj, prop, float(params[key]))
    return obj


def _add_shape(doc, group, name, label, shape, visible=True, role="result"):
    obj = _mark_generator_object(doc.addObject("Part::Feature", name), role)
    obj.Label = label
    obj.Shape = shape
    group.addObject(obj)
    try:
        obj.ViewObject.Visibility = visible
    except Exception:
        pass
    return obj


def _lid_clearance(params):
    """Return (source, millimetres) without treating unknown space as usable."""
    source = str(params.get("lid_clearance_source", "unknown")).strip().lower()
    if source not in ("measured", "cad-derived"):
        return "unknown", None
    try:
        height = float(params.get("lid_clearance", 0.0))
    except (TypeError, ValueError):
        raise ValueError("lid clearance must be a number")
    if height < 0.0:
        raise ValueError("lid clearance cannot be negative")
    return source, height


def _add_clearance_references(doc, group, params, length, width, rim_z):
    """Add visible, non-exported rim and conservative closed-lid references."""
    rim = _add_shape(
        doc, group, "CaseRimPlane", "Case rim / seal plane",
        Part.makePlane(float(length), float(width), App.Vector(0, 0, float(rim_z))),
        role="rim-reference")
    rim.addProperty("App::PropertyString", "Evidence", "Clearance")
    rim.Evidence = "Case-bottom rim reference; not an exportable print part"
    try:
        rim.ViewObject.ShapeColor = (0.95, 0.65, 0.10)
        rim.ViewObject.LineColor = (0.95, 0.45, 0.05)
        rim.ViewObject.Transparency = 82
    except Exception:
        pass

    source, clearance = _lid_clearance(params)
    ceiling = None
    if clearance is not None:
        ceiling = _add_shape(
            doc, group, "ClosedLidCeiling", "Closed-lid usable ceiling",
            Part.makePlane(float(length), float(width),
                           App.Vector(0, 0, float(rim_z) + clearance)),
            role="lid-reference")
        ceiling.addProperty("App::PropertyString", "Evidence", "Clearance")
        ceiling.Evidence = "%s lowest closed-lid clearance: %.3f mm" % (
            source, clearance)
        try:
            ceiling.ViewObject.ShapeColor = (0.25, 0.70, 0.95)
            ceiling.ViewObject.LineColor = (0.10, 0.45, 0.90)
            ceiling.ViewObject.Transparency = 86
        except Exception:
            pass
    return rim, ceiling


def _valid_solid(shape, label):
    if shape.isNull() or shape.Volume <= 0:
        raise RuntimeError("%s is empty" % label)
    if not shape.isValid():
        raise RuntimeError("%s is not a valid solid" % label)
    if len(shape.Solids) < 1:
        raise RuntimeError("%s contains no solid" % label)


def _printable_plan_dimensions(shape):
    """Return the realised X/Y size after a Boolean tile operation.

    OCCT can retain the bounds of an underlying trimmed curve after ``common``
    even though every realised edge and mesh point is inside the tile.  That
    stale box made shared-panel carriers fail the post-split bed check.  A
    fine tessellation measures the realised result and also refreshes FreeCAD's
    cached bounds for the document object that receives the shape.
    """
    points, _triangles = shape.tessellate(0.05)
    if not points:
        bounds = shape.BoundBox
        return bounds.XLength, bounds.YLength
    x_values = [point.x for point in points]
    y_values = [point.y for point in points]
    return max(x_values) - min(x_values), max(y_values) - min(y_values)


def split_shape_for_bed(shape, bed_x, bed_y, margin=5.0):
    """Cut a solid into straight, butt-jointed tiles that fit the print bed."""
    bed_x, bed_y, margin = float(bed_x), float(bed_y), float(margin)
    if min(bed_x, bed_y) <= 0 or margin < 0:
        raise ValueError("Printer-bed dimensions must be positive and the margin cannot be negative")
    usable_x, usable_y = bed_x - 2.0 * margin, bed_y - 2.0 * margin
    if min(usable_x, usable_y) <= 0:
        raise ValueError("Print-bed margin leaves no printable area")
    bbox = shape.BoundBox
    count_x = max(1, int(math.ceil(bbox.XLength / usable_x)))
    count_y = max(1, int(math.ceil(bbox.YLength / usable_y)))
    if count_x == 1 and count_y == 1:
        return [shape.copy()]
    tile_x, tile_y = bbox.XLength / count_x, bbox.YLength / count_y
    parts = []
    for row in range(count_y):
        y0 = bbox.YMin + row * tile_y
        y1 = bbox.YMax if row == count_y - 1 else y0 + tile_y
        for column in range(count_x):
            x0 = bbox.XMin + column * tile_x
            x1 = bbox.XMax if column == count_x - 1 else x0 + tile_x
            cutter = Part.makeBox(x1 - x0, y1 - y0, bbox.ZLength + 2.0,
                                  App.Vector(x0, y0, bbox.ZMin - 1.0))
            clipped = shape.common(cutter)
            for solid in clipped.Solids:
                part = solid.copy()
                _valid_solid(part, "split print part")
                part_x, part_y = _printable_plan_dimensions(part)
                if (part_x > usable_x + 0.02 or
                        part_y > usable_y + 0.02):
                    raise RuntimeError("A split part still exceeds the usable print bed")
                parts.append(part)
    if not parts:
        raise RuntimeError("Automatic bed splitting produced no printable parts")
    source_volume = shape.Volume
    split_volume = sum(part.Volume for part in parts)
    tolerance = max(0.1, source_volume * 0.0001)
    if abs(source_volume - split_volume) > tolerance:
        raise RuntimeError("Automatic bed splitting changed the model volume")
    return parts


def _keyed_split_counts(length, width, usable_x, usable_y, key_size):
    radius = float(key_size) / 2.0
    effective_x = float(usable_x) - radius - 0.5
    effective_y = float(usable_y) - radius - 0.5
    if effective_x <= 0.0 or effective_y <= 0.0:
        raise ValueError("Alignment key size leaves no usable printer-bed area")
    count_x = max(1, int(math.ceil(float(length) / effective_x)))
    count_y = max(1, int(math.ceil(float(width) / effective_y)))
    return count_x, count_y


def _seam_key_positions(start, end, key_size):
    """Return one or two stable key centres away from tile corners."""
    span = float(end) - float(start)
    edge = max(float(key_size), 2.0)
    if span >= edge * 5.0:
        return (start + span / 3.0, start + 2.0 * span / 3.0)
    return (start + span / 2.0,)


def split_lid_panel_for_bed(shape, bed_x, bed_y, margin=5.0,
                            key_size=8.0, key_clearance=0.25):
    """Split one panel with complementary in-plane round alignment keys.

    Each lower/left tile owns the circular material crossing its outgoing seam;
    the adjacent tile receives the matching clearance socket.  The result is a
    keyed puzzle seam, not an unsupported claim that straight butt edges will
    self-align after printing.
    """
    bed_x, bed_y = float(bed_x), float(bed_y)
    margin = float(margin)
    key_size = float(key_size)
    key_clearance = float(key_clearance)
    if min(bed_x, bed_y, key_size) <= 0.0 or margin < 0.0 or key_clearance < 0.0:
        raise ValueError(
            "Bed dimensions and key size must be positive; margins and key clearance cannot be negative"
        )
    usable_x, usable_y = bed_x - 2.0 * margin, bed_y - 2.0 * margin
    if min(usable_x, usable_y) <= 0.0:
        raise ValueError("Print-bed margin leaves no printable area")
    bbox = shape.BoundBox
    count_x, count_y = _keyed_split_counts(
        bbox.XLength, bbox.YLength, usable_x, usable_y, key_size)
    if count_x == 1 and count_y == 1:
        return [shape.copy()], {
            "columns": 1,
            "rows": 1,
            "key_count": 0,
            "volume_loss_mm3": 0.0,
        }
    tile_x = bbox.XLength / count_x
    tile_y = bbox.YLength / count_y
    tiles = {}
    for row in range(count_y):
        y0 = bbox.YMin + row * tile_y
        y1 = bbox.YMax if row == count_y - 1 else y0 + tile_y
        for column in range(count_x):
            x0 = bbox.XMin + column * tile_x
            x1 = bbox.XMax if column == count_x - 1 else x0 + tile_x
            clip = Part.makeBox(
                x1 - x0,
                y1 - y0,
                bbox.ZLength + 2.0,
                App.Vector(x0, y0, bbox.ZMin - 1.0),
            )
            tile = shape.common(clip).removeSplitter()
            _valid_solid(tile, "keyed split base tile")
            tiles[(row, column)] = tile

    key_radius = key_size / 2.0
    key_count = 0
    key_height = bbox.ZLength + 2.0
    key_z = bbox.ZMin - 1.0
    for row in range(count_y):
        y0 = bbox.YMin + row * tile_y
        y1 = bbox.YMax if row == count_y - 1 else y0 + tile_y
        for column in range(count_x - 1):
            seam_x = bbox.XMin + (column + 1) * tile_x
            for center_y in _seam_key_positions(y0, y1, key_size):
                male_tool = Part.makeCylinder(
                    key_radius, key_height,
                    App.Vector(seam_x, center_y, key_z))
                male_material = shape.common(male_tool)
                if male_material.Volume <= 0.01:
                    continue
                socket = Part.makeCylinder(
                    key_radius + key_clearance,
                    key_height,
                    App.Vector(seam_x, center_y, key_z))
                left_key = (row, column)
                right_key = (row, column + 1)
                tiles[left_key] = tiles[left_key].fuse(male_material).removeSplitter()
                tiles[right_key] = tiles[right_key].cut(socket).removeSplitter()
                key_count += 1
    for row in range(count_y - 1):
        seam_y = bbox.YMin + (row + 1) * tile_y
        for column in range(count_x):
            x0 = bbox.XMin + column * tile_x
            x1 = bbox.XMax if column == count_x - 1 else x0 + tile_x
            for center_x in _seam_key_positions(x0, x1, key_size):
                male_tool = Part.makeCylinder(
                    key_radius, key_height,
                    App.Vector(center_x, seam_y, key_z))
                male_material = shape.common(male_tool)
                if male_material.Volume <= 0.01:
                    continue
                socket = Part.makeCylinder(
                    key_radius + key_clearance,
                    key_height,
                    App.Vector(center_x, seam_y, key_z))
                lower_key = (row, column)
                upper_key = (row + 1, column)
                tiles[lower_key] = tiles[lower_key].fuse(male_material).removeSplitter()
                tiles[upper_key] = tiles[upper_key].cut(socket).removeSplitter()
                key_count += 1

    if key_count == 0:
        raise RuntimeError(
            "Keyed splitting found no solid seam material; adjust the pattern, bed, or key size"
        )
    parts = []
    for row in range(count_y):
        for column in range(count_x):
            part = tiles[(row, column)].removeSplitter()
            _valid_solid(part, "keyed split panel part")
            realised_x, realised_y = _printable_plan_dimensions(part)
            if realised_x > usable_x + 0.02 or realised_y > usable_y + 0.02:
                raise RuntimeError(
                    "A keyed panel part still exceeds the usable print bed"
                )
            parts.append(part)
    for first_index, first in enumerate(parts):
        for second in parts[first_index + 1:]:
            if first.common(second).Volume > 0.01:
                raise RuntimeError("Keyed split panel parts overlap in assembled position")
    source_volume = shape.Volume
    split_volume = sum(part.Volume for part in parts)
    if split_volume > source_volume + max(0.1, source_volume * 0.0001):
        raise RuntimeError("Keyed panel splitting added overlapping material")
    return parts, {
        "columns": count_x,
        "rows": count_y,
        "key_count": key_count,
        "volume_loss_mm3": max(0.0, source_volume - split_volume),
    }


def _make_case_reference(length, width, depth, radius):
    return rounded_prism(length, width, depth, radius)


def _offset_face(face, clearance):
    if abs(clearance) < 0.0001:
        return face
    errors = []
    # FreeCAD's fill=True returns the swept band between the original and
    # offset boundaries.  That produces a thin outline cut instead of an
    # enlarged equipment pocket.  Keep fill=False and turn a returned closed
    # wire into a face when needed.
    for args in ((clearance, 0, False, False, False),
                 (clearance, 0, False, False),
                 (clearance, 0, False),
                 (clearance, 0)):
        try:
            candidate = face.makeOffset2D(*args)
            if candidate and not candidate.isNull():
                if candidate.Faces:
                    offset_face = max(candidate.Faces, key=lambda item: item.Area)
                else:
                    closed = [wire for wire in candidate.Wires if wire.isClosed()]
                    offset_face = max(
                        (Part.Face(wire) for wire in closed),
                        key=lambda item: item.Area)
                if clearance > 0.0 and offset_face.Area <= face.Area + 0.000001:
                    raise RuntimeError(
                        "offset did not enlarge the closed SVG profile")
                return offset_face
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("SVG clearance offset failed: %s" %
                       (errors[-1] if errors else "unknown error"))


def _nested_svg_faces(wires, fill_rule="evenodd", winding_signs=None):
    """Build fallback planar faces when importSVG supplies wires but no faces.

    FreeCAD's importer normally supplies finished faces and therefore remains
    the authority for SVG fill semantics.  The wire fallback is safe for the
    deterministic even/odd rule only; nonzero compound winding must never be
    guessed from flattened wires.
    """
    if winding_signs is not None and len(winding_signs) != len(wires):
        raise ValueError("SVG path winding data does not match imported wires")
    records = []
    seen = set()
    for wire in wires:
        if not wire.isClosed():
            continue
        try:
            face = Part.Face(wire)
        except Exception:
            continue
        bounds = wire.BoundBox
        key = tuple(round(value, 6) for value in (
            face.Area, bounds.XMin, bounds.YMin, bounds.XMax, bounds.YMax))
        if key in seen or face.Area <= 0.000001:
            continue
        seen.add(key)
        sign = (int(winding_signs[len(records)])
                if winding_signs is not None else 1)
        records.append({"wire": wire, "face": face, "area": face.Area,
                        "parent": None, "depth": 0, "sign": sign,
                        "outside_winding": 0, "inside_winding": sign})
    records.sort(key=lambda item: item["area"], reverse=True)
    for index, child in enumerate(records):
        candidates = []
        point = child["face"].CenterOfMass
        for parent_index in range(index):
            parent = records[parent_index]
            try:
                if parent["face"].isInside(point, 0.01, True):
                    candidates.append(parent)
            except Exception:
                pass
        if candidates:
            child["parent"] = min(candidates, key=lambda item: item["area"])
            child["depth"] = child["parent"]["depth"] + 1
            child["outside_winding"] = child["parent"]["inside_winding"]
            child["inside_winding"] = (
                child["outside_winding"] + child["sign"])
    faces = []
    if fill_rule == "nonzero":
        outer_records = [record for record in records
                         if record["outside_winding"] == 0 and
                         record["inside_winding"] != 0]
        hole_records = [record for record in records
                        if record["outside_winding"] != 0 and
                        record["inside_winding"] == 0]
        for outer in outer_records:
            holes = []
            for candidate in hole_records:
                ancestor = candidate.get("parent")
                nearest_outer = None
                while ancestor is not None:
                    if ancestor in outer_records:
                        nearest_outer = ancestor
                        break
                    ancestor = ancestor.get("parent")
                if nearest_outer is outer:
                    holes.append(candidate["face"])
            face = outer["face"]
            if holes:
                face = face.cut(Part.makeCompound(holes))
            for candidate in face.Faces or [face]:
                if candidate.Area > 0.000001:
                    faces.append(candidate)
        return faces
    for record in records:
        if record["depth"] % 2:
            continue
        face = record["face"]
        holes = [candidate["face"] for candidate in records
                 if candidate["parent"] is record and candidate["depth"] % 2]
        if holes:
            face = face.cut(Part.makeCompound(holes))
        for candidate in face.Faces or [face]:
            if candidate.Area > 0.000001:
                faces.append(candidate)
    return faces


def _import_svg_faces(svg_path, scale, x, y, rotation, clearance):
    if not svg_path or not os.path.isfile(svg_path):
        raise ValueError("Select an existing SVG file")
    try:
        preflight = _addon_module("svg_import").preflight_svg_file(
            svg_path).require_importable()
    except ValueError as exc:
        raise ValueError(str(exc))
    import importSVG
    token = hashlib.sha256(
        (os.path.realpath(svg_path) + "\0" + str(id(preflight))).encode("utf-8")
    ).hexdigest()[:12]
    base_name = "CaseInsertSVGImport_%s" % token
    temp_name = base_name
    suffix = 1
    while temp_name in App.listDocuments():
        suffix += 1
        temp_name = "%s_%d" % (base_name, suffix)
    active_name = App.ActiveDocument.Name if App.ActiveDocument else None
    temp = App.newDocument(temp_name)
    faces = []
    open_count = 0
    try:
        importSVG.insert(svg_path, temp.Name)
        temp.recompute()
        imported_wires = []
        for obj in list(temp.Objects):
            shape = getattr(obj, "Shape", None)
            if not shape or shape.isNull():
                continue
            imported_wires.extend(wire.copy() for wire in shape.Wires
                                  if wire.isClosed())
            open_count += sum(1 for wire in shape.Wires
                              if not wire.isClosed())
        wire_index = 0
        for candidate in preflight.candidates:
            wire_count = max(1, int(candidate.subpath_count))
            candidate_wires = imported_wires[wire_index:wire_index + wire_count]
            wire_index += wire_count
            if len(candidate_wires) != wire_count:
                raise ValueError(
                    "FreeCAD imported fewer SVG contours than preflight found; "
                    "no partial cut was made.")
            winding_signs = None
            if candidate.fill_rule == "nonzero" and wire_count > 1:
                if candidate.tag != "path":
                    raise ValueError(
                        "Compound nonzero fill is supported only for SVG paths")
                winding_signs = _addon_module("svg_import").path_winding_signs(
                    candidate.attributes.get("d", ""))
            local_faces = _nested_svg_faces(
                candidate_wires, candidate.fill_rule, winding_signs)
            for face in local_faces:
                matrix = App.Matrix()
                matrix.A11 = scale
                matrix.A22 = scale
                matrix.A33 = 1.0
                transformed = face.transformGeometry(matrix)
                transformed.rotate(
                    App.Vector(0, 0, 0), App.Vector(0, 0, 1), rotation)
                faces.append(_offset_face(transformed, clearance))
        if wire_index != len(imported_wires):
            raise ValueError(
                "FreeCAD imported unexpected extra SVG contours; no partial cut was made.")
    finally:
        App.closeDocument(temp.Name)
        if active_name and active_name in App.listDocuments():
            App.setActiveDocument(active_name)
    if not faces:
        raise ValueError("SVG contains no supported closed paths")
    # importSVG follows SVG's downward-positive Y axis, and source viewBoxes
    # need not start at zero.  Rebase the transformed result so X/Y always mean
    # the final cutout's lower-left distance from the insert's lower-left.
    imported_bounds = Part.makeCompound(faces).BoundBox
    placement = App.Vector(x - imported_bounds.XMin,
                           y - imported_bounds.YMin, 0.0)
    for face in faces:
        face.translate(placement)
    # Hidden geometry may be ignored safely; visible unsupported content was
    # already blocked by require_importable().
    open_count += sum(1 for item in preflight.warning_diagnostics
                      if item.code == "HIDDEN_GEOMETRY_IGNORED")
    return faces, open_count


def build_svg_insert(params):
    params = _resolved_params(params)
    length, width, depth, radius = _effective_case(params)
    block = _case_blank(params)
    scale = _as_float(params, "svg_scale", 0.001)
    x = _as_float(params, "svg_x")
    y = _as_float(params, "svg_y")
    rotation = _as_float(params, "svg_rotation")
    clearance = _as_float(params, "svg_clearance", 0.0)
    faces, open_count = _import_svg_faces(
        params.get("svg_path", ""), scale, x, y, rotation, clearance)
    through = bool(params.get("through_cut", False))
    invert = bool(params.get("invert_svg", False))
    cut_depth = depth + 2.0 if through else _as_float(params, "cutout_depth", 0.01)
    if not through and cut_depth >= depth:
        raise ValueError("Partial cutout depth must be less than insert depth")
    cutters = []
    for face in faces:
        if through:
            z0 = -1.0
        elif invert:
            z0 = 0.0
        else:
            z0 = depth - cut_depth
        placed = face.copy()
        placed.translate(App.Vector(0, 0, z0))
        cutters.append(placed.extrude(App.Vector(0, 0, cut_depth)))
    cutter = cutters[0].multiFuse(cutters[1:]).removeSplitter()
    if block.common(cutter).Volume <= 0.000001:
        raise ValueError(
            "SVG cutout does not overlap the insert; reduce its X/Y position "
            "or scale")
    result = block.cut(cutter).removeSplitter()
    if block.Volume - result.Volume <= 0.000001:
        raise RuntimeError("SVG cutout removed no material from the insert")
    _valid_solid(result, "SVG insert")
    return result, cutter, open_count, (length, width, depth, radius)


def _bay_divider_centers(text, span, divider, label):
    """Convert locked sizes and flexible '*' bays into wall centres."""
    tokens = [item.strip() for item in str(text or "").split(",") if item.strip()]
    if not tokens:
        raise ValueError("Enter %s, for example: 55, 70, *" % label)
    values = []
    fixed_total = 0.0
    flexible_indices = []
    for index, token in enumerate(tokens):
        if token == "*":
            flexible_indices.append(index)
            values.append(None)
            continue
        try:
            value = float(token)
        except ValueError:
            raise ValueError("%s contains an invalid size: %s" %
                             (label.capitalize(), token))
        if value <= 0:
            raise ValueError("%s sizes must be greater than zero" % label.capitalize())
        values.append(value)
        fixed_total += value
    clear_available = span - divider * max(0, len(values) - 1)
    remainder = clear_available - fixed_total
    if flexible_indices:
        if remainder <= 0:
            raise ValueError("%s locked sizes leave no room for flexible bays" % label.capitalize())
        shared_size = remainder / len(flexible_indices)
        for flexible_index in flexible_indices:
            values[flexible_index] = shared_size
    elif abs(remainder) > 0.1:
        raise ValueError("%s must total %.2f mm; add a flexible bay to share the remaining %.2f mm" %
                         (label.capitalize(), clear_available, remainder))
    centres = []
    cursor = values[0]
    for value in values[1:]:
        centres.append(cursor + divider / 2.0)
        cursor += divider + value
    return centres, values


def build_divider_insert(params):
    params = _resolved_params(params)
    length, width, available_depth, radius = _effective_case(params)
    base = _as_float(params, "base_thickness", MIN_WALL)
    outer = _as_float(params, "outer_wall", MIN_WALL)
    divider = _as_float(params, "divider_wall", MIN_WALL)
    wall_height = _as_float(params, "divider_height", 0.1)
    height = base + wall_height
    if min(base, outer, divider) < MIN_WALL:
        raise ValueError("Printable wall and base thickness must be at least %.1f mm" % MIN_WALL)
    if height > available_depth:
        raise ValueError("Divider height exceeds the available insert depth")
    if length <= 2.0 * outer or width <= 2.0 * outer:
        raise ValueError("Outer walls leave no tray interior")
    rows = max(1, int(params.get("rows", 1)))
    cols = max(1, int(params.get("columns", 1)))
    layout = str(params.get("divider_layout", "Equal grid"))
    measured = layout == "Measured bay sizes"
    use_rows = layout in ("Rows only", "Equal grid", "Horizontal divisions",
                          "Cross-grid", "User-defined")
    use_cols = layout in ("Columns only", "Equal grid", "Vertical divisions",
                          "Cross-grid", "User-defined")
    envelope = _case_blank(params, height)
    envelope_box = envelope.BoundBox
    model = _case_model(params)
    if model:
        profile_clearance = (_as_float(params, "side_clearance", 0.0) +
                             _as_float(params, "taper_allowance", 0.0) + outer)
        profile_height = _as_float(params, "bottom_clearance", 0.0) + base
        inner_l, inner_w, _inner_radius = _profile_at_height(
            model, profile_height, profile_clearance)
    else:
        inner_l = length - 2.0 * outer
        inner_w = width - 2.0 * outer
    if min(inner_l, inner_w) <= 0:
        raise ValueError("Outer walls leave no tray interior at floor height")

    def equal_centres(span, count):
        clear_size = (span - divider * max(0, count - 1)) / count
        if clear_size <= 0:
            raise ValueError("Too many compartments for the selected divider thickness")
        centres = []
        cursor = clear_size
        for _index in range(1, count):
            centres.append(cursor + divider / 2.0)
            cursor += divider + clear_size
        return centres

    if measured:
        column_centres, column_sizes = _bay_divider_centers(
            params.get("length_bays", ""), inner_l, divider,
            "left-to-right bay sizes")
        row_centres, row_sizes = _bay_divider_centers(
            params.get("width_bays", ""), inner_w, divider,
            "front-to-back bay sizes")
        use_cols, use_rows = bool(column_centres), bool(row_centres)
    else:
        column_centres = equal_centres(inner_l, cols) if use_cols else []
        row_centres = equal_centres(inner_w, rows) if use_rows else []

    # Build the floor and perimeter as one conformal shell. Preset surfaces
    # follow the stored corner radius and draft at every height.
    inner_params = dict(params)
    inner_params["side_clearance"] = _as_float(params, "side_clearance", 0.0) + outer
    inner_params["bottom_clearance"] = _as_float(params, "bottom_clearance", 0.0) + base
    inner_void = _case_blank(inner_params, wall_height)
    inner_box = inner_void.BoundBox
    centre_x = (envelope_box.XMin + envelope_box.XMax) / 2.0
    centre_y = (envelope_box.YMin + envelope_box.YMax) / 2.0
    inner_void.translate(App.Vector(
        centre_x - (inner_box.XMin + inner_box.XMax) / 2.0,
        centre_y - (inner_box.YMin + inner_box.YMax) / 2.0,
        base))
    tray_shell = envelope.cut(inner_void).removeSplitter()
    pieces = [tray_shell]
    layout_x = centre_x - inner_l / 2.0
    layout_y = centre_y - inner_w / 2.0
    divider_z = max(0.0, base - 0.05)
    divider_h = height - divider_z
    if use_rows:
        for centre in row_centres:
            cy = layout_y + centre
            wall = Part.makeBox(envelope_box.XLength + 2.0, divider, divider_h,
                                App.Vector(envelope_box.XMin - 1.0,
                                           cy - divider / 2.0, divider_z))
            pieces.append(wall.common(envelope))
    if use_cols:
        for centre in column_centres:
            cx = layout_x + centre
            wall = Part.makeBox(divider, envelope_box.YLength + 2.0, divider_h,
                                App.Vector(cx - divider / 2.0,
                                           envelope_box.YMin - 1.0, divider_z))
            pieces.append(wall.common(envelope))
    result = pieces[0].multiFuse(pieces[1:]).removeSplitter()
    _valid_solid(result, "divider insert")
    if len(result.Solids) != 1:
        raise RuntimeError("Divider tray did not fuse into one printable solid")
    return result, (length, width, height, radius)


def _parse_hole_coordinates(text):
    points = []
    for item in str(text or "").split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [value.strip() for value in item.split(",")]
        if len(parts) != 2:
            raise ValueError("Custom holes must use x,y; x,y format")
        points.append((float(parts[0]), float(parts[1])))
    return points


def _parse_keepout_rectangles(text):
    rectangles = []
    for index, item in enumerate(str(text or "").split(";"), 1):
        item = item.strip()
        if not item:
            continue
        values = [value.strip() for value in item.split(",")]
        if len(values) not in (4, 5):
            raise ValueError(
                "Clearance keep-outs must use x,y,length,width[,label]; separate rectangles with semicolons"
            )
        try:
            x, y, length, width = [float(value) for value in values[:4]]
        except ValueError:
            raise ValueError(
                "Clearance keep-out %d contains a non-numeric position or size" % index)
        if min(x, y) < 0.0 or min(length, width) <= 0.0:
            raise ValueError(
                "Clearance keep-out %d needs non-negative x/y and positive length/width" % index)
        rectangles.append({
            "label": values[4] if len(values) == 5 and values[4]
                     else "Clearance keep-out %d" % index,
            "x_mm": x,
            "y_mm": y,
            "length_mm": length,
            "width_mm": width,
        })
    return rectangles


def build_lid_panel(params):
    params = _resolved_params(params)
    case_l, case_w = _lid_panel_dimensions(params)
    inset = _as_float(params, "edge_inset", 0.0)
    length, width = case_l - 2.0 * inset, case_w - 2.0 * inset
    thickness = _as_float(params, "panel_thickness", MIN_WALL)
    radius = _as_float(params, "panel_corner_radius", 0.0)
    slot_w = _as_float(params, "slot_width", 1.0)
    slot_h = _as_float(params, "slot_height", 1.0)
    pitch_x = _as_float(params, "slot_pitch_x", slot_w)
    pitch_y = _as_float(params, "slot_pitch_y", slot_h)
    hole_d = _as_float(params, "hole_diameter", 0.0)
    hole_edge = _as_float(params, "hole_edge_offset", 0.0)
    if min(length, width) <= 0:
        raise ValueError("Edge inset leaves no lid panel")
    if thickness < MIN_WALL:
        raise ValueError("Panel thickness must be at least %.1f mm" % MIN_WALL)
    if pitch_x < slot_w or pitch_y < slot_h:
        raise ValueError("Slot pitches must be at least the corresponding slot size")
    panel = rounded_prism(length, width, thickness, radius)
    border = max(5.0, radius)
    cutters = []
    x = border
    row = 0
    y = border
    while y + slot_h <= width - border + 1e-7:
        x = border
        while x + slot_w <= length - border + 1e-7:
            slot = rounded_prism(slot_w, slot_h, thickness + 2.0,
                                 min(slot_h / 2.0, slot_w / 2.0), -1.0)
            slot.translate(App.Vector(x, y, 0))
            cutters.append(slot)
            x += pitch_x
        y += pitch_y
        row += 1
    custom = _parse_hole_coordinates(params.get("custom_holes", ""))
    if custom:
        hole_points = custom
    elif hole_d > 0 and hole_edge > 0:
        hole_points = [(hole_edge, hole_edge), (length - hole_edge, hole_edge),
                       (hole_edge, width - hole_edge),
                       (length - hole_edge, width - hole_edge)]
    else:
        hole_points = []
    for hx, hy in hole_points:
        if not (hole_d / 2.0 < hx < length - hole_d / 2.0 and
                hole_d / 2.0 < hy < width - hole_d / 2.0):
            raise ValueError("Mounting hole (%.2f, %.2f) falls outside the panel" % (hx, hy))
        cutters.append(Part.makeCylinder(hole_d / 2.0, thickness + 2.0,
                                         App.Vector(hx, hy, -1.0)))
    if not cutters:
        raise ValueError("Panel dimensions and border leave no slots")
    cutter = cutters[0].multiFuse(cutters[1:])
    result = panel.cut(cutter).removeSplitter()
    _valid_solid(result, "lid panel")
    return result, cutter, len(cutters) - len(hole_points), len(hole_points), (length, width, thickness, radius)


def _rectangles_overlap(first, second, clearance=0.0):
    clearance = float(clearance)
    return not (
        first[0] + first[2] + clearance <= second[0] or
        second[0] + second[2] + clearance <= first[0] or
        first[1] + first[3] + clearance <= second[1] or
        second[1] + second[3] + clearance <= first[1]
    )


def _lid_panel_mount_points(length, width, count, inset):
    """Distribute stable mounting points around an inset perimeter."""
    x0, x1 = float(inset), float(length) - float(inset)
    y0, y1 = float(inset), float(width) - float(inset)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Panel is too small for the configured perimeter mounting inset")
    dx, dy = x1 - x0, y1 - y0
    perimeter = 2.0 * (dx + dy)
    points = []
    phase = min(dx, dy) * 0.25
    for index in range(int(count)):
        distance = (phase + index * perimeter / float(count)) % perimeter
        if distance <= dx:
            point = (x0 + distance, y0)
        elif distance <= dx + dy:
            point = (x1, y0 + distance - dx)
        elif distance <= 2.0 * dx + dy:
            point = (x1 - (distance - dx - dy), y1)
        else:
            point = (x0, y1 - (distance - 2.0 * dx - dy))
        points.append(point)
    return points


def _lid_panel_split_guards(length, width, printer, splitting):
    if not bool(printer.get("split", False)):
        return [], {"columns": 1, "rows": 1}
    usable_x = float(printer["bed_x"]) - 2.0 * float(printer["margin"])
    usable_y = float(printer["bed_y"]) - 2.0 * float(printer["margin"])
    if min(usable_x, usable_y) <= 0.0:
        raise ValueError("Print-bed margin leaves no printable area")
    key_size = (float(splitting["key_size_mm"])
                if splitting.get("keyed_alignment", True) else 0.0)
    count_x, count_y = _keyed_split_counts(
        length, width, usable_x, usable_y, max(0.1, key_size))
    if not splitting.get("keyed_alignment", True):
        count_x = max(1, int(math.ceil(float(length) / usable_x)))
        count_y = max(1, int(math.ceil(float(width) / usable_y)))
    guard = max(2.0, key_size + 2.0 * float(splitting.get("key_clearance_mm", 0.0)))
    rectangles = []
    for column in range(1, count_x):
        seam = float(length) * column / count_x
        rectangles.append((seam - guard / 2.0, 0.0, guard, float(width)))
    for row in range(1, count_y):
        seam = float(width) * row / count_y
        rectangles.append((0.0, seam - guard / 2.0, float(length), guard))
    return rectangles, {"columns": count_x, "rows": count_y}


def _lid_panel_retainer_geometry(length, width, thickness, mounting,
                                 protected_rectangles):
    if not (mounting["perimeter_enabled"] and mounting["retainers_enabled"]):
        return [], [], [], []
    projection = float(mounting["retainer_projection_mm"])
    if projection < 1.2:
        raise ValueError(
            "Printable retainers need at least 1.2 mm projection below the panel"
        )
    retainer_width = float(mounting["retainer_width_mm"])
    clearance = float(mounting["retainer_clearance_mm"])
    shaft_radius = min(2.6, max(1.6, retainer_width * 0.20))
    inset = max(retainer_width * 0.75, shaft_radius + clearance + 2.0)
    points = _lid_panel_mount_points(
        length, width, int(mounting["retainer_count"]), inset)
    holes = []
    recesses = []
    clips = []
    exclusion_rectangles = []
    head_length = retainer_width
    head_width = max(3.2, retainer_width * 0.34)
    head_thickness = min(1.2, thickness * 0.45)
    tab_thickness = min(1.5, projection)
    for x, y in points:
        protected = (
            x - retainer_width / 2.0,
            y - retainer_width / 2.0,
            retainer_width,
            retainer_width,
        )
        if any(_rectangles_overlap(protected, rectangle)
               for rectangle in protected_rectangles):
            raise ValueError(
                "A perimeter retainer overlaps a lid-clearance keep-out; reduce the retainer count or move the keep-out"
            )
        holes.append(Part.makeCylinder(
            shaft_radius + clearance,
            thickness + 2.0,
            App.Vector(x, y, -1.0)))
        recesses.append(Part.makeBox(
            head_length + 2.0 * clearance,
            head_width + 2.0 * clearance,
            head_thickness + clearance + 1.0,
            App.Vector(x - head_length / 2.0 - clearance,
                       y - head_width / 2.0 - clearance,
                       thickness - head_thickness - clearance)))
        shaft = Part.makeCylinder(
            shaft_radius,
            thickness + projection,
            App.Vector(x, y, -projection))
        head = Part.makeBox(
            head_length, head_width, head_thickness,
            App.Vector(x - head_length / 2.0,
                       y - head_width / 2.0,
                       thickness - head_thickness))
        tab = Part.makeBox(
            head_width, head_length, tab_thickness,
            App.Vector(x - head_width / 2.0,
                       y - head_length / 2.0,
                       -projection))
        clip = shaft.fuse(head).fuse(tab).removeSplitter()
        _valid_solid(clip, "inside-lid quarter-turn retainer")
        clips.append(clip)
        exclusion_rectangles.append(protected)
    return holes, recesses, clips, exclusion_rectangles


def build_lid_panel_project(spec):
    """Build one evidenced schema-v1 lid-panel assembly without a document."""
    model_api = _project_module()
    project = model_api.validate_project(spec)
    panel_settings = project["lid_panel"]
    if not panel_settings["enabled"]:
        raise ValueError("Enable the inside-lid panel before building geometry")
    plan = model_api.lid_panel_plan(project)
    length = float(plan["length_mm"])
    width = float(plan["width_mm"])
    thickness = float(panel_settings["thickness_mm"])
    radius = min(float(panel_settings["corner_radius_mm"]), length / 2.0, width / 2.0)
    base_panel = rounded_prism(length, width, thickness, radius)
    keepouts = panel_settings["keepouts"]
    mounting = panel_settings["mounting"]
    printer = project["printer"]
    splitting = panel_settings["splitting"]

    keepout_rectangles = [
        (float(item["x_mm"]), float(item["y_mm"]),
         float(item["length_mm"]), float(item["width_mm"]))
        for item in keepouts["rectangles"]
    ]
    keepout_cutters = [
        Part.makeBox(rectangle[2], rectangle[3], thickness + 2.0,
                     App.Vector(rectangle[0], rectangle[1], -1.0))
        for rectangle in keepout_rectangles
    ]
    seam_guards, split_plan = _lid_panel_split_guards(
        length, width, printer, splitting)
    retainer_holes, retainer_recesses, retainers, retainer_guards = (
        _lid_panel_retainer_geometry(
            length, width, thickness, mounting, keepout_rectangles))

    fastener_holes = []
    fastener_guards = []
    if mounting["fastener_holes_enabled"]:
        diameter = float(mounting["fastener_hole_diameter_mm"])
        hole_radius = diameter / 2.0
        custom = list(mounting["custom_fastener_holes"])
        if custom:
            points = [(float(item["x_mm"]), float(item["y_mm"]))
                      for item in custom]
        else:
            offset = float(mounting["fastener_edge_offset_mm"])
            points = [(offset, offset), (length - offset, offset),
                      (offset, width - offset), (length - offset, width - offset)]
        for x, y in points:
            if not (hole_radius < x < length - hole_radius and
                    hole_radius < y < width - hole_radius):
                raise ValueError(
                    "Fastener hole (%.2f, %.2f) falls outside the finished panel" % (x, y))
            fastener_holes.append(Part.makeCylinder(
                hole_radius, thickness + 2.0, App.Vector(x, y, -1.0)))
            guard_radius = hole_radius + 2.0
            fastener_guards.append(
                (x - guard_radius, y - guard_radius,
                 2.0 * guard_radius, 2.0 * guard_radius))

    lift_cutters = []
    lift_guards = []
    if mounting["lift_access_enabled"]:
        lift_radius = float(mounting["lift_access_diameter_mm"]) / 2.0
        opposite = {
            "top": "bottom", "bottom": "top",
            "left": "right", "right": "left",
        }[keepouts["hinge_edge"]]
        if opposite == "bottom":
            x, y = length / 2.0, 0.0
        elif opposite == "top":
            x, y = length / 2.0, width
        elif opposite == "left":
            x, y = 0.0, width / 2.0
        else:
            x, y = length, width / 2.0
        lift_cutters.append(Part.makeCylinder(
            lift_radius, thickness + 2.0, App.Vector(x, y, -1.0)))
        lift_guards.append(
            (x - lift_radius - 2.0, y - lift_radius - 2.0,
             2.0 * lift_radius + 4.0, 2.0 * lift_radius + 4.0))

    exclusions = (keepout_rectangles + seam_guards + retainer_guards +
                  fastener_guards + lift_guards)
    pattern_cutters = []
    pattern_bounds = []
    pattern = panel_settings["pattern"]
    clearance_margin = float(keepouts["clearance_margin_mm"])
    if pattern == "slot_grid":
        settings = panel_settings["slot_grid"]
        horizontal = settings["orientation"] == "horizontal"
        size_x = float(settings["slot_length_mm"] if horizontal
                       else settings["slot_width_mm"])
        size_y = float(settings["slot_width_mm"] if horizontal
                       else settings["slot_length_mm"])
        pitch_x = float(settings["pitch_x_mm"])
        pitch_y = float(settings["pitch_y_mm"])
        margin_x = float(settings["margin_x_mm"]) + clearance_margin
        margin_y = float(settings["margin_y_mm"]) + clearance_margin
        y = margin_y
        while y + size_y <= width - margin_y + 1e-7:
            x = margin_x
            while x + size_x <= length - margin_x + 1e-7:
                candidate = (x, y, size_x, size_y)
                if not any(_rectangles_overlap(candidate, item)
                           for item in exclusions):
                    tool = rounded_prism(
                        size_x, size_y, thickness + 2.0,
                        min(size_x, size_y) / 2.0, -1.0)
                    tool.translate(App.Vector(x, y, 0.0))
                    pattern_cutters.append(tool)
                    pattern_bounds.append(candidate)
                x += pitch_x
            y += pitch_y
    elif pattern == "perforated_grid":
        settings = panel_settings["perforated_grid"]
        diameter = float(settings["diameter_mm"])
        hole_radius = diameter / 2.0
        pitch_x = float(settings["pitch_x_mm"])
        pitch_y = float(settings["pitch_y_mm"])
        margin_x = float(settings["margin_x_mm"]) + clearance_margin
        margin_y = float(settings["margin_y_mm"]) + clearance_margin
        y = margin_y + hole_radius
        while y + hole_radius <= width - margin_y + 1e-7:
            x = margin_x + hole_radius
            while x + hole_radius <= length - margin_x + 1e-7:
                candidate = (x - hole_radius, y - hole_radius,
                             diameter, diameter)
                if not any(_rectangles_overlap(candidate, item)
                           for item in exclusions):
                    pattern_cutters.append(Part.makeCylinder(
                        hole_radius, thickness + 2.0,
                        App.Vector(x, y, -1.0)))
                    pattern_bounds.append(candidate)
                x += pitch_x
            y += pitch_y
    if pattern != "solid" and not pattern_cutters:
        raise ValueError(
            "Panel dimensions, margins, keep-outs, and split seams leave no pattern openings"
        )

    all_cutters = (keepout_cutters + retainer_holes + retainer_recesses +
                   fastener_holes + lift_cutters + pattern_cutters)
    panel = (base_panel.cut(Part.makeCompound(all_cutters)).removeSplitter()
             if all_cutters else base_panel.copy())
    _valid_solid(panel, "inside-lid panel")
    if len(panel.Solids) != 1:
        raise ValueError(
            "Lid-clearance keep-outs disconnect the panel; resize or move them"
        )
    retainer_set = Part.makeCompound(retainers) if retainers else None
    if retainer_set is not None:
        _valid_solid(retainer_set, "inside-lid printable retainers")
        if panel.common(retainer_set).Volume > 0.01:
            raise RuntimeError("Inside-lid panel and assembled retainers overlap")
    tools = Part.makeCompound(all_cutters) if all_cutters else None
    return {
        "project": project,
        "panel": panel,
        "retainers": retainer_set,
        "retainer_shapes": list(retainers),
        "tools": tools,
        "plan": plan,
        "pattern_count": len(pattern_cutters),
        "pattern_bounds": pattern_bounds,
        "keepout_count": len(keepout_rectangles),
        "fastener_hole_count": len(fastener_holes),
        "retainer_count": len(retainers),
        "split_plan": split_plan,
    }


def _project_case_params(spec):
    case = dict(spec.get("case") or {})
    lid = dict(spec.get("lid") or {})
    printer = dict(spec.get("printer") or {})
    params = {
        "case_model": case.get("case_model", "Custom Case"),
        "internal_length": case.get("internal_length", 300.0),
        "internal_width": case.get("internal_width", 200.0),
        "corner_radius": case.get("corner_radius", 8.0),
        "side_clearance": case.get("side_clearance", 1.0),
        "bottom_clearance": case.get("bottom_clearance", 0.5),
        "taper_allowance": case.get("taper_allowance", 0.5),
        "bed_x": printer.get("bed_x", DEFAULT_BED),
        "bed_y": printer.get("bed_y", DEFAULT_BED),
        "bed_margin": printer.get("margin", 5.0),
        "split_for_bed": bool(printer.get("split", False)),
        "insert_type": "Project Composer",
        "lid_envelope_source": lid.get("envelope_source", "unknown"),
        "lid_length": (lid.get("length_mm")
                       if lid.get("length_mm") is not None else 0.0),
        "lid_width": (lid.get("width_mm")
                      if lid.get("width_mm") is not None else 0.0),
        "lid_clearance_source": lid.get("source", "unknown"),
        "lid_clearance": (lid.get("clearance_mm")
                          if lid.get("clearance_mm") is not None else 0.0),
    }
    if case.get("insert_depth") not in (None, ""):
        params["insert_depth"] = case.get("insert_depth")
    return _resolved_params(params)


def _project_with_layout(project, layout, placement_offsets=None):
    """Apply a pure planner result while keeping failed objects editable."""
    resolved = json.loads(json.dumps(project))
    by_id = {item["id"]: item for item in resolved.get("objects", [])}
    placement_offsets = dict(placement_offsets or {})
    for placement in layout.placements:
        if placement.object_id not in by_id:
            continue
        obj = by_id[placement.object_id]
        offset = float(placement_offsets.get(placement.object_id, 0.0))
        obj.update({
            "x": placement.x + offset,
            "y": placement.y + offset,
            "rotation": placement.rotation,
            "layer": placement.layer,
        })
    resolved["layout_strategy"] = layout.strategy
    resolved["unplaced"] = [item.to_dict() for item in layout.unplaced]
    return resolved


def _svg_planning_dimensions(obj):
    """Return the fail-closed SVG viewport envelope in project millimetres."""
    path = str(obj.get("svg_path", "")).strip()
    if not path:
        raise ValueError("SVG object %s has no source file" % obj.get("id", ""))
    preflight = _addon_module("svg_import").preflight_svg_file(path)
    preflight.require_importable()
    viewport = preflight.metadata.viewport
    scale = float(obj.get("scale", 1.0))
    if scale <= 0.0:
        raise ValueError("SVG object %s scale must be positive" % obj.get("id", ""))
    return viewport.width_mm * scale, viewport.height_mm * scale


def _project_with_svg_dimensions(project):
    """Make imported viewport size authoritative for canvas and layout bounds."""
    resolved = json.loads(json.dumps(project))
    for obj in resolved.get("objects", []):
        if obj.get("type") != "svg_pocket":
            continue
        length, width = _svg_planning_dimensions(obj)
        obj["length"] = round(length, 6)
        obj["width"] = round(width, 6)
    return resolved


def _project_for_clearance_aware_layout(project):
    """Inflate planner envelopes for modifiers that grow generated geometry."""
    planner = _project_with_svg_dimensions(project)
    default_retention_clearance = float(
        planner.get("containment", {}).get("clearance_mm", 0.0))
    placement_offsets = {}
    for obj in planner.get("objects", []):
        clearance = 0.0
        if obj.get("type") == "removable_bin":
            clearance = max(
                0.0, float(obj.get("clearance", default_retention_clearance)))
        elif obj.get("type") != "divider_region":
            clearance = max(0.0, float(obj.get("clearance", 0.0)))
        if (bool(obj.get("finger_scoop", False)) and
                obj.get("type") != "divider_region"):
            clearance += min(
                7.5, max(3.0, min(float(obj["length"]),
                                  float(obj["width"])) * 0.2))
        if clearance <= 0.0:
            continue
        obj["length"] = float(obj["length"]) + 2.0 * clearance
        obj["width"] = float(obj["width"]) + 2.0 * clearance
        obj["x"] = float(obj["x"]) - clearance
        obj["y"] = float(obj["y"]) - clearance
        placement_offsets[obj["id"]] = clearance
    return planner, placement_offsets


def _safe_object_name(prefix, object_id):
    token = "".join(character if character.isalnum() else "_"
                    for character in str(object_id))
    token = token.strip("_") or "Object"
    if token[0].isdigit():
        token = "O_" + token
    return prefix + token[:48]


def _rotate_about_object(shape, obj, z_axis=None):
    angle = float(obj.get("rotation", 0.0)) % 360.0
    if abs(angle) < 0.0001:
        return shape
    x, y = float(obj.get("x", 0.0)), float(obj.get("y", 0.0))
    shape.rotate(App.Vector(x, y, float(z_axis or 0.0)),
                 App.Vector(0, 0, 1), angle)
    length = float(obj.get("length") or obj.get("diameter") or 0.0)
    width = float(obj.get("width") or obj.get("diameter") or 0.0)
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    corners = ((0.0, 0.0), (length, 0.0),
               (0.0, width), (length, width))
    rotated = [(px * cosine - py * sine,
                px * sine + py * cosine) for px, py in corners]
    shape.translate(App.Vector(-min(point[0] for point in rotated),
                               -min(point[1] for point in rotated), 0.0))
    return shape


def _case_layout_inset(params, whole=None):
    """Return the smallest uniform rectangle inset contained by the full BRep."""
    relevant = tuple((key, str(params.get(key, ""))) for key in (
        "case_model", "internal_length", "internal_width", "insert_depth",
        "corner_radius", "side_clearance", "bottom_clearance",
        "taper_allowance"))
    if relevant in _LAYOUT_INSET_CACHE:
        return _LAYOUT_INSET_CACHE[relevant]
    envelope = whole.copy() if whole is not None else _case_blank(params)
    bounds = envelope.BoundBox
    z_margin = min(0.02, max(0.001, bounds.ZLength / 1000.0))
    probe_height = bounds.ZLength - 2.0 * z_margin
    if probe_height <= 0.0:
        raise ValueError("Case envelope is too shallow for layout planning")

    def contained(inset):
        length = bounds.XLength - 2.0 * inset
        width = bounds.YLength - 2.0 * inset
        if min(length, width) <= 0.02:
            return True
        probe = Part.makeBox(
            length, width, probe_height,
            App.Vector(bounds.XMin + inset, bounds.YMin + inset,
                       bounds.ZMin + z_margin))
        outside = probe.cut(envelope).Volume
        tolerance = max(0.01, probe.Volume * 0.000001)
        return outside <= tolerance

    if contained(0.0):
        result = 0.0
    else:
        low = 0.0
        high = min(bounds.XLength, bounds.YLength) / 2.0 - 0.02
        if high <= 0.0 or not contained(high):
            raise ValueError(
                "The case contour has no safe axis-aligned layout rectangle")
        for _index in range(18):
            middle = (low + high) / 2.0
            if contained(middle):
                high = middle
            else:
                low = middle
        result = min(high + 0.05,
                     min(bounds.XLength, bounds.YLength) / 2.0 - 0.01)
    result = round(max(0.0, result), 3)
    if len(_LAYOUT_INSET_CACHE) >= 32:
        _LAYOUT_INSET_CACHE.clear()
    _LAYOUT_INSET_CACHE[relevant] = result
    return result


def _required_project_layout_inset(spec, params, whole=None):
    """Reserve the real contour plus two-layer alignment hardware."""
    inset = _case_layout_inset(params, whole)
    if bool((spec.get("layers") or {}).get("enabled", False)):
        containment = spec.get("containment") or {}
        key_clearance = max(0.2, float(containment.get("clearance_mm", 0.3)))
        # Keys sit 7 mm beyond the contour inset and have a 3 mm peg radius.
        # The final 0.5 mm keeps content envelopes clear of that hardware.
        inset += 10.5 + key_clearance
    return inset


def _assert_inside_case(footprint, whole, object_id):
    """Reject a placed object's occupied volume when it crosses the case BRep."""
    outside = footprint.cut(whole).Volume
    tolerance = max(0.01, footprint.Volume * 0.000001)
    if outside > tolerance:
        raise ValueError(
            "Object %s extends outside the usable case contour by %.3f mm^3; "
            "move it inward or run Auto Layout." % (object_id, outside))


def _assert_no_intersection(first, second, label):
    overlap = first.common(second).Volume
    tolerance = max(0.001, min(first.Volume, second.Volume) * 0.000001)
    if overlap > tolerance:
        raise RuntimeError(
            "%s has %.3f mm^3 of unintended assembly overlap" %
            (label, overlap))


def _scaled_xy_inset(shape, inset):
    """Conservatively shrink a contour around its centre by inset per side."""
    inset = max(0.0, float(inset))
    bounds = shape.BoundBox
    if inset < 0.0001:
        return shape.copy()
    if bounds.XLength <= 2.0 * inset or bounds.YLength <= 2.0 * inset:
        raise ValueError("Retention inset leaves no usable panel")
    centre_x = (bounds.XMin + bounds.XMax) / 2.0
    centre_y = (bounds.YMin + bounds.YMax) / 2.0
    moved = shape.copy()
    moved.translate(App.Vector(-centre_x, -centre_y, 0))
    matrix = App.Matrix()
    matrix.A11 = (bounds.XLength - 2.0 * inset) / bounds.XLength
    matrix.A22 = (bounds.YLength - 2.0 * inset) / bounds.YLength
    matrix.A33 = 1.0
    scaled = moved.transformGeometry(matrix)
    scaled.translate(App.Vector(centre_x, centre_y, 0))
    return scaled


def _open_bin_shape(length, width, height, wall, floor, origin):
    if min(width, length, height) <= 0.0:
        raise ValueError("Removable-bin dimensions must be positive")
    if width <= 2.0 * wall or length <= 2.0 * wall or height <= floor:
        raise ValueError("Removable bin is too small for its wall and floor")
    x, y, z = origin
    outer = Part.makeBox(length, width, height, App.Vector(x, y, z))
    inner = Part.makeBox(length - 2.0 * wall, width - 2.0 * wall,
                         height - floor + 1.0,
                         App.Vector(x + wall, y + wall, z + floor))
    result = outer.cut(inner).removeSplitter()
    _valid_solid(result, "removable bin")
    return result


def _individual_bin_geometry(length, width, height, wall, floor, origin,
                             clearance, thickness):
    """Return an open bin with sliding rails and a collision-free fitted lid."""
    clearance = max(0.2, float(clearance))
    thickness = max(1.2, float(thickness))
    if height <= thickness + clearance + floor + 1.0:
        raise ValueError(
            "Removable bin is too shallow for its floor and sliding lid")
    x, y, z = origin
    inner_length = length - 2.0 * wall
    inner_width = width - 2.0 * wall
    lid_length = inner_length - 2.0 * clearance
    lid_width = inner_width - 2.0 * clearance
    if min(lid_length, lid_width) <= 4.0:
        raise ValueError("Individual lid is too small after wall and print clearance")

    body = _open_bin_shape(length, width, height, wall, floor, origin)
    lid_z = z + height - thickness
    rail_thickness = max(1.0, min(1.4, thickness * 0.75))
    support = max(1.0, clearance + 0.7)
    rail_z = lid_z - clearance - rail_thickness
    rails = [
        Part.makeBox(inner_length, support, rail_thickness,
                     App.Vector(x + wall, y + wall, rail_z)),
        Part.makeBox(inner_length, support, rail_thickness,
                     App.Vector(x + wall, y + width - wall - support, rail_z)),
    ]
    stop = Part.makeBox(max(1.0, support), inner_width, rail_thickness,
                        App.Vector(x + wall, y + wall, rail_z))
    body = body.fuse(Part.makeCompound(rails + [stop])).removeSplitter()

    # The X-max wall receives the sliding lid; the opposite low stop prevents
    # it from passing through the bin during normal use.
    aperture = Part.makeBox(
        wall + 2.0, lid_width + 2.0 * clearance,
        thickness + 2.0 * clearance,
        App.Vector(x + length - wall - 1.0, y + wall,
                   lid_z - clearance))
    body = body.cut(aperture).removeSplitter()
    _valid_solid(body, "removable bin with sliding-lid rails")

    lid = Part.makeBox(
        lid_length, lid_width, thickness,
        App.Vector(x + wall + clearance, y + wall + clearance, lid_z))
    notch_radius = min(5.0, lid_width / 4.0)
    notch = Part.makeCylinder(
        notch_radius, thickness + 2.0,
        App.Vector(x + wall + clearance + lid_length,
                   y + width / 2.0, lid_z - 1.0))
    lid = lid.cut(notch).removeSplitter()
    _valid_solid(lid, "individual bin lid")
    _assert_no_intersection(body, lid, "Individual bin lid and rails")
    return body, lid


def _divider_walls(obj, z_bottom, z_top):
    x, y = float(obj["x"]), float(obj["y"])
    width, length = float(obj["width"]), float(obj["length"])
    wall = float(obj.get("wall", MIN_WALL))
    rows = max(1, int(obj.get("rows", 2)))
    columns = max(1, int(obj.get("columns", 2)))
    height = z_top - z_bottom
    shapes = [
        Part.makeBox(length, wall, height, App.Vector(x, y, z_bottom)),
        Part.makeBox(length, wall, height, App.Vector(x, y + width - wall, z_bottom)),
        Part.makeBox(wall, width, height, App.Vector(x, y, z_bottom)),
        Part.makeBox(wall, width, height, App.Vector(x + length - wall, y, z_bottom)),
    ]
    clear_length = length - 2.0 * wall
    clear_width = width - 2.0 * wall
    for index in range(1, columns):
        divider_x = x + wall + clear_length * index / columns - wall / 2.0
        shapes.append(Part.makeBox(wall, width - 2.0 * wall, height,
                                   App.Vector(divider_x, y + wall, z_bottom)))
    for index in range(1, rows):
        divider_y = y + wall + clear_width * index / rows - wall / 2.0
        shapes.append(Part.makeBox(length - 2.0 * wall, wall, height,
                                   App.Vector(x + wall, divider_y, z_bottom)))
    result = shapes[0].multiFuse(shapes[1:]).removeSplitter()
    _valid_solid(result, "divider-region walls")
    return result


def _shared_panel_geometry(whole, top_z, settings):
    thickness = float(settings.get("panel_thickness_mm", 2.0))
    clearance = float(settings.get("clearance_mm", 0.3))
    ledge = max(3.0, float(settings.get("ledge_width_mm", 4.0)))
    if thickness < 1.2 or top_z <= thickness:
        raise ValueError("Shared-panel thickness is outside the usable insert height")
    bounds = whole.BoundBox
    slab_tool = Part.makeBox(bounds.XLength + 2.0, bounds.YLength + 2.0,
                             thickness, App.Vector(bounds.XMin - 1.0,
                                                   bounds.YMin - 1.0,
                                                   top_z - thickness))
    top_slab = whole.common(slab_tool)
    panel = _scaled_xy_inset(top_slab, ledge + clearance)
    recess = _scaled_xy_inset(top_slab, ledge)
    panel_bounds = panel.BoundBox
    hole_radius = min(5.0, max(2.5, panel_bounds.YLength / 18.0))
    finger_holes = [
        Part.makeCylinder(hole_radius, thickness + 2.0,
                          App.Vector(panel_bounds.XMin + panel_bounds.XLength / 2.0,
                                     panel_bounds.YMin + hole_radius * 0.65,
                                     top_z - thickness - 1.0)),
        Part.makeCylinder(hole_radius, thickness + 2.0,
                          App.Vector(panel_bounds.XMin + panel_bounds.XLength / 2.0,
                                     panel_bounds.YMax - hole_radius * 0.65,
                                     top_z - thickness - 1.0)),
    ]
    latch_inset = max(7.0, ledge + 3.0)
    latch_points = [
        (panel_bounds.XMin + latch_inset, panel_bounds.YMin + latch_inset),
        (panel_bounds.XMax - latch_inset, panel_bounds.YMin + latch_inset),
        (panel_bounds.XMin + latch_inset, panel_bounds.YMax - latch_inset),
        (panel_bounds.XMax - latch_inset, panel_bounds.YMax - latch_inset),
    ]
    shaft_radius = 2.4
    head_length, head_width = 10.0, 3.2
    head_thickness = min(1.2, thickness * 0.55)
    tab_thickness = 1.5
    tab_z = top_z - thickness - tab_thickness - clearance
    latch_holes = []
    head_recesses = []
    receiver_cutters = []
    clips = []
    for x, y in latch_points:
        latch_holes.append(Part.makeCylinder(
            shaft_radius + clearance, thickness + 2.0,
            App.Vector(x, y, top_z - thickness - 1.0)))
        head_recesses.append(Part.makeBox(
            head_length + 2.0 * clearance,
            head_width + 2.0 * clearance,
            head_thickness + clearance,
            App.Vector(x - head_length / 2.0 - clearance,
                       y - head_width / 2.0 - clearance,
                       top_z - head_thickness - clearance)))
        receiver_cutters.extend([
            Part.makeCylinder(
                shaft_radius + clearance, thickness + tab_thickness + 2.0,
                App.Vector(x, y, tab_z - clearance)),
            Part.makeBox(
                head_length + 2.0 * clearance,
                head_width + 2.0 * clearance,
                tab_thickness + 2.0 * clearance,
                App.Vector(x - head_length / 2.0 - clearance,
                           y - head_width / 2.0 - clearance,
                           tab_z - clearance)),
        ])
        shaft = Part.makeCylinder(
            shaft_radius, top_z - tab_z, App.Vector(x, y, tab_z))
        head = Part.makeBox(
            head_length, head_width, head_thickness,
            App.Vector(x - head_length / 2.0, y - head_width / 2.0,
                       top_z - head_thickness))
        tab = Part.makeBox(
            head_length, head_width, tab_thickness,
            App.Vector(x - head_length / 2.0, y - head_width / 2.0, tab_z))
        clip = shaft.fuse(head).fuse(tab).removeSplitter()
        _valid_solid(clip, "quarter-turn clip")
        clips.append(clip)
        clip_bounds = clip.BoundBox
        receiver_cutters.append(Part.makeBox(
            clip_bounds.XLength + 2.0 * clearance,
            clip_bounds.YLength + 2.0 * clearance,
            clip_bounds.ZLength + 2.0 * clearance,
            App.Vector(clip_bounds.XMin - clearance,
                       clip_bounds.YMin - clearance,
                       clip_bounds.ZMin - clearance)))
    panel_cutters = finger_holes + latch_holes + head_recesses
    panel = panel.cut(Part.makeCompound(panel_cutters)).removeSplitter()
    _valid_solid(panel, "shared retention panel")
    clip_set = Part.makeCompound(clips)
    receiver = Part.makeCompound(receiver_cutters)
    carrier_cut = Part.makeCompound([recess, receiver])
    _assert_no_intersection(panel, clip_set, "Shared panel and quarter-turn clips")
    return panel, carrier_cut, clip_set


def generate_project(spec, document=None):
    """Generate an editable schema-v1 composed insert project."""
    model_api = _project_module()
    preserved_unplaced = list(spec.get("unplaced", [])) if isinstance(spec, dict) else []
    requested_layout = spec.get("layout_strategy") if isinstance(spec, dict) else None
    normalized = model_api.validate_project(spec)
    normalized = model_api.validate_project(
        _project_with_svg_dimensions(normalized))
    params = _project_case_params(normalized)
    if normalized["case"].get("insert_depth") is None:
        normalized["case"]["insert_depth"] = float(params["insert_depth"])
        normalized = model_api.validate_project(normalized)
    whole = _case_blank(params)
    actual_inset = _required_project_layout_inset(normalized, params, whole)
    if normalized["case"].get("layout_inset", 0.0) < actual_inset:
        normalized["case"]["layout_inset"] = round(actual_inset, 3)
        normalized = model_api.validate_project(normalized)
    if requested_layout:
        planner_spec, placement_offsets = _project_for_clearance_aware_layout(
            normalized)
        layout = model_api.layout_project(planner_spec, requested_layout)
        normalized = _project_with_layout(
            normalized, layout, placement_offsets)
    elif preserved_unplaced:
        normalized["unplaced"] = preserved_unplaced

    total_height = whole.BoundBox.ZLength
    layers = dict(normalized.get("layers") or {})
    use_layers = bool(layers.get("enabled", False))
    ratio = float(layers.get("ratio", 0.5))
    floor = float(layers.get("floor_mm", 2.4))
    containment = dict(normalized.get("containment") or {})
    containment_mode = containment.get("mode", "none")
    containment_clearance = float(containment.get("clearance_mm", 0.3))
    panel_reserve = 0.0
    if containment_mode == "shared_panel":
        panel_reserve = (float(containment.get("panel_thickness_mm", 2.0)) +
                         containment_clearance)
    content_height = total_height - panel_reserve
    bounds = whole.BoundBox
    z_origin = bounds.ZMin
    content_ceiling = z_origin + content_height
    if use_layers:
        total_clear = content_height - 2.0 * floor
        if total_clear <= 0.0:
            raise ValueError("Two carrier floors leave no usable layer height")
        lower_clear = total_clear * ratio
        upper_clear = total_clear - lower_clear
        split_z = z_origin + floor + lower_clear
        lower_tool = Part.makeBox(bounds.XLength + 2.0, bounds.YLength + 2.0,
                                  split_z - z_origin + 1.0,
                                  App.Vector(bounds.XMin - 1.0,
                                             bounds.YMin - 1.0,
                                             z_origin - 1.0))
        upper_tool = Part.makeBox(bounds.XLength + 2.0, bounds.YLength + 2.0,
                                  z_origin + total_height - split_z + 1.0,
                                  App.Vector(bounds.XMin - 1.0,
                                             bounds.YMin - 1.0, split_z))
        carriers = {"lower": whole.common(lower_tool),
                    "upper": whole.common(upper_tool)}
        layer_info = {
            "lower": {"bottom": z_origin, "top": split_z,
                      "content_top": split_z, "clear": lower_clear,
                      "access_top": split_z},
            "upper": {"bottom": split_z,
                      "top": z_origin + total_height,
                      "content_top": content_ceiling, "clear": upper_clear,
                      "access_top": z_origin + total_height},
        }
    else:
        clear_height = content_height - floor
        if clear_height <= 0.0:
            raise ValueError("Carrier floor leaves no usable object height")
        carriers = {"lower": whole.copy()}
        layer_info = {
            "lower": {"bottom": z_origin,
                      "top": z_origin + total_height,
                      "content_top": content_ceiling, "clear": clear_height,
                      "access_top": z_origin + total_height},
        }

    cutters = {key: [] for key in carriers}
    additions = {key: [] for key in carriers}
    occupied = {key: [] for key in carriers}
    extra_parts = []
    warnings = []
    unplaced_ids = {
        str(item.get("object_id")) for item in normalized.get("unplaced", [])
        if isinstance(item, dict) and item.get("object_id")
    }

    def register_footprint(layer_name, object_id, footprint):
        _assert_inside_case(footprint, whole, object_id)
        prior_index = None
        for index, (other_id, other) in enumerate(occupied[layer_name]):
            if other_id == object_id:
                prior_index = index
                continue
            overlap = footprint.common(other).Volume
            tolerance = max(0.01, min(footprint.Volume, other.Volume) * 0.000001)
            if overlap > tolerance:
                raise ValueError(
                    "Object %s collides with %s on the %s layer by %.3f mm^3" %
                    (object_id, other_id, layer_name, overlap))
        if prior_index is None:
            occupied[layer_name].append((object_id, footprint))
        else:
            previous = occupied[layer_name][prior_index][1]
            occupied[layer_name][prior_index] = (
                object_id, Part.makeCompound([previous, footprint]))

    for obj in normalized.get("objects", []):
        if obj["id"] in unplaced_ids:
            continue
        layer = obj.get("layer", "lower") if use_layers else "lower"
        if layer not in carriers:
            raise ValueError("Object %s targets an unavailable layer" % obj["id"])
        info = layer_info[layer]
        depth = float(obj.get("height", 10.0))
        if depth > info["clear"] + 0.000001:
            raise ValueError(
                "Object %s is %.3f mm high but the %s layer has only %.3f mm "
                "of clear height after its %.3f mm floor." %
                (obj["id"], depth, layer, info["clear"], floor))
        content_top = info["content_top"]
        cut_bottom = content_top - depth
        cut_height = info["access_top"] - cut_bottom + 1.0
        footprint_height = max(0.001, depth - 0.02)
        footprint_z = cut_bottom + min(0.01, depth / 4.0)
        object_type = obj["type"]
        x, y = float(obj["x"]), float(obj["y"])
        fit_clearance = (0.0 if object_type == "divider_region" else
                         max(0.0, float(obj.get("clearance", 0.0))))
        if object_type == "circular_pocket":
            diameter = float(obj["diameter"])
            cutter_diameter = diameter + 2.0 * fit_clearance
            cutter = Part.makeCylinder(cutter_diameter / 2.0, cut_height,
                                       App.Vector(x + diameter / 2.0,
                                                  y + diameter / 2.0,
                                                  cut_bottom))
            cutters[layer].append(cutter)
            footprint = Part.makeCylinder(
                cutter_diameter / 2.0, footprint_height,
                App.Vector(x + diameter / 2.0, y + diameter / 2.0,
                           footprint_z))
            register_footprint(layer, obj["id"], footprint)
        elif object_type in ("rectangular_pocket", "existing_container_bay"):
            cut_obj = dict(obj)
            cut_obj.update({
                "x": x - fit_clearance,
                "y": y - fit_clearance,
                "length": float(obj["length"]) + 2.0 * fit_clearance,
                "width": float(obj["width"]) + 2.0 * fit_clearance,
            })
            cutter = Part.makeBox(float(cut_obj["length"]),
                                  float(cut_obj["width"]), cut_height,
                                  App.Vector(cut_obj["x"], cut_obj["y"],
                                             cut_bottom))
            cutter = _rotate_about_object(cutter, cut_obj, content_top)
            cutters[layer].append(cutter)
            footprint = Part.makeBox(
                float(cut_obj["length"]), float(cut_obj["width"]),
                footprint_height,
                App.Vector(cut_obj["x"], cut_obj["y"], footprint_z))
            footprint = _rotate_about_object(footprint, cut_obj, content_top)
            register_footprint(layer, obj["id"], footprint)
        elif object_type == "svg_pocket":
            faces, open_count = _import_svg_faces(
                obj.get("svg_path", ""), float(obj.get("scale", 1.0)), x, y,
                float(obj.get("rotation", 0.0)),
                float(obj.get("clearance", 0.0)))
            svg_cutters = []
            svg_footprints = []
            for face in faces:
                cutter = face.extrude(App.Vector(0, 0, cut_height))
                cutter.translate(App.Vector(0, 0, cut_bottom))
                svg_cutters.append(cutter)
                footprint = face.extrude(App.Vector(0, 0, footprint_height))
                footprint.translate(App.Vector(0, 0, footprint_z))
                svg_footprints.append(footprint)
            cutters[layer].append(Part.makeCompound(svg_cutters))
            register_footprint(
                layer, obj["id"], Part.makeCompound(svg_footprints))
            if open_count:
                warnings.append("SVG object %s contains %d open path(s)." %
                                (obj["id"], open_count))
        elif object_type == "removable_bin":
            width, length = float(obj["width"]), float(obj["length"])
            bin_height = depth
            bay_clearance = max(0.0, float(obj.get("clearance", containment_clearance)))
            bay_obj = dict(obj)
            bay_obj.update({"x": x - bay_clearance, "y": y - bay_clearance,
                            "length": length + 2.0 * bay_clearance,
                            "width": width + 2.0 * bay_clearance})
            bay = Part.makeBox(length + 2.0 * bay_clearance,
                               width + 2.0 * bay_clearance,
                               info["access_top"] - cut_bottom + 1.0,
                               App.Vector(x - bay_clearance, y - bay_clearance,
                                          cut_bottom))
            bay = _rotate_about_object(bay, bay_obj, content_top)
            cutters[layer].append(bay)
            wall = float(obj.get("wall", 1.8))
            origin = (x, y, content_top - bin_height)
            raw_lid = None
            if containment_mode == "individual_lids":
                raw_bin_shape, raw_lid = _individual_bin_geometry(
                    length, width, bin_height, wall, floor, origin,
                    containment_clearance,
                    float(containment.get("panel_thickness_mm", 1.6)))
            else:
                raw_bin_shape = _open_bin_shape(
                    length, width, bin_height, wall, floor, origin)
            bin_shape = _rotate_about_object(
                raw_bin_shape.copy(), obj, content_top)
            extra_parts.append((
                _safe_object_name("Bin_", obj["id"]),
                obj.get("name") or "Removable bin %s" % obj["id"], bin_shape))
            if raw_lid is not None:
                lid = _rotate_about_object(raw_lid, obj, content_top)
                _assert_no_intersection(
                    bin_shape, lid, "Individual lid for %s" % obj["id"])
                extra_parts.append((
                    _safe_object_name("Lid_", obj["id"]),
                    "%s sliding lid" % (obj.get("name") or obj["id"]), lid))
            footprint = Part.makeBox(
                length + 2.0 * bay_clearance,
                width + 2.0 * bay_clearance, footprint_height,
                App.Vector(x - bay_clearance, y - bay_clearance, footprint_z))
            footprint = _rotate_about_object(footprint, bay_obj, content_top)
            register_footprint(layer, obj["id"], footprint)
        elif object_type == "divider_region":
            cavity = Part.makeBox(float(obj["length"]), float(obj["width"]),
                                  cut_height,
                                  App.Vector(x, y, cut_bottom))
            cutters[layer].append(
                _rotate_about_object(cavity, obj, content_top))
            walls = _divider_walls(obj, cut_bottom, content_top)
            additions[layer].append(
                _rotate_about_object(walls, obj, content_top))
            footprint = Part.makeBox(
                float(obj["length"]), float(obj["width"]), footprint_height,
                App.Vector(x, y, footprint_z))
            footprint = _rotate_about_object(footprint, obj, content_top)
            register_footprint(layer, obj["id"], footprint)
        else:
            raise ValueError("Unsupported project object type: %s" % object_type)

        if (bool(obj.get("finger_scoop", False)) and
                object_type != "divider_region"):
            object_length = float(obj.get("length") or obj.get("diameter"))
            object_width = float(obj.get("width") or obj.get("diameter"))
            scoop_radius = min(
                7.5, max(3.0, min(object_length, object_width) * 0.2))
            scoop_obj = dict(obj)
            scoop_obj.update({
                "x": x - fit_clearance,
                "y": y - fit_clearance,
                "length": object_length + 2.0 * fit_clearance,
                "width": object_width + 2.0 * fit_clearance,
            })
            scoop_x = x + object_length / 2.0
            scoop_y = y - fit_clearance
            scoop = Part.makeCylinder(
                scoop_radius, cut_height,
                App.Vector(scoop_x, scoop_y, cut_bottom))
            scoop = _rotate_about_object(scoop, scoop_obj, content_top)
            cutters[layer].append(scoop)
            scoop_footprint = Part.makeCylinder(
                scoop_radius, footprint_height,
                App.Vector(scoop_x, scoop_y, footprint_z))
            scoop_footprint = _rotate_about_object(
                scoop_footprint, scoop_obj, content_top)
            register_footprint(layer, obj["id"], scoop_footprint)

    for layer, carrier in list(carriers.items()):
        if cutters[layer]:
            carrier = carrier.cut(Part.makeCompound(cutters[layer]))
        if additions[layer]:
            carrier = carrier.fuse(Part.makeCompound(additions[layer]))
        carrier = carrier.removeSplitter()
        _valid_solid(carrier, "%s carrier" % layer)
        carriers[layer] = carrier

    if use_layers:
        key_clearance = max(0.2, containment_clearance)
        contour_inset = _case_layout_inset(params, whole)
        key_inset = contour_inset + 7.0
        if (bounds.XLength <= 2.0 * key_inset or
                bounds.YLength <= 2.0 * key_inset):
            raise ValueError("Case is too small for keyed two-layer alignment")
        key_points = [
            (bounds.XMin + key_inset, bounds.YMin + key_inset),
            (bounds.XMax - key_inset, bounds.YMin + key_inset),
            (bounds.XMin + key_inset, bounds.YMax - key_inset),
            (bounds.XMax - key_inset, bounds.YMax - key_inset),
        ]
        sockets = [Part.makeCylinder(3.0 + key_clearance, 3.0,
                                     App.Vector(x, y, split_z - 3.0))
                   for x, y in key_points]
        pegs = [Part.makeCylinder(3.0, 3.0,
                                  App.Vector(x, y, split_z - 3.0))
                for x, y in key_points]
        peg_set = Part.makeCompound(pegs)
        for object_id, envelope in occupied["lower"]:
            overlap = envelope.common(peg_set).Volume
            if overlap > 0.01:
                raise ValueError(
                    "Lower object %s overlaps a keyed alignment zone by "
                    "%.3f mm^3; move it inward or run Auto Layout." %
                    (object_id, overlap))
        carriers["lower"] = carriers["lower"].cut(Part.makeCompound(sockets)).removeSplitter()
        carriers["upper"] = carriers["upper"].fuse(peg_set).removeSplitter()

        lift_radius = min(
            8.0, max(4.0, min(bounds.XLength, bounds.YLength) / 18.0))
        lift_height = carriers["upper"].BoundBox.ZLength + 2.0
        lift_z = carriers["upper"].BoundBox.ZMin - 1.0
        lift_y = bounds.YMin + bounds.YLength / 2.0
        lift_cutters = Part.makeCompound([
            Part.makeCylinder(lift_radius, lift_height,
                              App.Vector(bounds.XMin, lift_y, lift_z)),
            Part.makeCylinder(lift_radius, lift_height,
                              App.Vector(bounds.XMax, lift_y, lift_z)),
        ])
        carriers["upper"] = carriers["upper"].cut(lift_cutters).removeSplitter()
        _valid_solid(carriers["lower"], "keyed lower carrier")
        _valid_solid(carriers["upper"], "keyed upper carrier with lift access")
        _assert_no_intersection(
            carriers["lower"], carriers["upper"], "Two-layer carriers")
        for object_id, envelope in occupied["lower"]:
            overlap = envelope.common(carriers["upper"]).Volume
            if overlap > 0.01:
                raise ValueError(
                    "Upper carrier underside overlaps lower object %s by "
                    "%.3f mm^3; reduce its height or move it inward." %
                    (object_id, overlap))
        warnings.append(
            "Upper carrier includes keyed alignment, support surfaces, and "
            "two side lift-access notches.")

    if containment_mode == "shared_panel":
        top_layer = "upper" if use_layers else "lower"
        panel, recess, clips = _shared_panel_geometry(whole, total_height, containment)
        carriers[top_layer] = carriers[top_layer].cut(recess).removeSplitter()
        _valid_solid(carriers[top_layer], "shared-panel carrier")
        _assert_no_intersection(
            carriers[top_layer], panel, "Shared panel and carrier")
        _assert_no_intersection(
            carriers[top_layer], clips, "Shared clips and carrier")
        extra_parts.extend([
            ("SharedRetentionPanel", "Shared inner retention panel", panel),
            ("SharedPanelClips", "Quarter-turn retention clips", clips),
        ])
        warnings.append(
            "Shared panel and quarter-turn clips are a printable prototype; "
            "calibrate the retention coupon and loaded carry before release.")
    warnings.extend(model_api.uncovered_storage_warnings(normalized))

    source_parts = []
    for layer in ("lower", "upper"):
        if layer in carriers:
            source_parts.append(("%sCarrier" % layer.title(),
                                 "%s carrier" % layer.title(), carriers[layer]))
    for name, label, shape in extra_parts:
        _valid_solid(shape, label)
        source_parts.append((name, label, shape))

    bed_x = float(normalized["printer"]["bed_x"])
    bed_y = float(normalized["printer"]["bed_y"])
    bed_margin = float(normalized["printer"]["margin"])
    usable_x, usable_y = bed_x - 2.0 * bed_margin, bed_y - 2.0 * bed_margin
    printable_parts = []
    for name, label, shape in source_parts:
        split_shapes = ([shape.copy()]
                        if not normalized["printer"]["split"] else
                        split_shape_for_bed(shape, bed_x, bed_y, bed_margin))
        if len(split_shapes) > 1:
            warnings.append(
                "%s split into %d bed-sized parts with straight butt seams." %
                (label, len(split_shapes)))
        for index, part_shape in enumerate(split_shapes, 1):
            if len(split_shapes) == 1:
                part_name, part_label = name, label
            else:
                part_name = "%sPart%02d" % (name, index)
                part_label = "%s — part %02d" % (label, index)
            if (not normalized["printer"]["split"] and
                    (part_shape.BoundBox.XLength > usable_x + 0.01 or
                     part_shape.BoundBox.YLength > usable_y + 0.01)):
                warnings.append(
                    "%s footprint %.1f x %.1f mm exceeds usable printer area "
                    "%.1f x %.1f mm; enable bed splitting." %
                    (label, part_shape.BoundBox.XLength,
                     part_shape.BoundBox.YLength, usable_x, usable_y))
            printable_parts.append((part_name, part_label, part_shape))

    # Validate metadata serialization before touching the last good result.
    # Result names are assigned by FreeCAD during the transaction below.
    json.dumps(normalized, sort_keys=True, allow_nan=False)
    json.dumps(params, sort_keys=True, allow_nan=False)
    doc = document or App.ActiveDocument
    if doc is None:
        doc = App.newDocument("CaseInsertProject")
    with _generation_transaction(doc, "Generate composed case insert"):
        _safe_remove_group(doc)
        group = _mark_generator_object(
            doc.addObject("App::DocumentObjectGroup", PROJECT_GROUP), "project-root")
        group.Label = "Case Insert Generator"
        print_objects = [
            _add_shape(doc, group, name, label, shape)
            for name, label, shape in printable_parts
        ]

        result_names = [item.Name for item in print_objects]
        normalized["result"] = result_names[0] if result_names else ""
        normalized["results"] = result_names
        normalized["parts"] = len(result_names)
        normalized["warnings"] = list(warnings)
        normalized["unplaced"] = list(normalized.get("unplaced", []))

        length, width, _depth, _radius = _effective_case(params)
        model = _case_model(params)
        rim_z = ((float(model.get("bottom_depth") or model["internal_depth"]) -
                  _as_float(params, "bottom_clearance", 0.0)) if model
                 else total_height)
        _add_clearance_references(doc, group, params, length, width, rim_z)
        params_obj = _add_parameter_object(
            doc, group, params, result_names)
        params_obj.addProperty("App::PropertyInteger", "SchemaVersion", "Project")
        params_obj.SchemaVersion = 1
        params_obj.addProperty("App::PropertyString", "ProjectJSON", "Project")
        params_obj.ProjectJSON = json.dumps(normalized, sort_keys=True, allow_nan=False)
        params_obj.addProperty("App::PropertyStringList", "Warnings", "Project")
        params_obj.Warnings = warnings
        group.addProperty("App::PropertyInteger", "SchemaVersion", "Project")
        group.SchemaVersion = 1
        group.addProperty("App::PropertyString", "ProjectJSON", "Project")
        group.ProjectJSON = params_obj.ProjectJSON
        doc.recompute()
        report = {
            "document": doc.Name,
            "results": result_names,
            "parts": len(print_objects),
            "mode": "Project Composer",
            "valid": all(item.Shape.isValid() for item in print_objects),
            "solids": sum(len(item.Shape.Solids) for item in print_objects),
            "volume": sum(item.Shape.Volume for item in print_objects),
            "warnings": warnings,
            "unplaced": list(normalized.get("unplaced", [])),
            "project": normalized,
        }
        result_type = getattr(model_api, "GenerationResult", None)
        return result_type.from_mapping(report) if result_type and hasattr(result_type, "from_mapping") else report


def _store_schema_project(doc, group, project, params, result_names, warnings):
    project_json = json.dumps(project, sort_keys=True, allow_nan=False)
    params_obj = _add_parameter_object(doc, group, params, result_names)
    params_obj.addProperty("App::PropertyInteger", "SchemaVersion", "Project")
    params_obj.SchemaVersion = 1
    params_obj.addProperty("App::PropertyString", "ProjectJSON", "Project")
    params_obj.ProjectJSON = project_json
    params_obj.addProperty("App::PropertyStringList", "Warnings", "Project")
    params_obj.Warnings = list(warnings)
    group.addProperty("App::PropertyInteger", "SchemaVersion", "Project")
    group.SchemaVersion = 1
    group.addProperty("App::PropertyString", "ProjectJSON", "Project")
    group.ProjectJSON = params_obj.ProjectJSON
    return params_obj


def preview_lid_panel_project(spec, document=None):
    """Store and display a non-printable schema-v1 panel configuration."""
    model_api = _project_module()
    project = model_api.validate_project(spec)
    budget = model_api.lid_panel_height_budget(project)
    warnings = list(budget["reasons"])
    warnings.append(
        "Preview only — no STL/STEP print part was generated and physical fit remains unverified."
    )
    preview_shapes = []
    preview_package = None
    lid = project["lid"]
    if lid.get("length_mm") is not None and lid.get("width_mm") is not None:
        plan = model_api.lid_panel_plan(project)
        lid_length = float(lid["length_mm"])
        lid_width = float(lid["width_mm"])
        preview_shapes.append((
            "LidEnvelopePreview",
            "Lid envelope preview (non-printable)",
            Part.makePlane(lid_length, lid_width),
            "lid-envelope-reference",
        ))
        preview_package = build_lid_panel_project(project)
        offset = App.Vector(float(plan["x_mm"]), float(plan["y_mm"]), 0.2)
        preview = preview_package["panel"].copy()
        preview.translate(offset)
        preview_shapes.append((
            "LidPanelPreview",
            "Configured lid panel preview (non-printable)",
            preview,
            "lid-panel-preview",
        ))
        if preview_package["retainers"] is not None:
            retainer_preview = preview_package["retainers"].copy()
            retainer_preview.translate(offset)
            preview_shapes.append((
                "LidRetainersPreview",
                "Configured perimeter retainers (non-printable)",
                retainer_preview,
                "lid-retainer-preview",
            ))
        for index, item in enumerate(project["lid_panel"]["keepouts"]["rectangles"], 1):
            keepout = Part.makeBox(
                float(item["length_mm"]), float(item["width_mm"]), 0.25,
                App.Vector(float(plan["x_mm"]) + float(item["x_mm"]),
                           float(plan["y_mm"]) + float(item["y_mm"]), 0.65))
            preview_shapes.append((
                "LidKeepoutPreview%02d" % index,
                "%s (non-printable keep-out)" % item["label"],
                keepout,
                "lid-keepout-reference",
            ))

    params = _project_case_params(project)
    params["insert_type"] = "Lid Panel Preview"
    project["result"] = ""
    project["results"] = []
    project["parts"] = 0
    project["warnings"] = warnings
    project["lid_panel_report"] = {
        **budget,
        "pattern_count": (
            int(preview_package["pattern_count"]) if preview_package else 0),
        "retainer_count": (
            int(preview_package["retainer_count"]) if preview_package else 0),
        "panel_plan": (
            dict(preview_package["plan"]) if preview_package else None),
    }
    project.setdefault("verification", {})["physical_fit"] = False
    json.dumps(project, sort_keys=True, allow_nan=False)
    json.dumps(params, sort_keys=True, allow_nan=False)

    doc = document or App.ActiveDocument
    if doc is None:
        doc = App.newDocument("CaseInsertLidPanelPreview")
    with _generation_transaction(doc, "Preview lid panel"):
        _safe_remove_group(doc)
        group = _mark_generator_object(
            doc.addObject("App::DocumentObjectGroup", PROJECT_GROUP), "project-root")
        group.Label = "Case Insert Generator — Lid Panel Preview"
        for name, label, shape, role in preview_shapes:
            obj = _add_shape(doc, group, name, label, shape, role=role)
            try:
                if role == "lid-panel-preview":
                    obj.ViewObject.ShapeColor = (0.25, 0.65, 0.90)
                    obj.ViewObject.Transparency = 35
                elif role == "lid-retainer-preview":
                    obj.ViewObject.ShapeColor = (0.95, 0.48, 0.12)
                    obj.ViewObject.Transparency = 20
                elif role == "lid-keepout-reference":
                    obj.ViewObject.ShapeColor = (0.90, 0.30, 0.20)
                    obj.ViewObject.Transparency = 30
                else:
                    obj.ViewObject.ShapeColor = (0.75, 0.75, 0.75)
            except Exception:
                pass
        _store_schema_project(doc, group, project, params, [], warnings)
        doc.recompute()
        report = {
            "document": doc.Name,
            "results": [],
            "parts": 0,
            "mode": "Lid Panel Preview",
            "valid": True,
            "solids": 0,
            "volume": 0.0,
            "warnings": warnings,
            "unplaced": [],
            "project": project,
            "printable": False,
            "height_budget": budget,
            "pattern_bounds": (
                list(preview_package["pattern_bounds"]) if preview_package else []),
        }
        result_type = getattr(model_api, "GenerationResult", None)
        return (result_type.from_mapping(report)
                if result_type and hasattr(result_type, "from_mapping") else report)


def generate_lid_panel_project(spec, document=None):
    """Generate evidenced printable inside-lid panel parts from schema v1."""
    model_api = _project_module()
    project = model_api.validate_project(spec)
    budget = model_api.lid_panel_height_budget(project)
    if not budget["printable"]:
        raise ValueError(
            "Printable lid-panel generation blocked: %s" %
            " ".join(budget["reasons"]))
    package = build_lid_panel_project(project)
    project = package["project"]
    panel = package["panel"]
    retainers = package["retainers"]
    plan = package["plan"]
    printer = project["printer"]
    splitting = project["lid_panel"]["splitting"]
    warnings = [
        "Physical fit is unverified. Dry-fit the panel, close the unloaded lid, and test retention before carrying equipment."
    ]
    if project["lid_panel"]["pattern"] == "slot_grid":
        warnings.append(
            "Modular slot grid uses only the dimensions supplied by the user."
        )
    if package["keepout_count"]:
        warnings.append(
            "%d custom lid-clearance keep-out%s removed from the panel."
            % (package["keepout_count"],
               "" if package["keepout_count"] == 1 else "s"))

    split_metadata = {
        "columns": 1, "rows": 1, "key_count": 0, "volume_loss_mm3": 0.0}
    if printer["split"]:
        if splitting["keyed_alignment"]:
            panel_parts, split_metadata = split_lid_panel_for_bed(
                panel,
                printer["bed_x"],
                printer["bed_y"],
                printer["margin"],
                splitting["key_size_mm"],
                splitting["key_clearance_mm"],
            )
        else:
            panel_parts = split_shape_for_bed(
                panel, printer["bed_x"], printer["bed_y"], printer["margin"])
            split_metadata = dict(package["split_plan"])
            split_metadata.update({"key_count": 0, "volume_loss_mm3": 0.0})
    else:
        panel_parts = [panel.copy()]
    if len(panel_parts) > 1:
        if splitting["keyed_alignment"]:
            warnings.append(
                "Oversized panel split into %d bed-sized parts with %d complementary alignment keys and clearance sockets."
                % (len(panel_parts), split_metadata["key_count"]))
        else:
            warnings.append(
                "Oversized panel split into %d bed-sized parts with straight seams; keyed alignment is disabled."
                % len(panel_parts))
    elif not printer["split"]:
        usable_x = float(printer["bed_x"]) - 2.0 * float(printer["margin"])
        usable_y = float(printer["bed_y"]) - 2.0 * float(printer["margin"])
        if panel.BoundBox.XLength > usable_x + 0.01 or panel.BoundBox.YLength > usable_y + 0.01:
            warnings.append(
                "Panel footprint %.1f x %.1f mm exceeds the usable %.1f x %.1f mm bed; enable splitting before export."
                % (panel.BoundBox.XLength, panel.BoundBox.YLength,
                   usable_x, usable_y))

    offset = App.Vector(float(plan["x_mm"]), float(plan["y_mm"]), 0.0)
    assembled_panel = panel.copy()
    assembled_panel.translate(offset)
    positioned_parts = []
    for part in panel_parts:
        positioned = part.copy()
        positioned.translate(offset)
        positioned_parts.append(positioned)
    positioned_retainers = []
    for retainer in package["retainer_shapes"]:
        positioned = retainer.copy()
        positioned.translate(offset)
        positioned_retainers.append(positioned)
    positioned_tools = None
    if package["tools"] is not None:
        positioned_tools = package["tools"].copy()
        positioned_tools.translate(offset)

    project["warnings"] = warnings
    project["unplaced"] = []
    project["lid_panel_report"] = {
        **budget,
        "pattern_count": package["pattern_count"],
        "keepout_count": package["keepout_count"],
        "fastener_hole_count": package["fastener_hole_count"],
        "retainer_count": package["retainer_count"],
        "split": split_metadata,
        "panel_plan": plan,
    }
    project.setdefault("verification", {}).update({
        "physical_fit": False,
        "status": "synthetic-or-user-evidenced geometry; physical-fit unverified",
    })
    params = _project_case_params(project)
    params.update({
        "insert_type": "Lid Panel",
        "panel_thickness": project["lid_panel"]["thickness_mm"],
        "payload_thickness": project["lid_panel"]["payload_thickness_mm"],
    })
    json.dumps(project, sort_keys=True, allow_nan=False)
    json.dumps(params, sort_keys=True, allow_nan=False)

    doc = document or App.ActiveDocument
    if doc is None:
        doc = App.newDocument("CaseInsertLidPanel")
    with _generation_transaction(doc, "Generate lid panel"):
        _safe_remove_group(doc)
        group = _mark_generator_object(
            doc.addObject("App::DocumentObjectGroup", PROJECT_GROUP), "project-root")
        group.Label = "Case Insert Generator — Inside-lid Panel"
        lid_length = float(project["lid"]["length_mm"])
        lid_width = float(project["lid"]["width_mm"])
        lid_reference = _add_shape(
            doc, group, "LidEnvelopeReference", "Evidenced lid-panel envelope",
            Part.makePlane(lid_length, lid_width), role="lid-envelope-reference")
        lid_reference.addProperty("App::PropertyString", "Evidence", "Clearance")
        lid_reference.Evidence = "%s lid envelope; physical fit unverified" % (
            project["lid"]["envelope_source"])
        if positioned_tools is not None:
            tools_obj = _add_shape(
                doc, group, "LidPanelTools",
                "Pattern, mounting, lift, and keep-out tools",
                positioned_tools, False, role="construction-tool")
            tools_obj.addProperty("App::PropertyInteger", "PatternCount", "Panel")
            tools_obj.PatternCount = int(package["pattern_count"])

        print_objects = []
        if len(positioned_parts) == 1:
            print_objects.append(_add_shape(
                doc, group, "LidPanel", "Inside-lid equipment panel",
                positioned_parts[0]))
        else:
            assembly = _add_shape(
                doc, group, "LidPanelAssemblyReference",
                "Inside-lid panel (uncut assembly reference)",
                assembled_panel, False, role="assembly-reference")
            assembly.addProperty("App::PropertyString", "Note", "Panel")
            assembly.Note = "Non-exported reference; numbered keyed parts are the printable outputs"
            for index, part in enumerate(positioned_parts, 1):
                print_objects.append(_add_shape(
                    doc, group, "LidPanelPart%02d" % index,
                    "Inside-lid panel — keyed part %02d" % index, part))
        for index, retainer in enumerate(positioned_retainers, 1):
            print_objects.append(_add_shape(
                doc, group, "LidPanelRetainer%02d" % index,
                "Printable perimeter quarter-turn retainer %02d" % index,
                retainer))
        for obj in print_objects:
            obj.addProperty("App::PropertyString", "PhysicalFit", "Verification")
            obj.PhysicalFit = "unverified"
            obj.addProperty("App::PropertyString", "PanelPattern", "Panel")
            obj.PanelPattern = str(project["lid_panel"]["pattern"])

        _add_clearance_references(doc, group, _project_case_params(project),
                                  lid_length, lid_width, 0.0)
        result_names = [item.Name for item in print_objects]
        project["result"] = result_names[0] if result_names else ""
        project["results"] = result_names
        project["parts"] = len(result_names)
        _store_schema_project(doc, group, project, params, result_names, warnings)
        doc.recompute()
        report = {
            "document": doc.Name,
            "results": result_names,
            "parts": len(result_names),
            "mode": "Lid Panel",
            "valid": all(item.Shape.isValid() for item in print_objects),
            "solids": sum(len(item.Shape.Solids) for item in print_objects),
            "volume": sum(item.Shape.Volume for item in print_objects),
            "warnings": warnings,
            "unplaced": [],
            "project": project,
            "printable": True,
            "height_budget": budget,
            "panel_plan": plan,
            "pattern_bounds": package["pattern_bounds"],
            "split": split_metadata,
        }
        result_type = getattr(model_api, "GenerationResult", None)
        return (result_type.from_mapping(report)
                if result_type and hasattr(result_type, "from_mapping") else report)


def load_project(document=None):
    """Load and validate the editable schema-v1 project stored in an FCStd."""
    doc = document or App.ActiveDocument
    if not doc:
        raise RuntimeError("No active FreeCAD document")
    root = _find_project_group(doc)
    params_obj = _find_parameter_object(doc, require_project=True)
    raw = (getattr(root, "ProjectJSON", "") if root else "") or (
        getattr(params_obj, "ProjectJSON", "") if params_obj else "")
    if not raw:
        raise RuntimeError(
            "The active document does not contain an editable Case Insert project")
    try:
        stored = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("Stored Case Insert project JSON is invalid: %s" % exc)
    normalized = _project_module().validate_project(stored)
    for key in ("layout_strategy", "unplaced"):
        if key in stored:
            normalized[key] = json.loads(json.dumps(stored[key]))
    return normalized


def generate_insert(params, document=None):
    """Generate or replace one insert. Returns a compact verification report."""
    params = _resolved_params(params)
    mode = str(params.get("insert_type", "SVG Cutout"))
    model = _case_model(params)
    if mode == "Lid Panel":
        envelope_source = str(
            params.get("lid_envelope_source", "unknown")).strip().lower()
        clearance_source, clearance = _lid_clearance(params)
        if envelope_source not in ("measured", "cad-derived"):
            raise ValueError(
                "Printable lid-panel generation blocked: lid-envelope evidence is Unknown")
        if clearance_source not in ("measured", "cad-derived") or clearance is None:
            raise ValueError(
                "Printable lid-panel generation blocked: closed-lid clearance is Unknown")
        required_height = (
            _as_float(params, "panel_thickness", MIN_WALL) +
            _as_float(params, "payload_thickness", 0.0))
        if required_height > clearance + 0.000001:
            raise ValueError(
                "Printable lid-panel generation blocked: panel and payload need "
                "%.2f mm but the evidenced clearance is %.2f mm" %
                (required_height, clearance))
    lid_dimensions = (_lid_panel_dimensions(params)
                      if mode == "Lid Panel" else None)
    doc = document or App.ActiveDocument
    if mode == "Lid Panel":
        length, width = lid_dimensions
        depth = _as_float(params, "panel_thickness", MIN_WALL)
        radius = min(_as_float(params, "panel_corner_radius", 0.0),
                     length / 2.0, width / 2.0)
        reference_shape = _make_case_reference(length, width, depth, radius)
    else:
        length, width, depth, radius = _effective_case(params)
        reference_shape = (_case_blank(params) if model else
                           _make_case_reference(length, width, depth, radius))
    if mode == "Lid Panel":
        rim_z = depth
    elif model:
        rim_z = (float(model.get("bottom_depth") or model["internal_depth"]) -
                 _as_float(params, "bottom_clearance", 0.0))
    else:
        rim_z = depth

    cutter = None
    open_count = 0
    if mode == "Case Blank":
        result = reference_shape.copy()
        result_name = "CaseBlank"
        result_label = "Editable Case Blank (Interior Negative)"
    elif mode == "SVG Cutout":
        result, cutter, open_count, used = build_svg_insert(params)
        result_name, result_label = "InsertBody", "Insert Body"
    elif mode == "Dividers":
        result, used = build_divider_insert(params)
        result_name, result_label = "DividerWalls", "Divider Tray"
    elif mode == "Lid Panel":
        result, cutter, slot_count, hole_count, used = build_lid_panel(params)
        result_name, result_label = "LidPanel", "Lid Panel"
    else:
        raise ValueError("Unknown insert type: %s" % mode)
    _valid_solid(result, result_label)
    bbox = result.BoundBox
    bed_x = float(params.get("bed_x", DEFAULT_BED))
    bed_y = float(params.get("bed_y", DEFAULT_BED))
    bed_margin = float(params.get("bed_margin", 5.0))
    usable_x, usable_y = bed_x - 2.0 * bed_margin, bed_y - 2.0 * bed_margin
    if min(usable_x, usable_y) <= 0:
        raise ValueError("Printer-bed margin leaves no printable area")
    split_shapes = ([result.copy()] if not bool(params.get("split_for_bed", False))
                    else split_shape_for_bed(result, bed_x, bed_y, bed_margin))

    # Shape building and bed checks complete before the previous generated
    # group is replaced, so invalid input preserves the last good model.
    json.dumps(params, sort_keys=True, allow_nan=False)
    if doc is None:
        doc = App.newDocument("CaseInsert")
    with _generation_transaction(doc, "Generate case insert"):
        _safe_remove_group(doc)
        group = _mark_generator_object(
            doc.addObject("App::DocumentObjectGroup", PROJECT_GROUP), "project-root")
        group.Label = "Case Insert Generator"
        # Other modes keep the usable case envelope as a hidden comparison object.
        # Case Blank exposes that exact same solid directly, so avoid duplicating
        # the envelope BRep in the saved FreeCAD document.
        if mode != "Case Blank":
            reference = _add_shape(doc, group, "CaseReference", "Case Reference",
                                   reference_shape, False)
            reference.addProperty("App::PropertyString", "Note", "Reference")
            if mode == "Lid Panel":
                reference.Note = (
                    "Verified preset or user-measured lid-panel envelope; hidden by default")
            else:
                reference.Note = (
                    "Measured custom or synthetic preset envelope after configured "
                    "clearances; hidden by default")
        warnings = []
        _add_clearance_references(doc, group, params, length, width, rim_z)
        _lid_source, lid_clearance = _lid_clearance(params)
        if lid_clearance is None and mode != "Lid Panel":
            warnings.append(
                "Closed-lid clearance is unknown. The orange case-rim plane is "
                "shown, but no space above it is treated as usable.")
        if mode == "Case Blank":
            warnings.append(
                "Editable blank created as the positive solid of the usable case "
                "interior (the case negative) after configured clearances. Add your "
                "own pockets, walls, or other features before treating it as a "
                "finished printable insert.")
        if mode == "Lid Panel":
            if model:
                warnings.append(
                    "Verified lid-panel envelope applied. Mounting bosses and hole "
                    "positions are not assumed; measure them before printing.")
            else:
                warnings.append(
                    "Using the custom measured lid-panel envelope. Verify the "
                    "physical lid and mounting points before printing.")
        elif model:
            verification = model.get("_verification", {})
            warnings.append(
                "%s applied. Bundled presets are synthetic demonstrations, not fit "
                "claims; replace them with physical measurements before printing." %
                str(verification.get("label") or "Stored preset geometry"))
        if mode == "Case Blank":
            result_obj = _add_shape(
                doc, group, result_name, result_label, result)
            result_obj.addProperty("App::PropertyString", "Usage", "Generator")
            result_obj.Usage = (
                "Usable case-interior solid after fit clearances. Edit it with "
                "FreeCAD Part or Part Design tools, or use it to crop custom geometry.")
        elif mode == "SVG Cutout":
            _add_shape(doc, group, "SVGCutout", "SVG Cutout Tool", cutter, False)
            result_obj = _add_shape(
                doc, group, result_name, result_label, result)
            if open_count:
                warnings.append("Ignored %d unsupported open SVG path(s)." % open_count)
        elif mode == "Dividers":
            result_obj = _add_shape(
                doc, group, result_name, result_label, result)
        elif mode == "Lid Panel":
            _add_shape(doc, group, "MountingSlots", "Mounting Slots and Holes", cutter, False)
            result_obj = _add_shape(
                doc, group, result_name, result_label, result)
        print_objects = [result_obj]
        if bool(params.get("split_for_bed", False)):
            if len(split_shapes) > 1:
                try:
                    result_obj.ViewObject.Visibility = False
                except Exception:
                    pass
                result_obj.Label += " (uncut assembly reference)"
                print_objects = []
                for index, part_shape in enumerate(split_shapes, 1):
                    print_objects.append(_add_shape(
                        doc, group, "PrintPart%02d" % index,
                        "Print Part %02d" % index, part_shape))
                warnings.append("Split into %d bed-sized parts with straight butt seams; exported files are numbered. Join the printed sections after printing." %
                                len(print_objects))
        else:
            if bbox.XLength > usable_x + 0.01 or bbox.YLength > usable_y + 0.01:
                warnings.append("Footprint %.1f x %.1f mm exceeds the usable %.1f x %.1f mm printer area. Enable 'Split into bed-sized parts when needed'." %
                                (bbox.XLength, bbox.YLength, usable_x, usable_y))
        params_obj = _add_parameter_object(
            doc, group, params, [item.Name for item in print_objects])
        params_obj.addProperty("App::PropertyStringList", "Warnings", "Generator")
        params_obj.Warnings = warnings
        doc.recompute()
        return {
            "document": doc.Name,
            "result": print_objects[0].Name,
            "results": [item.Name for item in print_objects],
            "parts": len(print_objects),
            "mode": mode,
            "valid": all(item.Shape.isValid() for item in print_objects),
            "solids": sum(len(item.Shape.Solids) for item in print_objects),
            "volume": result_obj.Shape.Volume,
            "bounds": [bbox.XLength, bbox.YLength, bbox.ZLength],
            "warnings": warnings,
        }


def _resolve_export_names(available_names, selected_names=None):
    """Resolve an optional part selection in stable generation order.

    ``None`` is the compatibility default and means every generated part.
    Passing an empty selection is an explicit user error rather than silently
    exporting the whole project.
    """
    available = []
    for raw_name in available_names:
        name = "" if raw_name is None else str(raw_name).strip()
        if name and name not in available:
            available.append(name)
    if selected_names is None:
        return available
    if isinstance(selected_names, str):
        requested = [selected_names.strip()] if selected_names.strip() else []
    else:
        requested = []
        for raw_name in selected_names:
            name = "" if raw_name is None else str(raw_name).strip()
            if name:
                requested.append(name)
    if not requested:
        raise ValueError("Select at least one generated part to export.")
    unknown = sorted(set(requested) - set(available))
    if unknown:
        raise ValueError(
            "Selected export part is no longer available: %s. "
            "Refresh the generated-parts list and try again." %
            ", ".join(unknown))
    requested_set = set(requested)
    return [name for name in available if name in requested_set]


def active_results(doc=None, selected_names=None):
    doc = doc or App.ActiveDocument
    if not doc:
        raise RuntimeError("No active FreeCAD document")
    params = _find_parameter_object(doc)
    if not params:
        raise RuntimeError("Generate an insert first")
    names = list(getattr(params, "GeneratedResults", []) or [])
    if not names:
        names = [params.GeneratedResult]
    names = _resolve_export_names(names, selected_names)
    objects = []
    for name in names:
        obj = doc.getObject(name)
        if not obj or obj.Shape.isNull():
            raise RuntimeError("Generated result is missing: %s" % name)
        objects.append(obj)
    return objects


def active_result(doc=None):
    return active_results(doc)[0]


def _numbered_export_paths(path, count):
    if count < 1:
        raise RuntimeError("No printable generated parts are available to export.")
    if count == 1:
        return [path]
    root, extension = os.path.splitext(path)
    return ["%s_part_%02d%s" % (root, index, extension)
            for index in range(1, count + 1)]


def export_paths(path, doc=None, selected_names=None):
    """Return the actual destinations so callers can confirm every overwrite."""
    objects = active_results(doc, selected_names=selected_names)
    return _numbered_export_paths(os.fspath(path), len(objects))


def _check_export_destinations(paths, overwrite):
    for path in paths:
        if os.path.isdir(path):
            raise IsADirectoryError("Export destination is a directory: %s" % path)
    collisions = [path for path in paths if os.path.lexists(path)]
    if collisions and not overwrite:
        raise FileExistsError(
            "Export files already exist; confirm replacement of these files: %s" %
            ", ".join(collisions))


def _write_export_batch(objects, paths, write_part, overwrite=False):
    """Stage a complete export, restoring prior files if replacement fails."""
    _check_export_destinations(paths, overwrite)
    parent = os.path.dirname(os.path.abspath(paths[0]))
    staging = tempfile.mkdtemp(prefix=".caseinsert-export-", dir=parent)
    keep_recovery = False
    backups = {}
    replaced = []
    try:
        staged = []
        for obj, output in zip(objects, paths):
            candidate = os.path.join(staging, os.path.basename(output))
            write_part(obj, candidate)
            if not os.path.isfile(candidate) or os.path.getsize(candidate) == 0:
                raise RuntimeError("Export produced no file for %s" % output)
            staged.append(candidate)

        # Recheck after expensive meshing: a destination may have appeared
        # while the export was being prepared.
        _check_export_destinations(paths, overwrite)
        backup_dir = os.path.join(staging, "previous")
        os.mkdir(backup_dir)
        for output in paths:
            if os.path.lexists(output):
                backup = os.path.join(backup_dir, os.path.basename(output))
                shutil.copy2(output, backup, follow_symlinks=False)
                backups[output] = backup
        try:
            for candidate, output in zip(staged, paths):
                os.replace(candidate, output)
                replaced.append(output)
        except BaseException as export_error:
            recovery_errors = []
            for output in reversed(replaced):
                try:
                    if output in backups:
                        os.replace(backups[output], output)
                    else:
                        os.unlink(output)
                except OSError as recovery_error:
                    recovery_errors.append(str(recovery_error))
            if recovery_errors:
                keep_recovery = True
                raise RuntimeError(
                    "Export replacement failed and some files could not be restored. "
                    "Recovery copies are in %s: %s" %
                    (backup_dir, "; ".join(recovery_errors))) from export_error
            raise
    finally:
        if not keep_recovery:
            shutil.rmtree(staging)
    return paths[0] if len(paths) == 1 else paths


def _shape_at_origin(shape):
    placed = shape.copy()
    bbox = placed.BoundBox
    placed.translate(App.Vector(-bbox.XMin, -bbox.YMin, -bbox.ZMin))
    return placed


def export_stl(path, doc=None, selected_names=None, overwrite=False):
    """Export selected parts; existing files require explicit overwrite=True."""
    import Mesh
    import MeshPart
    objects = active_results(doc, selected_names=selected_names)
    paths = _numbered_export_paths(os.fspath(path), len(objects))

    def write_part(obj, output):
        shape = _shape_at_origin(obj.Shape)
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.15,
                                      AngularDeflection=math.radians(15.0), Relative=False)
        # Stepped CAD contours can tessellate an otherwise valid tangency as a
        # microscopic sliver.  Collapse only sub-nanometre-square facets, and
        # keep the cleaned copy only when it remains a closed solid.
        degenerate = []
        for index, facet in enumerate(mesh.Facets):
            points = [App.Vector(point[0], point[1], point[2])
                      for point in facet.Points]
            area = 0.5 * (points[1] - points[0]).cross(
                points[2] - points[0]).Length
            if area <= 0.000000001:
                degenerate.append(index)
        if degenerate:
            cleaned = Mesh.Mesh(mesh)
            cleaned.collapseFacets(degenerate)
            cleaned.removeDuplicatedPoints()
            cleaned.removeDuplicatedFacets()
            cleaned.harmonizeNormals()
            if cleaned.isSolid():
                mesh = cleaned
        if not mesh.isSolid():
            # OCC may tessellate two coincident BRep seam vertices a few
            # millionths of a millimetre apart. Rebuild from the finest
            # practical weld grid first; coarser grids are attempted only
            # when the prior copy remains open, and never beyond 0.0001 mm.
            welded = None
            for digits in (7, 6, 5, 4):
                facets = []
                for facet in mesh.Facets:
                    facets.append([
                        App.Vector(round(point[0], digits),
                                   round(point[1], digits),
                                   round(point[2], digits))
                        for point in facet.Points])
                candidate = Mesh.Mesh(facets)
                candidate.removeDuplicatedPoints()
                candidate.removeDuplicatedFacets()
                candidate.harmonizeNormals()
                if candidate.isSolid():
                    welded = candidate
                    break
            if welded is None:
                raise RuntimeError("STL tessellation is not a closed solid")
            mesh = welded
        mesh.write(output)
    return _write_export_batch(objects, paths, write_part, overwrite=overwrite)


def export_step(path, doc=None, selected_names=None, overwrite=False):
    """Export selected parts; existing files require explicit overwrite=True."""
    objects = active_results(doc, selected_names=selected_names)
    paths = _numbered_export_paths(os.fspath(path), len(objects))

    def write_part(obj, output):
        _shape_at_origin(obj.Shape).exportStep(output)

    return _write_export_batch(objects, paths, write_part, overwrite=overwrite)


def save_fcstd(path, doc=None):
    doc = doc or App.ActiveDocument
    if not doc:
        raise RuntimeError("No active FreeCAD document")
    doc.recompute()
    doc.saveAs(path)
    return path


def _qt_modules():
    try:
        from PySide import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _overlay_edited_controls(base, initial, current):
    """Keep stored precision and noneditable fields until a control changes."""
    if isinstance(base, dict) and isinstance(current, dict):
        result = json.loads(json.dumps(base))
        initial = initial if isinstance(initial, dict) else {}
        for key, value in current.items():
            if key in base and key in initial:
                result[key] = _overlay_edited_controls(
                    base[key], initial[key], value)
            else:
                result[key] = json.loads(json.dumps(value))
        return result
    if current == initial:
        return json.loads(json.dumps(base))
    # Objects are identified by their stable IDs, not their list positions.
    if (isinstance(base, list) and isinstance(initial, list) and
            isinstance(current, list) and
            all(isinstance(item, dict) and "id" in item
                for items in (base, initial, current) for item in items)):
        original = {item["id"]: item for item in initial}
        stored = {item["id"]: item for item in base}
        return [_overlay_edited_controls(
            stored.get(item["id"], {}), original.get(item["id"], {}), item)
            for item in current]
    return json.loads(json.dumps(current))


class BaySizeEditor(object):
    """Scrollable editor for ordered locked or flexible compartment sizes."""

    LOCKED = 0
    FLEXIBLE = 1

    def __init__(self, QtWidgets, title, item_name, defaults):
        self.QtWidgets = QtWidgets
        self.item_name = item_name
        self.entries = []
        self.changed = None
        self.widget = QtWidgets.QGroupBox(title)
        outer = QtWidgets.QVBoxLayout(self.widget)
        note = QtWidgets.QLabel(
            "Locked keeps an exact clear size. Flexible bays automatically "
            "share the space left over.")
        note.setWordWrap(True)
        outer.addWidget(note)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(100)
        self.scroll.setMaximumHeight(145)
        self.container = QtWidgets.QWidget()
        self.entry_layout = QtWidgets.QVBoxLayout(self.container)
        self.entry_layout.setContentsMargins(4, 4, 4, 4)
        self.entry_layout.setSpacing(4)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)

        self.add_button = QtWidgets.QPushButton("+ Add %s" % item_name.lower())
        self.add_button.setToolTip(
            "Adds another compartment. New compartments share the remaining space by default.")
        self.add_button.clicked.connect(
            lambda _checked=False: self.add_bay(50.0, True))
        outer.addWidget(self.add_button)
        self.set_bays(defaults)

    def add_bay(self, value=50.0, flexible=False):
        QW = self.QtWidgets
        row_widget = QW.QWidget()
        row = QW.QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        number = QW.QLabel()
        number.setMinimumWidth(68)
        mode = QW.QComboBox()
        mode.addItems(["Locked (exact)", "Flexible (auto)"])
        mode.setToolTip(
            "Locked stays fixed. Flexible is recalculated when the case or other bays change.")
        size = QW.QDoubleSpinBox()
        size.setDecimals(2)
        size.setRange(1.0, 2000.0)
        size.setSuffix(" mm")
        size.setValue(max(1.0, float(value)))
        size.setMinimumWidth(105)
        remove = QW.QPushButton("Remove")
        remove.setToolTip("Remove this %s" % self.item_name.lower())
        mode.currentIndexChanged.connect(
            lambda index, control=size: control.setEnabled(index == self.LOCKED))
        mode.setCurrentIndex(self.FLEXIBLE if flexible else self.LOCKED)
        size.setEnabled(not flexible)
        mode.currentIndexChanged.connect(self._notify_changed)
        size.valueChanged.connect(self._notify_changed)
        remove.clicked.connect(
            lambda _checked=False, target=row_widget: self.remove_bay(target))
        row.addWidget(number)
        row.addWidget(mode, 1)
        row.addWidget(size)
        row.addWidget(remove)
        entry = {"widget": row_widget, "number": number, "mode": mode,
                 "size": size, "remove": remove}
        self.entries.append(entry)
        self.entry_layout.addWidget(row_widget)
        self._renumber()
        self._notify_changed()
        return entry

    def _notify_changed(self, *_args):
        if self.changed is not None:
            self.changed()

    def remove_bay(self, row_widget):
        if len(self.entries) <= 1:
            return
        for index, entry in enumerate(self.entries):
            if entry["widget"] is row_widget:
                self.entries.pop(index)
                self.entry_layout.removeWidget(row_widget)
                row_widget.deleteLater()
                break
        self._renumber()
        self._notify_changed()

    def _renumber(self):
        for index, entry in enumerate(self.entries, 1):
            entry["number"].setText("%s %d" % (self.item_name, index))
            entry["remove"].setEnabled(len(self.entries) > 1)

    def set_bays(self, defaults):
        for entry in self.entries:
            self.entry_layout.removeWidget(entry["widget"])
            entry["widget"].deleteLater()
        self.entries = []
        for value, flexible in defaults:
            self.add_bay(value, flexible)
        if not self.entries:
            self.add_bay(50.0, True)

    def schedule_text(self):
        tokens = []
        for entry in self.entries:
            if entry["mode"].currentIndex() == self.FLEXIBLE:
                tokens.append("*")
            else:
                value = ("%.3f" % entry["size"].value()).rstrip("0").rstrip(".")
                tokens.append(value)
        return ", ".join(tokens)

    def setEnabled(self, enabled):
        self.widget.setEnabled(enabled)


class ProjectCanvas(object):
    """Small top-down Qt scene; 3D BReps are generated only on demand."""

    COLORS = {
        "svg_pocket": (210, 124, 86),
        "circular_pocket": (90, 160, 220),
        "rectangular_pocket": (90, 160, 220),
        "removable_bin": (105, 185, 135),
        "existing_container_bay": (180, 145, 225),
        "divider_region": (225, 175, 75),
    }

    def __init__(self, QtCore, QtGui, QtWidgets, selection_changed=None):
        self.QtCore, self.QtGui, self.QtWidgets = QtCore, QtGui, QtWidgets
        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setMinimumSize(430, 300)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.view.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.items = {}
        self.objects = {}
        self.case_item = None
        self.safe_item = None
        self._selection_changed = selection_changed
        self.scene.selectionChanged.connect(self._on_selection_changed)

    def set_case(self, width, length, inset=0.0):
        width, length = max(1.0, float(width)), max(1.0, float(length))
        if self.case_item:
            self.scene.removeItem(self.case_item)
        if self.safe_item:
            self.scene.removeItem(self.safe_item)
        pen = self.QtGui.QPen(self.QtGui.QColor(235, 235, 235))
        pen.setWidthF(1.2)
        brush = self.QtGui.QBrush(self.QtGui.QColor(40, 43, 48))
        self.case_item = self.scene.addRect(0.0, 0.0, width, length, pen, brush)
        self.case_item.setZValue(-100.0)
        inset = max(0.0, min(float(inset), min(width, length) / 2.0 - 0.01))
        safe_pen = self.QtGui.QPen(self.QtGui.QColor(245, 170, 65))
        safe_pen.setStyle(self.QtCore.Qt.DashLine)
        safe_pen.setWidthF(1.0)
        self.safe_item = self.scene.addRect(
            inset, inset, width - 2.0 * inset, length - 2.0 * inset,
            safe_pen, self.QtGui.QBrush(self.QtCore.Qt.NoBrush))
        self.safe_item.setToolTip(
            "Conservative placement area inside the full case contour")
        self.safe_item.setZValue(-90.0)
        self.scene.setSceneRect(-8.0, -8.0, width + 16.0, length + 16.0)
        self.view.fitInView(self.scene.sceneRect(), self.QtCore.Qt.KeepAspectRatio)

    def _make_item(self, obj):
        object_type = obj["type"]
        if object_type == "circular_pocket":
            diameter = float(obj.get("diameter", 30.0))
            item = self.QtWidgets.QGraphicsEllipseItem(0.0, 0.0, diameter, diameter)
        else:
            item = self.QtWidgets.QGraphicsRectItem(
                0.0, 0.0, float(obj.get("length", 45.0)),
                float(obj.get("width", 35.0)))
        colour = self.QtGui.QColor(*self.COLORS.get(object_type, (150, 150, 150)))
        item.setBrush(self.QtGui.QBrush(colour))
        self._style_item(item, obj, colour)
        flags = (self.QtWidgets.QGraphicsItem.ItemIsSelectable |
                 self.QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        if not obj.get("locked", False):
            flags |= self.QtWidgets.QGraphicsItem.ItemIsMovable
        item.setFlags(flags)
        item.setData(0, obj["id"])
        self._place_item(item, obj)
        label = self.QtWidgets.QGraphicsSimpleTextItem(
            self._display_name(obj), item)
        label_font = label.font()
        label_font.setPointSizeF(3.5)
        label.setFont(label_font)
        label.setBrush(self.QtGui.QBrush(self.QtGui.QColor(20, 20, 20)))
        label.setPos(3.0, 2.0)
        self.scene.addItem(item)
        self.items[obj["id"]] = item
        self.objects[obj["id"]] = dict(obj)
        return item

    def _rotated_local_bounds(self, item, rotation=None):
        transform = self.QtGui.QTransform()
        transform.rotate(float(item.rotation() if rotation is None else rotation))
        # Use the authored geometry, not boundingRect(), because Qt expands
        # boundingRect() by half the display pen width. Project x/y are CAD
        # footprint coordinates and must not drift when styling changes.
        return transform.mapRect(item.rect())

    def _place_item(self, item, obj):
        """Keep object x/y at the rotated AABB lower-left shown in the canvas."""
        rotation = float(obj.get("rotation", 0.0)) % 360.0
        item.setTransformOriginPoint(0.0, 0.0)
        item.setRotation(rotation)
        bounds = self._rotated_local_bounds(item, rotation)
        item.setPos(float(obj.get("x", 0.0)) - bounds.left(),
                    float(obj.get("y", 0.0)) - bounds.top())

    def _display_name(self, obj):
        name = str(obj.get("name") or obj["id"])
        return name + (" [upper]" if obj.get("layer") == "upper" else "")

    def _style_item(self, item, obj, colour=None):
        colour = colour or self.QtGui.QColor(
            *self.COLORS.get(obj.get("type"), (150, 150, 150)))
        pen = self.QtGui.QPen(colour.lighter(145), 0.8)
        upper = obj.get("layer") == "upper"
        if upper:
            pen.setStyle(self.QtCore.Qt.DashLine)
            pen.setWidthF(1.6)
        item.setPen(pen)
        item.setOpacity(0.62 if upper else 0.82)
        item.setToolTip(
            "%s — %s layer" % (str(obj.get("name") or obj.get("id")),
                                  "upper" if upper else "lower"))

    def add_object(self, obj):
        object_id = str(obj["id"])
        if object_id in self.items:
            raise ValueError("Object ID already exists: %s" % object_id)
        item = self._make_item(obj)
        self.scene.clearSelection()
        item.setSelected(True)
        return object_id

    def selected_id(self):
        selected = self.scene.selectedItems()
        return str(selected[0].data(0)) if selected else None

    def selected_object(self):
        object_id = self.selected_id()
        if not object_id:
            return None
        self._sync_item(object_id)
        return dict(self.objects[object_id])

    def _sync_item(self, object_id):
        item = self.items[object_id]
        obj = self.objects[object_id]
        bounds = self._rotated_local_bounds(item)
        obj["x"] = round(float(item.pos().x() + bounds.left()), 3)
        obj["y"] = round(float(item.pos().y() + bounds.top()), 3)
        obj["rotation"] = round(float(item.rotation()) % 360.0, 3)

    def update_selected(self, updates):
        object_id = self.selected_id()
        if not object_id:
            return
        obj = self.objects[object_id]
        obj.update(updates)
        item = self.items[object_id]
        item.setFlag(self.QtWidgets.QGraphicsItem.ItemIsMovable,
                     not bool(obj.get("locked", False)))
        self._style_item(item, obj)
        if obj["type"] == "circular_pocket":
            diameter = float(obj.get("diameter", 30.0))
            item.setRect(0.0, 0.0, diameter, diameter)
        else:
            item.setRect(0.0, 0.0, float(obj.get("length", 45.0)),
                         float(obj.get("width", 35.0)))
        self._place_item(item, obj)
        children = item.childItems()
        if children:
            children[0].setText(self._display_name(obj))

    def delete_selected(self):
        object_id = self.selected_id()
        if not object_id:
            return None
        self.scene.removeItem(self.items.pop(object_id))
        self.objects.pop(object_id, None)
        return object_id

    def set_objects(self, objects):
        for item in list(self.items.values()):
            self.scene.removeItem(item)
        self.items, self.objects = {}, {}
        for obj in objects:
            self._make_item(obj)

    def to_objects(self):
        for object_id in list(self.items):
            self._sync_item(object_id)
        return [dict(self.objects[key]) for key in sorted(self.objects)]

    def _on_selection_changed(self):
        if self._selection_changed:
            self._selection_changed(self.selected_object())


class CaseInsertDialog(object):
    def __init__(self):
        self.QtCore, self.QtGui, self.QtWidgets = _qt_modules()
        self.catalog = load_case_catalog()
        self.models = self.catalog["models"]
        self.dialog = self.QtWidgets.QDialog()
        self.dialog.setWindowTitle("Case Insert Generator")
        self.dialog.setMinimumSize(820, 680)
        self._object_counter = 0
        self._updating_inspector = False
        self._layout_snapshot = None
        self._layout_unplaced = []
        self._document = App.ActiveDocument
        self._document_name = self._document.Name if self._document else None
        self._source_record = self._document_record(self._document)
        self._base_project = {}
        self._initial_project_controls = {}
        self._base_legacy_params = {}
        self._initial_legacy_controls = {}
        self._generation_signature = None
        self._generated_controls_signature = None
        self._geometry_snapshot = None
        self._generated_has_parts = False
        self._load_error = None
        self._hydrating = True
        self._build_ui()
        self._load_case()
        self._load_active_project()
        self._hydrating = False
        self._refresh_export_parts()
        if self._generation_signature is not None:
            self._generated_controls_signature = self._controls_signature()
            self._geometry_snapshot = self._geometry_state(self._document)
        self._connect_export_changes()
        self._mode_changed()
        self._set_document_title()

    @staticmethod
    def _document_record(doc):
        if doc is None:
            return None
        root = _find_project_group(doc)
        params = _find_parameter_object(doc)
        return (
            str(getattr(root, "ProjectJSON", "") or ""),
            str(getattr(params, "ProjectJSON", "") or ""),
            str(getattr(params, "ParameterJSON", "") or ""),
            tuple(getattr(params, "GeneratedResults", []) or []),
            str(getattr(params, "GeneratedResult", "") or ""),
        )

    def _set_document_title(self):
        target = self._document_name or "new document"
        self.dialog.setWindowTitle("Case Insert Generator — %s" % target)

    def _bound_document(self, create=False):
        if self._load_error:
            raise RuntimeError(self._load_error)
        if self._document is None:
            if not create:
                raise RuntimeError("Generate a model in this dialog first.")
            self._document = App.newDocument("CaseInsertGenerator")
            self._document_name = self._document.Name
            self._source_record = self._document_record(self._document)
            self._set_document_title()
        elif App.listDocuments().get(self._document_name) is not self._document:
            raise RuntimeError(
                "The document for this dialog was closed. Reopen the generator "
                "on the document you want to edit.")
        if self._document_record(self._document) != self._source_record:
            raise RuntimeError(
                "This document's generator project changed outside this dialog. "
                "Reopen the generator to load its current settings.")
        return self._document

    @staticmethod
    def _geometry_state(doc):
        """Serialize generated geometry only at load/generation/action boundaries."""
        params = _find_parameter_object(doc)
        names = list(getattr(params, "GeneratedResults", []) or [])
        if not names and not getattr(params, "GeneratedResult", ""):
            return ()
        return tuple(
            (obj.Name,
             hashlib.sha256(obj.Shape.copy().exportBrepToString().encode("utf-8")).hexdigest(),
             tuple(obj.Placement.toMatrix().A))
            for obj in active_results(doc))

    def _assert_geometry_unchanged(self, doc):
        if (self._geometry_snapshot is not None and
                self._geometry_state(doc) != self._geometry_snapshot):
            raise RuntimeError(
                "Generated geometry changed outside this dialog. Reopen the "
                "generator to review the current document before continuing.")

    def _spin(self, value=0.0, minimum=0.0, maximum=2000.0, decimals=2, suffix=" mm"):
        widget = self.QtWidgets.QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setSuffix(suffix)
        return widget

    def _form_page(self):
        page = self.QtWidgets.QWidget()
        page.setLayout(self.QtWidgets.QFormLayout())
        return page

    def _build_ui(self):
        QW = self.QtWidgets
        root = QW.QVBoxLayout(self.dialog)
        self.workflow_tabs = QW.QTabWidget()
        case_page = QW.QWidget()
        case_page_layout = QW.QVBoxLayout(case_page)
        design_page = QW.QWidget()
        design_page_layout = QW.QVBoxLayout(design_page)
        print_page = QW.QWidget()
        print_page_layout = QW.QVBoxLayout(print_page)
        self.workflow_tabs.addTab(case_page, "1  Case + fit")
        self.workflow_tabs.addTab(design_page, "2  Insert design")
        self.workflow_tabs.addTab(print_page, "3  Print + export")
        root.addWidget(self.workflow_tabs, 1)

        case_box = QW.QGroupBox("Choose case and fit")
        case_form = QW.QFormLayout(case_box)
        self.brand_combo = QW.QComboBox()
        self.series_combo = QW.QComboBox()
        self.case_combo = QW.QComboBox()
        brands = sorted(set(model["_brand"] for model in self.models.values()))
        self.brand_combo.addItems(brands + ["Custom measurements"])
        self.brand_combo.setToolTip(
            "Choose a synthetic example group, or use measured case dimensions.")
        self.series_combo.setToolTip("Choose a synthetic example family.")
        self.case_combo.setToolTip("Choose a synthetic example envelope.")
        self._refresh_series(load_case=False)
        case_form.addRow("Preset group", self.brand_combo)
        case_form.addRow("Preset family", self.series_combo)
        case_form.addRow("Preset", self.case_combo)
        self.internal_l = self._spin(300.0)
        self.internal_w = self._spin(200.0)
        self.insert_depth = self._spin(40.0, 1.0)
        self.corner_radius = self._spin(8.0)
        self.lid_length = self._spin(0.0)
        self.lid_width = self._spin(0.0)
        self.lid_length.setSpecialValueText("Not measured")
        self.lid_width.setSpecialValueText("Not measured")
        self.lid_length.setToolTip(
            "Measure the usable flat lid-panel envelope; do not reuse the case-bottom length.")
        self.lid_width.setToolTip(
            "Measure the usable flat lid-panel envelope; do not reuse the case-bottom width.")
        self.lid_envelope_source = QW.QComboBox()
        self.lid_envelope_source.addItem(
            "Unknown — configuration only", "unknown")
        self.lid_envelope_source.addItem("Measured lid envelope", "measured")
        self.lid_envelope_source.addItem("CAD-derived lid envelope", "cad-derived")
        self.lid_clearance_source = QW.QComboBox()
        self.lid_clearance_source.addItem("Unknown — do not use lid space", "unknown")
        self.lid_clearance_source.addItem("Measured lowest closed-lid ceiling", "measured")
        self.lid_clearance_source.addItem("CAD-derived lowest closed-lid ceiling", "cad-derived")
        self.lid_clearance = self._spin(0.0, 0.0)
        self.lid_clearance.setSpecialValueText("No usable lid space")
        self.lid_clearance.setEnabled(False)
        self.side_clearance = self._spin(1.0)
        self.bottom_clearance = self._spin(0.5)
        self.taper_allowance = self._spin(0.5)
        self.depth_guide = QW.QLabel()
        self.depth_guide.setWordWrap(True)
        self.verification_guide = QW.QLabel()
        self.verification_guide.setWordWrap(True)
        self.lid_availability = QW.QLabel()
        self.lid_availability.setWordWrap(True)
        for label, widget in (("Inside length", self.internal_l),
                              ("Inside width", self.internal_w),
                              ("Maximum insert depth (case bottom)", self.insert_depth),
                              ("Inside corner radius", self.corner_radius),
                              ("Lid-panel envelope evidence", self.lid_envelope_source),
                              ("Lid-panel envelope length", self.lid_length),
                              ("Lid-panel envelope width", self.lid_width),
                              ("Closed-lid clearance evidence", self.lid_clearance_source),
                              ("Usable height above case rim", self.lid_clearance),
                              ("Fit clearance on each side", self.side_clearance),
                              ("Clearance under insert", self.bottom_clearance),
                              ("Extra taper clearance", self.taper_allowance)):
            case_form.addRow(label, widget)
        self.insert_depth.setToolTip(
            "This is the bottom compartment only; lid depth is not included.")
        evidence_box = QW.QGroupBox("Evidence and limits")
        evidence_layout = QW.QVBoxLayout(evidence_box)
        for heading, guide in (
                ("Geometry confidence", self.verification_guide),
                ("Depth guide", self.depth_guide),
                ("Lid-panel availability", self.lid_availability)):
            title = QW.QLabel("<b>%s</b>" % heading)
            evidence_layout.addWidget(title)
            evidence_layout.addWidget(guide)
        case_form.addRow(evidence_box)
        case_scroll = QW.QScrollArea()
        case_scroll.setWidgetResizable(True)
        case_scroll.setFrameShape(QW.QFrame.NoFrame)
        case_scroll_body = QW.QWidget()
        case_scroll_layout = QW.QVBoxLayout(case_scroll_body)
        case_scroll_layout.setContentsMargins(0, 0, 0, 0)
        case_scroll_layout.addWidget(case_box)
        case_scroll_layout.addStretch(1)
        case_scroll.setWidget(case_scroll_body)
        case_page_layout.addWidget(case_scroll)

        print_box = QW.QGroupBox("Printer bed")
        print_form = QW.QFormLayout(print_box)
        self.bed_x = self._spin(DEFAULT_BED, 1.0)
        self.bed_y = self._spin(DEFAULT_BED, 1.0)
        self.bed_margin = self._spin(5.0, 0.0, 100.0)
        self.split_for_bed = QW.QCheckBox("Split into bed-sized parts when needed")
        self.split_for_bed.setToolTip(
            "Makes numbered bed-sized parts. Inside-lid panels use the keyed "
            "alignment setting under Advanced; other insert parts use straight seams.")
        print_form.addRow("Bed width (X)", self.bed_x)
        print_form.addRow("Bed depth (Y)", self.bed_y)
        print_form.addRow("Bed edge margin (each side)", self.bed_margin)
        print_form.addRow(self.split_for_bed)
        print_page_layout.addWidget(print_box)

        parts_box = QW.QGroupBox("Parts to export")
        parts_layout = QW.QVBoxLayout(parts_box)
        parts_note = QW.QLabel(
            "All generated parts are selected by default. Uncheck anything "
            "you do not want in the next STL or STEP export. Saving FCStd "
            "always keeps the complete editable project.")
        parts_note.setWordWrap(True)
        parts_layout.addWidget(parts_note)
        self.export_parts = QW.QListWidget()
        self.export_parts.setSelectionMode(QW.QAbstractItemView.NoSelection)
        self.export_parts.setMinimumHeight(120)
        self.export_parts.setToolTip(
            "Checked parts are written by STL and STEP export.")
        parts_layout.addWidget(self.export_parts, 1)
        parts_buttons = QW.QHBoxLayout()
        select_all_parts = QW.QPushButton("Select all")
        clear_all_parts = QW.QPushButton("Clear all")
        refresh_parts = QW.QPushButton("Refresh parts")
        select_all_parts.clicked.connect(
            lambda: self._set_export_parts_checked(True))
        clear_all_parts.clicked.connect(
            lambda: self._set_export_parts_checked(False))
        refresh_parts.clicked.connect(self._refresh_export_parts)
        parts_buttons.addWidget(select_all_parts)
        parts_buttons.addWidget(clear_all_parts)
        parts_buttons.addStretch(1)
        parts_buttons.addWidget(refresh_parts)
        parts_layout.addLayout(parts_buttons)
        self.export_part_summary = QW.QLabel("Generate a model to choose parts.")
        self.export_part_summary.setWordWrap(True)
        parts_layout.addWidget(self.export_part_summary)
        self.export_parts.itemChanged.connect(
            self._export_part_selection_changed)
        print_page_layout.addWidget(parts_box, 1)
        print_page_layout.addStretch(1)

        mode_row = QW.QHBoxLayout()
        mode_row.addWidget(QW.QLabel("What do you want to make?"))
        self.mode_combo = QW.QComboBox()
        self.mode_combo.addItems(["Custom insert composer",
                                  "Equipment insert from SVG",
                                  "Divider tray",
                                  "Lid mounting panel",
                                  "Editable case blank"])
        self.mode_combo.setToolTip(
            "Editable case blank outputs the usable case-interior solid so you "
            "can design the insert yourself in FreeCAD.")
        mode_row.addWidget(self.mode_combo, 1)
        design_page_layout.addLayout(mode_row)
        self.stack = QW.QStackedWidget()
        design_page_layout.addWidget(self.stack, 1)

        composer = QW.QWidget()
        composer_layout = QW.QVBoxLayout(composer)
        palette_row = QW.QHBoxLayout()
        self.object_type = QW.QComboBox()
        for label, value in (
                ("SVG pocket", "svg_pocket"),
                ("Circular pocket", "circular_pocket"),
                ("Rectangular pocket", "rectangular_pocket"),
                ("Removable bin / caddy", "removable_bin"),
                ("Existing-container bay", "existing_container_bay"),
                ("Bounded divider region", "divider_region")):
            self.object_type.addItem(label, value)
        add_object = QW.QPushButton("+ Add object")
        add_object.clicked.connect(self._add_composer_object)
        palette_row.addWidget(QW.QLabel("Add"))
        palette_row.addWidget(self.object_type, 1)
        palette_row.addWidget(add_object)
        composer_layout.addLayout(palette_row)

        composer_splitter = QW.QSplitter()
        composer_splitter.setOrientation(self.QtCore.Qt.Horizontal)
        object_panel = QW.QWidget()
        object_panel_layout = QW.QVBoxLayout(object_panel)
        object_panel_layout.setContentsMargins(0, 0, 0, 0)
        object_panel_layout.addWidget(QW.QLabel("Objects"))
        self.object_list = QW.QListWidget()
        self.object_list.currentItemChanged.connect(self._object_list_selected)
        object_panel_layout.addWidget(self.object_list, 1)
        object_buttons = QW.QGridLayout()
        for index, (label, callback) in enumerate((
                ("Duplicate", self._duplicate_composer_object),
                ("Delete", self._delete_composer_object),
                ("Rotate 90°", self._rotate_composer_object),
                ("Lock / unlock", self._toggle_composer_lock))):
            button = QW.QPushButton(label)
            button.clicked.connect(callback)
            object_buttons.addWidget(button, index // 2, index % 2)
        object_panel_layout.addLayout(object_buttons)
        composer_splitter.addWidget(object_panel)

        self.project_canvas = ProjectCanvas(
            self.QtCore, self.QtGui, QW, self._canvas_selection_changed)
        composer_splitter.addWidget(self.project_canvas.view)

        inspector = QW.QGroupBox("Selected object")
        inspector_form = QW.QFormLayout(inspector)
        self.object_name = QW.QLineEdit()
        self.object_x = self._spin(0.0, -2000.0)
        self.object_y = self._spin(0.0, -2000.0)
        self.object_width = self._spin(45.0, 1.0)
        self.object_length = self._spin(35.0, 1.0)
        self.object_height = self._spin(20.0, 1.0)
        self.object_clearance = self._spin(0.3, 0.0, 5.0)
        self.object_clearance.setToolTip(
            "Additional space on each side for printer and material fit.")
        self.object_finger_scoop = QW.QCheckBox("Add finger lift scoop")
        self.object_finger_scoop.setToolTip(
            "Cut a rounded access scoop at the front edge of this pocket or bay.")
        self.object_rotation = self._spin(0.0, -360.0, 360.0, 1, "°")
        self.object_layer = QW.QComboBox()
        self.object_layer.addItems(["Lower", "Upper"])
        self.object_locked = QW.QCheckBox("Keep fixed during Auto Layout")
        self.object_svg_path = QW.QLineEdit()
        self.object_svg_path.setPlaceholderText("SVG file path")
        self.object_rows = QW.QSpinBox(); self.object_rows.setRange(1, 20); self.object_rows.setValue(2)
        self.object_columns = QW.QSpinBox(); self.object_columns.setRange(1, 20); self.object_columns.setValue(2)
        for label, widget in (
                ("Name", self.object_name), ("X", self.object_x), ("Y", self.object_y),
                ("Width / diameter", self.object_width), ("Length", self.object_length),
                ("Height / pocket depth", self.object_height),
                ("Fit clearance", self.object_clearance),
                ("Rotation", self.object_rotation), ("Layer", self.object_layer),
                ("SVG source", self.object_svg_path), ("Divider rows", self.object_rows),
                ("Divider columns", self.object_columns)):
            inspector_form.addRow(label, widget)
        inspector_form.addRow(self.object_finger_scoop)
        inspector_form.addRow(self.object_locked)
        composer_splitter.addWidget(inspector)
        composer_splitter.setStretchFactor(0, 0)
        composer_splitter.setStretchFactor(1, 1)
        composer_splitter.setStretchFactor(2, 0)
        composer_layout.addWidget(composer_splitter, 1)

        options_grid = QW.QGridLayout()
        layer_box = QW.QGroupBox("Layers")
        layer_form = QW.QFormLayout(layer_box)
        self.layers_enabled = QW.QCheckBox("Use two printable layers")
        self.layer_ratio = QW.QSlider(self.QtCore.Qt.Horizontal)
        self.layer_ratio.setRange(25, 75); self.layer_ratio.setValue(50)
        self.layer_ratio.setEnabled(False)
        self.layer_ratio.setVisible(False)
        self.layer_budget = QW.QLabel("Single layer")
        layer_form.addRow(self.layers_enabled)
        layer_form.addRow("Height split", self.layer_ratio)
        self.layer_ratio_label = layer_form.labelForField(self.layer_ratio)
        self.layer_ratio_label.setVisible(False)
        self.layer_budget.setWordWrap(True)
        layer_form.addRow("Usable heights", self.layer_budget)
        options_grid.addWidget(layer_box, 0, 0)

        containment_box = QW.QGroupBox("Keep contents contained")
        containment_form = QW.QFormLayout(containment_box)
        self.containment_mode = QW.QComboBox()
        self.containment_mode.addItem("None", "none")
        self.containment_mode.addItem("Shared inner panel", "shared_panel")
        self.containment_mode.addItem("Individual bin lids", "individual_lids")
        self.retention_clearance = self._spin(0.3, 0.0, 3.0)
        self.retention_panel_thickness = self._spin(2.0, 1.2, 8.0)
        containment_form.addRow("Method", self.containment_mode)
        retention_advanced = QW.QGroupBox("Advanced retention clearances")
        retention_advanced.setCheckable(True)
        retention_advanced.setChecked(False)
        retention_advanced_body = QW.QWidget()
        retention_advanced_form = QW.QFormLayout(retention_advanced_body)
        retention_advanced_form.setContentsMargins(0, 4, 0, 0)
        retention_advanced_form.addRow("Print clearance", self.retention_clearance)
        retention_advanced_form.addRow(
            "Panel / lid thickness", self.retention_panel_thickness)
        retention_advanced_layout = QW.QVBoxLayout(retention_advanced)
        retention_advanced_layout.addWidget(retention_advanced_body)
        retention_advanced_body.setVisible(False)
        retention_advanced.toggled.connect(retention_advanced_body.setVisible)
        containment_form.addRow(retention_advanced)
        options_grid.addWidget(containment_box, 0, 1)

        layout_box = QW.QGroupBox("Auto Layout")
        layout_form = QW.QFormLayout(layout_box)
        self.layout_strategy = QW.QComboBox()
        self.layout_strategy.addItem("Balanced", "balanced")
        self.layout_strategy.addItem("Maximum capacity", "maximum_capacity")
        self.layout_strategy.addItem("Fewest layers", "fewest_layers")
        apply_layout = QW.QPushButton("Apply selected layout")
        apply_layout.clicked.connect(self._apply_auto_layout)
        self.layout_report = QW.QLabel("Manual layout")
        self.layout_report.setWordWrap(True)
        layout_form.addRow("Alternative", self.layout_strategy)
        layout_form.addRow(apply_layout)
        layout_form.addRow(self.layout_report)
        options_grid.addWidget(layout_box, 1, 0, 1, 2)
        options_grid.setColumnStretch(0, 1)
        options_grid.setColumnStretch(1, 1)
        composer_layout.addLayout(options_grid)
        self.stack.addWidget(composer)

        svg = self._form_page()
        path_row = QW.QHBoxLayout()
        self.svg_path = QW.QLineEdit(os.path.join(macro_directory(), "examples", "example_cutout.svg"))
        browse = QW.QPushButton("Browse…")
        browse.clicked.connect(self._browse_svg)
        path_row.addWidget(self.svg_path, 1)
        path_row.addWidget(browse)
        svg.layout().addRow("Cutout drawing (SVG)", path_row)
        self.svg_scale = self._spin(1.0, 0.001, 100.0, 3, "×")
        self.svg_x = self._spin(20.0, -2000.0)
        self.svg_y = self._spin(20.0, -2000.0)
        self.svg_rotation = self._spin(0.0, -360.0, 360.0, 1, "°")
        self.cutout_depth = self._spin(20.0, 0.01)
        self.svg_clearance = self._spin(0.6, 0.0)
        self.through_cut = QW.QCheckBox("Cut all the way through the insert")
        self.invert_svg = QW.QCheckBox("Cut upward from the insert bottom")
        for label, widget in (("SVG scale", self.svg_scale),
                              ("Distance from left (X)", self.svg_x),
                              ("Distance from front (Y)", self.svg_y),
                              ("Rotate SVG", self.svg_rotation),
                              ("Pocket depth", self.cutout_depth),
                              ("Extra space around cutout", self.svg_clearance)):
            svg.layout().addRow(label, widget)
        svg.layout().addRow(self.through_cut)
        svg.layout().addRow(self.invert_svg)
        self.stack.addWidget(svg)

        div = self._form_page()
        self.divider_form = div.layout()
        self.div_layout = QW.QComboBox()
        self.div_layout.addItems(["Equal grid (rows and columns)",
                                  "Rows only",
                                  "Columns only",
                                  "Measured compartment sizes"])
        self.rows = QW.QSpinBox(); self.rows.setRange(1, 50); self.rows.setValue(3)
        self.columns = QW.QSpinBox(); self.columns.setRange(1, 50); self.columns.setValue(3)
        self.column_bays = BaySizeEditor(
            QW, "Columns — left to right", "Column",
            [(55.0, False), (70.0, False), (50.0, True)])
        self.row_bays = BaySizeEditor(
            QW, "Rows — front to back", "Row",
            [(40.0, False), (50.0, True)])
        self.measured_tabs = QW.QTabWidget()
        self.measured_tabs.addTab(self.column_bays.widget, "Columns")
        self.measured_tabs.addTab(self.row_bays.widget, "Rows")
        self.measured_tabs.setToolTip(
            "Add and size compartments in either direction. Each list scrolls independently.")
        self.base_thickness = self._spin(2.4, MIN_WALL)
        self.outer_wall = self._spin(2.4, MIN_WALL)
        self.divider_wall = self._spin(1.6, MIN_WALL)
        self.divider_height = self._spin(35.0, MIN_WALL)
        for label, widget in (("Compartment layout", self.div_layout),
                              ("Compartments front to back", self.rows),
                              ("Compartments left to right", self.columns),
                              ("Floor thickness", self.base_thickness),
                              ("Outer wall thickness", self.outer_wall),
                              ("Internal divider thickness", self.divider_wall),
                              ("All wall height above floor", self.divider_height)):
            div.layout().addRow(label, widget)
        div.layout().addRow(self.measured_tabs)
        self.divider_height.setToolTip(
            "The outer wall and every internal divider use this same height. "
            "Total tray height also includes the floor thickness.")
        self.stack.addWidget(div)

        lid = QW.QWidget()
        lid_root = QW.QVBoxLayout(lid)
        lid_scroll = QW.QScrollArea()
        lid_scroll.setWidgetResizable(True)
        lid_scroll.setFrameShape(QW.QFrame.NoFrame)
        lid_body = QW.QWidget()
        lid_layout = QW.QVBoxLayout(lid_body)

        gate_box = QW.QGroupBox("Printable lid-panel gate")
        gate_layout = QW.QVBoxLayout(gate_box)
        self.lid_generation_gate = QW.QLabel()
        self.lid_generation_gate.setObjectName("lidPanelGenerationGate")
        self.lid_generation_gate.setWordWrap(True)
        gate_layout.addWidget(self.lid_generation_gate)
        lid_layout.addWidget(gate_box)

        panel_box = QW.QGroupBox("Panel and payload")
        panel_form = QW.QFormLayout(panel_box)
        self.panel_pattern = QW.QComboBox()
        self.panel_pattern.addItem("Solid equipment panel", "solid")
        self.panel_pattern.addItem(
            "Modular slot grid — user-defined dimensions",
            "slot_grid")
        self.panel_pattern.addItem("Perforated / round-hole grid", "perforated_grid")
        self.panel_thickness = self._spin(3.0, 1.2, 20.0)
        self.payload_thickness = self._spin(0.0, 0.0, 500.0)
        self.edge_inset = self._spin(4.0, 0.0, 500.0)
        self.panel_radius = self._spin(6.0, 0.0, 500.0)
        self.panel_height_budget = QW.QLabel()
        self.panel_height_budget.setWordWrap(True)
        panel_form.addRow("Panel type", self.panel_pattern)
        panel_form.addRow("Panel thickness", self.panel_thickness)
        panel_form.addRow("Payload thickness below panel", self.payload_thickness)
        panel_form.addRow("Base inset from evidenced lid envelope", self.edge_inset)
        panel_form.addRow("Panel corner radius", self.panel_radius)
        panel_form.addRow("Closed-lid height budget", self.panel_height_budget)
        lid_layout.addWidget(panel_box)

        pattern_box = QW.QGroupBox("Pattern dimensions")
        pattern_layout = QW.QVBoxLayout(pattern_box)
        self.panel_pattern_stack = QW.QStackedWidget()
        solid_note = QW.QLabel(
            "Solid panel: no organisation holes are cut. Mounting, lift access, "
            "and explicit keep-outs still apply.")
        solid_note.setWordWrap(True)
        self.panel_pattern_stack.addWidget(solid_note)
        slot_page = self._form_page()
        slot_note = QW.QLabel(
            "Set every slot and pitch dimension from your own hardware; no "
            "commercial mounting-system dimensions are assumed.")
        slot_note.setWordWrap(True)
        self.slot_width = self._spin(25.0, 1.0)
        self.slot_height = self._spin(4.0, 1.0)
        self.slot_pitch_x = self._spin(38.0, 1.0)
        self.slot_pitch_y = self._spin(25.0, 1.0)
        self.slot_margin_x = self._spin(10.0, 0.0)
        self.slot_margin_y = self._spin(10.0, 0.0)
        self.slot_orientation = QW.QComboBox()
        self.slot_orientation.addItem("Horizontal", "horizontal")
        self.slot_orientation.addItem("Vertical", "vertical")
        slot_page.layout().addRow(slot_note)
        for label, widget in (
                ("Slot length", self.slot_width),
                ("Slot width", self.slot_height),
                ("Pitch X", self.slot_pitch_x),
                ("Pitch Y", self.slot_pitch_y),
                ("Grid margin X", self.slot_margin_x),
                ("Grid margin Y", self.slot_margin_y),
                ("Slot orientation", self.slot_orientation)):
            slot_page.layout().addRow(label, widget)
        self.panel_pattern_stack.addWidget(slot_page)
        perforated_page = self._form_page()
        self.perforation_diameter = self._spin(5.0, 0.5)
        self.perforation_pitch_x = self._spin(12.0, 0.5)
        self.perforation_pitch_y = self._spin(12.0, 0.5)
        self.perforation_margin_x = self._spin(10.0, 0.0)
        self.perforation_margin_y = self._spin(10.0, 0.0)
        for label, widget in (
                ("Hole diameter", self.perforation_diameter),
                ("Pitch X", self.perforation_pitch_x),
                ("Pitch Y", self.perforation_pitch_y),
                ("Grid margin X", self.perforation_margin_x),
                ("Grid margin Y", self.perforation_margin_y)):
            perforated_page.layout().addRow(label, widget)
        self.panel_pattern_stack.addWidget(perforated_page)
        pattern_layout.addWidget(self.panel_pattern_stack)
        lid_layout.addWidget(pattern_box)

        keepout_box = QW.QGroupBox("Lid, rim, seal, and hinge keep-outs")
        keepout_form = QW.QFormLayout(keepout_box)
        self.rim_keepout = self._spin(4.0, 0.0, 500.0)
        self.seal_keepout = self._spin(2.0, 0.0, 500.0)
        self.hinge_keepout = self._spin(12.0, 0.0, 500.0)
        self.hinge_edge = QW.QComboBox()
        for label, value in (("Top", "top"), ("Bottom", "bottom"),
                             ("Left", "left"), ("Right", "right")):
            self.hinge_edge.addItem(label, value)
        self.clearance_keepout_margin = self._spin(3.0, 0.0, 500.0)
        self.custom_keepouts = QW.QLineEdit()
        self.custom_keepouts.setPlaceholderText(
            "Relative to finished panel: x,y,length,width,label")
        for label, widget in (
                ("Rim keep-out", self.rim_keepout),
                ("Seal keep-out", self.seal_keepout),
                ("Hinge keep-out", self.hinge_keepout),
                ("Hinge edge", self.hinge_edge),
                ("Pattern clearance from keep-outs", self.clearance_keepout_margin),
                ("Local clearance rectangles", self.custom_keepouts)):
            keepout_form.addRow(label, widget)
        lid_layout.addWidget(keepout_box)

        mounting_advanced = QW.QGroupBox("Advanced mounting and split controls")
        self.mounting_advanced = mounting_advanced
        mounting_advanced.setCheckable(True)
        mounting_advanced.setChecked(False)
        mounting_body = QW.QWidget()
        mounting_form = QW.QFormLayout(mounting_body)
        mounting_form.setContentsMargins(0, 4, 0, 0)
        self.perimeter_mounting = QW.QCheckBox("Use perimeter mounting points")
        self.perimeter_mounting.setChecked(True)
        self.printable_retainers = QW.QCheckBox(
            "Generate printable quarter-turn retainers")
        self.printable_retainers.setChecked(True)
        self.retainer_count = QW.QSpinBox()
        self.retainer_count.setRange(2, 8)
        self.retainer_count.setValue(4)
        self.retainer_width = self._spin(10.0, 4.0, 50.0)
        self.retainer_projection = self._spin(3.0, 1.2, 30.0)
        self.retainer_clearance = self._spin(0.35, 0.0, 3.0)
        self.panel_lift_access = QW.QCheckBox("Add lift-access notch opposite hinge")
        self.panel_lift_access.setChecked(True)
        self.lift_access_diameter = self._spin(18.0, 4.0, 100.0)
        self.fastener_holes = QW.QCheckBox("Add optional fastener holes")
        self.hole_diameter = self._spin(3.5, 0.5, 30.0)
        self.hole_edge = self._spin(12.0, 0.0, 500.0)
        self.custom_holes = QW.QLineEdit()
        self.custom_holes.setPlaceholderText("Optional x,y; x,y relative to panel")
        self.keyed_panel_split = QW.QCheckBox(
            "Use complementary keyed alignment when panel is split")
        self.keyed_panel_split.setChecked(True)
        self.split_key_size = self._spin(8.0, 2.0, 40.0)
        self.split_key_clearance = self._spin(0.25, 0.0, 3.0)
        mounting_form.addRow(self.perimeter_mounting)
        mounting_form.addRow(self.printable_retainers)
        mounting_form.addRow("Retainer count", self.retainer_count)
        mounting_form.addRow("Retainer head width", self.retainer_width)
        mounting_form.addRow("Retainer projection", self.retainer_projection)
        mounting_form.addRow("Retainer print clearance", self.retainer_clearance)
        mounting_form.addRow(self.panel_lift_access)
        mounting_form.addRow("Lift-access diameter", self.lift_access_diameter)
        mounting_form.addRow(self.fastener_holes)
        mounting_form.addRow("Fastener-hole diameter", self.hole_diameter)
        mounting_form.addRow("Default hole edge offset", self.hole_edge)
        mounting_form.addRow("Custom fastener holes", self.custom_holes)
        mounting_form.addRow(self.keyed_panel_split)
        mounting_form.addRow("Alignment key size", self.split_key_size)
        mounting_form.addRow("Alignment key clearance", self.split_key_clearance)
        mounting_advanced_layout = QW.QVBoxLayout(mounting_advanced)
        mounting_advanced_layout.addWidget(mounting_body)
        mounting_body.setVisible(False)
        mounting_advanced.toggled.connect(mounting_body.setVisible)
        lid_layout.addWidget(mounting_advanced)
        lid_layout.addStretch(1)
        lid_scroll.setWidget(lid_body)
        lid_root.addWidget(lid_scroll)
        self.stack.addWidget(lid)

        blank = self._form_page()
        blank_note = QW.QLabel(
            "Creates the positive solid of the case's usable interior—the "
            "case negative—after the selected fit clearances. Save the FreeCAD "
            "file, then cut pockets or add features with the Part or Part Design "
            "workbench. This is an editable starting solid, not a finished tray.")
        blank_note.setWordWrap(True)
        blank.layout().addRow(blank_note)
        self.stack.addWidget(blank)

        actions = QW.QGridLayout()
        buttons = [
            ("Generate / update model", self._generate), ("Fit model in view", self._fit_view),
            ("Export print files (STL)", self._export_stl), ("Export CAD files (STEP)", self._export_step),
            ("Save editable FreeCAD file", self._save_fcstd), ("Reset all settings", self._reset),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = QW.QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button, index // 2, index % 2)
            if callback == self._generate:
                self.generate_button = button
            elif callback == self._export_stl:
                self.export_stl_button = button
            elif callback == self._export_step:
                self.export_step_button = button
            elif callback == self._save_fcstd:
                self.save_fcstd_button = button
        root.addLayout(actions)
        self.status = QW.QLabel("Ready")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.brand_combo.currentIndexChanged.connect(self._brand_changed)
        self.series_combo.currentIndexChanged.connect(self._series_changed)
        self.case_combo.currentIndexChanged.connect(self._load_case)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.lid_envelope_source.currentIndexChanged.connect(
            self._update_lid_availability)
        self.lid_length.valueChanged.connect(self._update_lid_availability)
        self.lid_width.valueChanged.connect(self._update_lid_availability)
        self.lid_clearance_source.currentIndexChanged.connect(
            self._lid_clearance_source_changed)
        self.lid_clearance.valueChanged.connect(self._update_layer_budget)
        self.insert_depth.valueChanged.connect(self._update_layer_budget)
        self.bottom_clearance.valueChanged.connect(self._update_layer_budget)
        self.internal_l.valueChanged.connect(self._update_canvas_case)
        self.internal_w.valueChanged.connect(self._update_canvas_case)
        self.layers_enabled.toggled.connect(self._layers_toggled)
        self.layer_ratio.valueChanged.connect(self._update_layer_budget)
        self.containment_mode.currentIndexChanged.connect(
            self._update_layer_budget)
        self.retention_clearance.valueChanged.connect(self._update_layer_budget)
        self.retention_panel_thickness.valueChanged.connect(
            self._update_layer_budget)
        self.div_layout.currentIndexChanged.connect(self._divider_layout_changed)
        for widget in (self.object_width, self.object_length, self.object_height,
                       self.object_clearance, self.object_rotation):
            widget.valueChanged.connect(self._apply_inspector)
        self.object_x.valueChanged.connect(
            lambda _value: self._apply_inspector(position_from_controls=True))
        self.object_y.valueChanged.connect(
            lambda _value: self._apply_inspector(position_from_controls=True))
        self.object_finger_scoop.toggled.connect(self._apply_inspector)
        self.object_layer.currentIndexChanged.connect(self._apply_inspector)
        self.object_locked.toggled.connect(self._apply_inspector)
        self.object_rows.valueChanged.connect(self._apply_inspector)
        self.object_columns.valueChanged.connect(self._apply_inspector)
        self.object_name.editingFinished.connect(self._apply_inspector)
        self.object_svg_path.editingFinished.connect(self._apply_inspector)
        self.panel_pattern.currentIndexChanged.connect(
            self._panel_pattern_changed)
        for widget in (
                self.panel_thickness, self.payload_thickness, self.edge_inset,
                self.panel_radius, self.slot_width, self.slot_height,
                self.slot_pitch_x, self.slot_pitch_y, self.slot_margin_x,
                self.slot_margin_y, self.perforation_diameter,
                self.perforation_pitch_x, self.perforation_pitch_y,
                self.perforation_margin_x, self.perforation_margin_y,
                self.rim_keepout, self.seal_keepout, self.hinge_keepout,
                self.clearance_keepout_margin, self.retainer_width,
                self.retainer_projection, self.retainer_clearance,
                self.lift_access_diameter, self.hole_diameter, self.hole_edge,
                self.split_key_size, self.split_key_clearance):
            widget.valueChanged.connect(self._update_lid_generation_gate)
        for widget in (
                self.slot_orientation, self.hinge_edge):
            widget.currentIndexChanged.connect(self._update_lid_generation_gate)
        for widget in (
                self.perimeter_mounting, self.printable_retainers,
                self.panel_lift_access, self.fastener_holes,
                self.keyed_panel_split):
            widget.toggled.connect(self._update_lid_generation_gate)
        self.retainer_count.valueChanged.connect(
            self._update_lid_generation_gate)
        self.custom_holes.editingFinished.connect(
            self._update_lid_generation_gate)
        self.custom_keepouts.editingFinished.connect(
            self._update_lid_generation_gate)
        self._divider_layout_changed()
        self._panel_pattern_changed()
        self._lid_clearance_source_changed()
        self._update_canvas_case()
        self._update_layer_budget()
        self._update_lid_generation_gate()

    def _lid_clearance_source_changed(self, *_args):
        source = self.lid_clearance_source.currentData()
        known = source in ("measured", "cad-derived")
        self.lid_clearance.setEnabled(known)
        if not known:
            self.lid_clearance.setValue(0.0)
        self._update_layer_budget()
        self._update_lid_generation_gate()

    def _panel_pattern_changed(self, *_args):
        pattern = str(self.panel_pattern.currentData())
        index = {"solid": 0, "slot_grid": 1,
                 "perforated_grid": 2}.get(pattern, 0)
        self.panel_pattern_stack.setCurrentIndex(index)
        self._update_lid_generation_gate()

    def _lid_panel_settings(self):
        return {
            "enabled": True,
            "pattern": str(self.panel_pattern.currentData()),
            "thickness_mm": self.panel_thickness.value(),
            "payload_thickness_mm": self.payload_thickness.value(),
            "edge_inset_mm": self.edge_inset.value(),
            "corner_radius_mm": self.panel_radius.value(),
            "slot_grid": {
                "slot_length_mm": self.slot_width.value(),
                "slot_width_mm": self.slot_height.value(),
                "pitch_x_mm": self.slot_pitch_x.value(),
                "pitch_y_mm": self.slot_pitch_y.value(),
                "margin_x_mm": self.slot_margin_x.value(),
                "margin_y_mm": self.slot_margin_y.value(),
                "orientation": str(self.slot_orientation.currentData()),
            },
            "perforated_grid": {
                "diameter_mm": self.perforation_diameter.value(),
                "pitch_x_mm": self.perforation_pitch_x.value(),
                "pitch_y_mm": self.perforation_pitch_y.value(),
                "margin_x_mm": self.perforation_margin_x.value(),
                "margin_y_mm": self.perforation_margin_y.value(),
            },
            "keepouts": {
                "rim_mm": self.rim_keepout.value(),
                "seal_mm": self.seal_keepout.value(),
                "hinge_mm": self.hinge_keepout.value(),
                "hinge_edge": str(self.hinge_edge.currentData()),
                "clearance_margin_mm": self.clearance_keepout_margin.value(),
                "rectangles": _parse_keepout_rectangles(
                    self.custom_keepouts.text()),
            },
            "mounting": {
                "perimeter_enabled": self.perimeter_mounting.isChecked(),
                "retainers_enabled": self.printable_retainers.isChecked(),
                "retainer_count": self.retainer_count.value(),
                "retainer_width_mm": self.retainer_width.value(),
                "retainer_projection_mm": self.retainer_projection.value(),
                "retainer_clearance_mm": self.retainer_clearance.value(),
                "lift_access_enabled": self.panel_lift_access.isChecked(),
                "lift_access_diameter_mm": self.lift_access_diameter.value(),
                "fastener_holes_enabled": self.fastener_holes.isChecked(),
                "fastener_hole_diameter_mm": self.hole_diameter.value(),
                "fastener_edge_offset_mm": self.hole_edge.value(),
                "custom_fastener_holes": [
                    {"x_mm": x, "y_mm": y}
                    for x, y in _parse_hole_coordinates(
                        self.custom_holes.text())
                ],
            },
            "splitting": {
                "keyed_alignment": self.keyed_panel_split.isChecked(),
                "key_size_mm": self.split_key_size.value(),
                "key_clearance_mm": self.split_key_clearance.value(),
            },
        }

    def _update_lid_generation_gate(self, *_args):
        if not hasattr(self, "lid_generation_gate"):
            return
        printable = False
        try:
            spec = self._project_spec(lid_panel_enabled=True,
                                      compute_layout_inset=False)
            spec["objects"] = []
            budget = _project_module().lid_panel_height_budget(spec)
            printable = bool(budget["printable"])
            if printable:
                message = (
                    "Ready for printable generation: evidenced lid envelope and "
                    "closed-lid clearance are present. Required %.2f mm; %.2f mm "
                    "remains in the conservative height budget." %
                    (budget["required_height_mm"],
                     budget["remaining_clearance_mm"]))
                colour = "#236b3a"
            else:
                message = " ".join(budget["reasons"])
                colour = "#9a5a00"
            available = budget["available_clearance_mm"]
            self.panel_height_budget.setText(
                "Required %.2f mm; available %s; printable %s." %
                (budget["required_height_mm"],
                 "Unknown" if available is None else "%.2f mm" % available,
                 "yes" if printable else "no"))
        except Exception as exc:
            message = "Configuration needs correction before preview or printing: %s" % exc
            colour = "#9a2d20"
            self.panel_height_budget.setText("Height budget unavailable until the configuration is valid.")
        self.lid_generation_gate.setText(message)
        self.lid_generation_gate.setStyleSheet(
            "QLabel { color: %s; font-weight: 600; }" % colour)
        lid_mode = self.mode_combo.currentIndex() == 3
        if hasattr(self, "generate_button"):
            self.generate_button.setText(
                "Generate / update printable model" if (not lid_mode or printable)
                else "Preview configuration (print blocked)")
        self._update_export_availability()

    def _connect_export_changes(self):
        """Refresh cheap control-state checks; CAD validation happens on actions."""
        QW = self.QtWidgets
        for widget in self.dialog.findChildren(QW.QDoubleSpinBox):
            widget.valueChanged.connect(self._update_export_availability)
        for widget in self.dialog.findChildren(QW.QSpinBox):
            widget.valueChanged.connect(self._update_export_availability)
        for widget in self.dialog.findChildren(QW.QSlider):
            widget.valueChanged.connect(self._update_export_availability)
        for widget in self.dialog.findChildren(QW.QComboBox):
            widget.currentIndexChanged.connect(self._update_export_availability)
        for widget in self.dialog.findChildren(QW.QCheckBox):
            widget.toggled.connect(self._update_export_availability)
        for widget in self.dialog.findChildren(QW.QLineEdit):
            widget.textChanged.connect(self._update_export_availability)
        self.project_canvas.scene.changed.connect(self._update_export_availability)
        self.column_bays.changed = self._update_export_availability
        self.row_bays.changed = self._update_export_availability

    def _controls_signature(self):
        mode = self.mode_combo.currentIndex()
        controls = (self._project_controls() if mode in (0, 3)
                    else self._legacy_controls())
        return json.dumps([mode, controls], sort_keys=True)

    def _update_export_availability(self, *_args):
        if self._updating_inspector:
            return
        enabled = False
        reason = "Generate or save the current model before exporting."
        if not self._hydrating and not self._updating_inspector:
            try:
                self._bound_document()
                enabled = (
                    self._generated_has_parts and
                    self._generated_controls_signature == self._controls_signature())
                if enabled:
                    reason = "Export the checked parts from %s." % self._document_name
                elif self._generated_has_parts:
                    reason = "Settings changed. Generate or save the updated model before exporting."
            except Exception as exc:
                reason = str(exc)
        for name in ("export_stl_button", "export_step_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)
                button.setToolTip(reason)

    def _update_canvas_case(self, *_args):
        if not hasattr(self, "project_canvas"):
            return
        per_side = self.side_clearance.value() + self.taper_allowance.value()
        width = max(1.0, self.internal_l.value() - 2.0 * per_side)
        length = max(1.0, self.internal_w.value() - 2.0 * per_side)
        radius = max(0.0, self.corner_radius.value() - per_side)
        conservative_inset = radius * (1.0 - 1.0 / math.sqrt(2.0))
        if (hasattr(self, "layers_enabled") and
                self.layers_enabled.isChecked()):
            key_clearance = max(
                0.2, self.retention_clearance.value()
                if hasattr(self, "retention_clearance") else 0.3)
            conservative_inset += 10.5 + key_clearance
        self.project_canvas.set_case(width, length, conservative_inset)

    def _layers_toggled(self, enabled):
        self.layer_ratio.setEnabled(bool(enabled))
        self.layer_ratio.setVisible(bool(enabled))
        self.layer_ratio_label.setVisible(bool(enabled))
        self.object_layer.setEnabled(bool(enabled))
        if not enabled:
            for obj in self.project_canvas.objects.values():
                obj["layer"] = "lower"
            self.object_layer.setCurrentIndex(0)
        self._update_canvas_case()
        self._update_layer_budget()

    def _update_layer_budget(self, *_args):
        if not hasattr(self, "layer_budget"):
            return
        floor = float(self._base_project.get("layers", {}).get("floor_mm", 2.4))
        usable = max(0.0, self.insert_depth.value() - self.bottom_clearance.value())
        if str(self.containment_mode.currentData()) == "shared_panel":
            usable = max(
                0.0, usable - self.retention_panel_thickness.value() -
                self.retention_clearance.value())
        if self.layers_enabled.isChecked():
            clear_total = max(0.0, usable - 2.0 * floor)
            lower = clear_total * self.layer_ratio.value() / 100.0
            upper = clear_total - lower
            text = "Lower %.1f mm / upper %.1f mm" % (lower, upper)
        else:
            text = "Single layer: %.1f mm" % max(0.0, usable - floor)
        source = self.lid_clearance_source.currentData()
        if source in ("measured", "cad-derived"):
            text += "; verified above-rim space %.1f mm" % self.lid_clearance.value()
        else:
            text += "; above-rim space unknown"
        self.layer_budget.setText(text)

    def _next_object_id(self, object_type):
        while True:
            self._object_counter += 1
            candidate = "%s-%02d" % (
                object_type.replace("_", "-"), self._object_counter)
            if candidate not in self.project_canvas.objects:
                return candidate

    def _default_composer_object(self, object_type):
        object_id = self._next_object_id(object_type)
        label = self.object_type.currentText()
        offset = 12.0 + (self._object_counter - 1) * 6.0
        obj = {
            "id": object_id,
            "type": object_type,
            "name": "%s %d" % (label, self._object_counter),
            "x": offset,
            "y": offset,
            "rotation": 0.0,
            "layer": "lower",
            "locked": False,
            "height": 20.0,
            "floor": 2.4,
            "clearance": 0.3,
            "finger_scoop": False,
        }
        if object_type == "circular_pocket":
            obj.update({"diameter": 32.0, "width": 32.0, "length": 32.0})
        else:
            obj.update({"width": 35.0, "length": 45.0})
        if object_type == "svg_pocket":
            obj.update({
                "svg_path": os.path.join(macro_directory(), "examples", "example_cutout.svg"),
                "scale": 1.0,
            })
            length, width = _svg_planning_dimensions(obj)
            obj.update({"length": length, "width": width})
        if object_type == "divider_region":
            obj.update({"rows": 2, "columns": 2, "wall": MIN_WALL})
        return obj

    def _add_object_list_item(self, obj):
        item = self.QtWidgets.QListWidgetItem(
            self.project_canvas._display_name(obj))
        item.setData(self.QtCore.Qt.UserRole, obj["id"])
        self.object_list.addItem(item)
        self.object_list.setCurrentItem(item)

    def _add_composer_object(self, *_args):
        try:
            object_type = str(self.object_type.currentData())
            obj = self._default_composer_object(object_type)
            self.project_canvas.add_object(obj)
            self._add_object_list_item(obj)
            self.layout_report.setText("Manual layout changed")
        except Exception as exc:
            self._show_error(exc)

    def _find_list_item(self, object_id):
        for index in range(self.object_list.count()):
            item = self.object_list.item(index)
            if str(item.data(self.QtCore.Qt.UserRole)) == str(object_id):
                return item
        return None

    def _canvas_selection_changed(self, obj):
        self._updating_inspector = True
        try:
            if not obj:
                return
            list_item = self._find_list_item(obj["id"])
            if list_item and self.object_list.currentItem() is not list_item:
                self.object_list.setCurrentItem(list_item)
            self.object_name.setText(str(obj.get("name") or obj["id"]))
            self.object_x.setValue(float(obj.get("x", 0.0)))
            self.object_y.setValue(float(obj.get("y", 0.0)))
            diameter = float(obj.get("diameter", obj.get("width", 45.0)))
            self.object_width.setValue(diameter)
            self.object_length.setValue(float(obj.get("length", diameter)))
            self.object_height.setValue(float(obj.get("height", 20.0)))
            self.object_clearance.setValue(float(obj.get("clearance", 0.3)))
            self.object_finger_scoop.setChecked(
                bool(obj.get("finger_scoop", False)))
            self.object_rotation.setValue(float(obj.get("rotation", 0.0)))
            self.object_layer.setCurrentIndex(1 if obj.get("layer") == "upper" else 0)
            self.object_locked.setChecked(bool(obj.get("locked", False)))
            self.object_svg_path.setText(str(obj.get("svg_path", "")))
            self.object_rows.setValue(int(obj.get("rows", 2)))
            self.object_columns.setValue(int(obj.get("columns", 2)))
            is_svg = obj["type"] == "svg_pocket"
            is_divider = obj["type"] == "divider_region"
            self.object_clearance.setEnabled(not is_divider)
            self.object_finger_scoop.setEnabled(not is_divider)
            self.object_svg_path.setVisible(is_svg)
            label = self.object_svg_path.parentWidget().layout().labelForField(
                self.object_svg_path)
            if label:
                label.setVisible(is_svg)
            for widget in (self.object_rows, self.object_columns):
                widget.setVisible(is_divider)
                field_label = widget.parentWidget().layout().labelForField(widget)
                if field_label:
                    field_label.setVisible(is_divider)
        finally:
            self._updating_inspector = False

    def _object_list_selected(self, current, _previous):
        if self._updating_inspector or not current:
            return
        object_id = str(current.data(self.QtCore.Qt.UserRole))
        item = self.project_canvas.items.get(object_id)
        if item:
            self.project_canvas.scene.clearSelection()
            item.setSelected(True)

    def _apply_inspector(self, *_args, **kwargs):
        if self._updating_inspector:
            return
        obj = self.project_canvas.selected_object()
        if not obj:
            return
        position_from_controls = bool(kwargs.get("position_from_controls", False))
        if not position_from_controls:
            self._updating_inspector = True
            try:
                self.object_x.setValue(float(obj.get("x", 0.0)))
                self.object_y.setValue(float(obj.get("y", 0.0)))
            finally:
                self._updating_inspector = False
        updates = {
            "name": self.object_name.text().strip() or obj["id"],
            "x": (self.object_x.value() if position_from_controls else
                  float(obj.get("x", 0.0))),
            "y": (self.object_y.value() if position_from_controls else
                  float(obj.get("y", 0.0))),
            "height": self.object_height.value(),
            "clearance": (0.0 if obj["type"] == "divider_region" else
                          self.object_clearance.value()),
            "finger_scoop": (False if obj["type"] == "divider_region" else
                              self.object_finger_scoop.isChecked()),
            "rotation": self.object_rotation.value(),
            "layer": "upper" if self.object_layer.currentIndex() == 1 else "lower",
            "locked": self.object_locked.isChecked(),
            "svg_path": self.object_svg_path.text().strip(),
            "rows": self.object_rows.value(),
            "columns": self.object_columns.value(),
        }
        if obj["type"] == "circular_pocket":
            updates.update({"diameter": self.object_width.value(),
                            "width": self.object_width.value(),
                            "length": self.object_width.value()})
        else:
            updates.update({"width": self.object_width.value(),
                            "length": self.object_length.value()})
        if obj["type"] == "svg_pocket":
            sizing_source = dict(obj)
            sizing_source.update(updates)
            length, width = _svg_planning_dimensions(sizing_source)
            updates.update({"width": width, "length": length})
            self._updating_inspector = True
            try:
                self.object_width.setValue(width)
                self.object_length.setValue(length)
            finally:
                self._updating_inspector = False
        self.project_canvas.update_selected(updates)
        list_item = self._find_list_item(obj["id"])
        if list_item:
            updated = dict(obj)
            updated.update(updates)
            list_item.setText(self.project_canvas._display_name(updated))

    def _duplicate_composer_object(self, *_args):
        obj = self.project_canvas.selected_object()
        if not obj:
            return
        obj["id"] = self._next_object_id(obj["type"])
        obj["name"] = "%s copy" % obj.get("name", "Object")
        obj["x"] = float(obj.get("x", 0.0)) + 6.0
        obj["y"] = float(obj.get("y", 0.0)) + 6.0
        obj["locked"] = False
        self.project_canvas.add_object(obj)
        self._add_object_list_item(obj)

    def _delete_composer_object(self, *_args):
        object_id = self.project_canvas.delete_selected()
        if object_id:
            item = self._find_list_item(object_id)
            if item:
                self.object_list.takeItem(self.object_list.row(item))

    def _rotate_composer_object(self, *_args):
        obj = self.project_canvas.selected_object()
        if obj:
            self.project_canvas.update_selected(
                {"rotation": (float(obj.get("rotation", 0.0)) + 90.0) % 360.0})
            self._canvas_selection_changed(self.project_canvas.selected_object())

    def _toggle_composer_lock(self, *_args):
        obj = self.project_canvas.selected_object()
        if obj:
            self.project_canvas.update_selected({"locked": not bool(obj.get("locked", False))})
            self._canvas_selection_changed(self.project_canvas.selected_object())

    def _project_controls(self):
        source = str(self.lid_clearance_source.currentData())
        current_objects = self.project_canvas.to_objects()
        return {
            "schema_version": 1,
            "case": {
                "case_model": self._selected_case_name(),
                "internal_length": self.internal_l.value(),
                "internal_width": self.internal_w.value(),
                "insert_depth": self.insert_depth.value(),
                "corner_radius": self.corner_radius.value(),
                "side_clearance": self.side_clearance.value(),
                "bottom_clearance": self.bottom_clearance.value(),
                "taper_allowance": self.taper_allowance.value(),
            },
            "lid": {
                "source": source,
                "clearance_mm": (self.lid_clearance.value()
                                 if source != "unknown" else None),
                "envelope_source": str(self.lid_envelope_source.currentData()),
                "length_mm": (self.lid_length.value()
                              if self.lid_length.value() > 0.0 else None),
                "width_mm": (self.lid_width.value()
                             if self.lid_width.value() > 0.0 else None),
            },
            "lid_panel": self._lid_panel_settings(),
            "layers": {
                "enabled": self.layers_enabled.isChecked(),
                "ratio": self.layer_ratio.value() / 100.0,
            },
            "containment": {
                "mode": str(self.containment_mode.currentData()),
                "clearance_mm": self.retention_clearance.value(),
                "panel_thickness_mm": self.retention_panel_thickness.value(),
            },
            "printer": {
                "bed_x": self.bed_x.value(),
                "bed_y": self.bed_y.value(),
                "margin": self.bed_margin.value(),
                "split": self.split_for_bed.isChecked(),
            },
            "objects": current_objects,
        }

    def _project_spec(self, lid_panel_enabled=None, compute_layout_inset=True):
        controls = self._project_controls()
        spec = _overlay_edited_controls(
            self._base_project, self._initial_project_controls, controls)
        spec["layers"].setdefault("floor_mm", 2.4)
        spec["lid_panel"]["enabled"] = (
            self.mode_combo.currentIndex() == 3
            if lid_panel_enabled is None else bool(lid_panel_enabled))
        if (self._layout_snapshot is not None and
                controls["objects"] == self._layout_snapshot):
            spec["unplaced"] = list(self._layout_unplaced)
        else:
            spec.pop("unplaced", None)
            self._layout_snapshot = None
            self._layout_unplaced = []
        spec = _project_with_svg_dimensions(spec)
        if compute_layout_inset:
            params = _project_case_params(spec)
            inset = _required_project_layout_inset(spec, params)
            spec["case"]["layout_inset"] = max(
                float(spec["case"].get("layout_inset", 0.0)), round(inset, 3))
        else:
            spec["case"]["layout_inset"] = 0.0
        usable_length = max(
            1.0, spec["case"]["internal_length"] -
            2.0 * (spec["case"]["side_clearance"] +
                   spec["case"]["taper_allowance"]))
        usable_width = max(
            1.0, spec["case"]["internal_width"] -
            2.0 * (spec["case"]["side_clearance"] +
                   spec["case"]["taper_allowance"]))
        if compute_layout_inset:
            self.project_canvas.set_case(
                usable_length, usable_width, spec["case"]["layout_inset"])
        return spec

    def _apply_auto_layout(self, *_args):
        try:
            model_api = _project_module()
            strategy = str(self.layout_strategy.currentData())
            spec = self._project_spec()
            planner_spec, placement_offsets = _project_for_clearance_aware_layout(
                spec)
            result = model_api.layout_project(planner_spec, strategy)
            project = _project_with_layout(
                spec, result, placement_offsets)
            self.project_canvas.set_objects(project["objects"])
            self.object_list.clear()
            for obj in project["objects"]:
                self._add_object_list_item(obj)
            unplaced = list(getattr(result, "unplaced", project.get("unplaced", [])))
            self._layout_unplaced = [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in unplaced
            ]
            self._layout_snapshot = json.loads(json.dumps(
                self.project_canvas.to_objects()))
            if unplaced:
                reasons = [item.reason if hasattr(item, "reason")
                           else str(item.get("reason", item))
                           for item in unplaced]
                self.layout_report.setText(
                    "%s: %d unplaced — %s" %
                    (self.layout_strategy.currentText(), len(unplaced),
                     "; ".join(reasons)))
            else:
                self.layout_report.setText(
                    "%s: all objects placed" % self.layout_strategy.currentText())
        except Exception as exc:
            self._show_error(exc)

    def _refresh_series(self, *_args, **kwargs):
        load_case = kwargs.get("load_case", True)
        brand = self.brand_combo.currentText()
        custom = brand == "Custom measurements"
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        if custom:
            self.series_combo.addItem("Not applicable")
        else:
            series = sorted(set(model["_series"] for model in self.models.values()
                                if model["_brand"] == brand))
            self.series_combo.addItems(series)
        self.series_combo.setEnabled(not custom)
        self.series_combo.blockSignals(False)
        self._refresh_models(load_case=load_case)

    def _refresh_models(self, *_args, **kwargs):
        load_case = kwargs.get("load_case", True)
        brand = self.brand_combo.currentText()
        series = self.series_combo.currentText()
        custom = brand == "Custom measurements"
        self.case_combo.blockSignals(True)
        self.case_combo.clear()
        if custom:
            self.case_combo.addItem("Custom case", "Custom Case")
        else:
            choices = sorted(
                ((model["_model"], display_name)
                 for display_name, model in self.models.items()
                 if model["_brand"] == brand and model["_series"] == series),
                key=lambda item: item[0])
            for model_name, display_name in choices:
                self.case_combo.addItem(model_name, display_name)
        self.case_combo.setEnabled(not custom)
        self.case_combo.blockSignals(False)
        if load_case and hasattr(self, "internal_l"):
            self._load_case()

    def _brand_changed(self, *_args):
        self._refresh_series()

    def _series_changed(self, *_args):
        self._refresh_models()

    def _selected_case_name(self):
        value = self.case_combo.currentData()
        return str(value) if value else "Custom Case"

    def _set_case_selection(self, display_name):
        if display_name == "Custom Case" or display_name not in self.models:
            self.brand_combo.setCurrentText("Custom measurements")
            return
        model = self.models[display_name]
        self.brand_combo.setCurrentText(model["_brand"])
        self.series_combo.setCurrentText(model["_series"])
        index = self.case_combo.findData(display_name)
        if index >= 0:
            self.case_combo.setCurrentIndex(index)

    def _load_active_project(self):
        """Hydrate this dialog's document, including exposed legacy modes."""
        doc = self._document
        if doc is None:
            return False
        try:
            root = _find_project_group(doc)
            params = _find_parameter_object(doc)
            if not (getattr(root, "ProjectJSON", "") or
                    getattr(params, "ProjectJSON", "")):
                raw = getattr(params, "ParameterJSON", "")
                if not raw:
                    return False
                return self._load_legacy_parameters(json.loads(str(raw)))
            project = load_project(doc)
        except Exception as exc:
            self._load_error = "Stored project could not be loaded: %s" % exc
            self.status.setText(self._load_error)
            return False
        case = project["case"]
        self._set_case_selection(case["case_model"])
        self.internal_l.setValue(float(case["internal_length"]))
        self.internal_w.setValue(float(case["internal_width"]))
        self.insert_depth.setValue(float(case["insert_depth"]))
        self.corner_radius.setValue(float(case["corner_radius"]))
        self.side_clearance.setValue(float(case["side_clearance"]))
        self.bottom_clearance.setValue(float(case["bottom_clearance"]))
        self.taper_allowance.setValue(float(case["taper_allowance"]))
        lid = project["lid"]
        envelope_index = self.lid_envelope_source.findData(
            lid.get("envelope_source", "unknown"))
        self.lid_envelope_source.setCurrentIndex(max(0, envelope_index))
        self.lid_length.setValue(float(lid.get("length_mm") or 0.0))
        self.lid_width.setValue(float(lid.get("width_mm") or 0.0))
        lid_index = self.lid_clearance_source.findData(lid["source"])
        self.lid_clearance_source.setCurrentIndex(max(0, lid_index))
        self.lid_clearance.setValue(float(lid.get("clearance_mm") or 0.0))
        layers = project["layers"]
        self.layers_enabled.setChecked(bool(layers["enabled"]))
        self.layer_ratio.setValue(int(round(float(layers["ratio"]) * 100.0)))
        containment = project["containment"]
        containment_index = self.containment_mode.findData(containment["mode"])
        self.containment_mode.setCurrentIndex(max(0, containment_index))
        self.retention_clearance.setValue(float(containment["clearance_mm"]))
        self.retention_panel_thickness.setValue(
            float(containment["panel_thickness_mm"]))
        printer = project["printer"]
        self.bed_x.setValue(float(printer["bed_x"]))
        self.bed_y.setValue(float(printer["bed_y"]))
        self.bed_margin.setValue(float(printer["margin"]))
        self.split_for_bed.setChecked(bool(printer["split"]))
        panel = project.get("lid_panel") or _project_module().default_lid_panel()
        pattern_index = self.panel_pattern.findData(panel["pattern"])
        self.panel_pattern.setCurrentIndex(max(0, pattern_index))
        self.panel_thickness.setValue(float(panel["thickness_mm"]))
        self.payload_thickness.setValue(float(panel["payload_thickness_mm"]))
        self.edge_inset.setValue(float(panel["edge_inset_mm"]))
        self.panel_radius.setValue(float(panel["corner_radius_mm"]))
        slot = panel["slot_grid"]
        self.slot_width.setValue(float(slot["slot_length_mm"]))
        self.slot_height.setValue(float(slot["slot_width_mm"]))
        self.slot_pitch_x.setValue(float(slot["pitch_x_mm"]))
        self.slot_pitch_y.setValue(float(slot["pitch_y_mm"]))
        self.slot_margin_x.setValue(float(slot["margin_x_mm"]))
        self.slot_margin_y.setValue(float(slot["margin_y_mm"]))
        slot_index = self.slot_orientation.findData(slot["orientation"])
        self.slot_orientation.setCurrentIndex(max(0, slot_index))
        perforated = panel["perforated_grid"]
        self.perforation_diameter.setValue(float(perforated["diameter_mm"]))
        self.perforation_pitch_x.setValue(float(perforated["pitch_x_mm"]))
        self.perforation_pitch_y.setValue(float(perforated["pitch_y_mm"]))
        self.perforation_margin_x.setValue(float(perforated["margin_x_mm"]))
        self.perforation_margin_y.setValue(float(perforated["margin_y_mm"]))
        keepouts = panel["keepouts"]
        self.rim_keepout.setValue(float(keepouts["rim_mm"]))
        self.seal_keepout.setValue(float(keepouts["seal_mm"]))
        self.hinge_keepout.setValue(float(keepouts["hinge_mm"]))
        hinge_index = self.hinge_edge.findData(keepouts["hinge_edge"])
        self.hinge_edge.setCurrentIndex(max(0, hinge_index))
        self.clearance_keepout_margin.setValue(
            float(keepouts["clearance_margin_mm"]))
        self.custom_keepouts.setText("; ".join(
            "%.3f,%.3f,%.3f,%.3f,%s" %
            (item["x_mm"], item["y_mm"], item["length_mm"],
             item["width_mm"], str(item["label"]).replace(",", " "))
            for item in keepouts["rectangles"]))
        mounting = panel["mounting"]
        self.perimeter_mounting.setChecked(bool(mounting["perimeter_enabled"]))
        self.printable_retainers.setChecked(bool(mounting["retainers_enabled"]))
        self.retainer_count.setValue(int(mounting["retainer_count"]))
        self.retainer_width.setValue(float(mounting["retainer_width_mm"]))
        self.retainer_projection.setValue(
            float(mounting["retainer_projection_mm"]))
        self.retainer_clearance.setValue(float(mounting["retainer_clearance_mm"]))
        self.panel_lift_access.setChecked(bool(mounting["lift_access_enabled"]))
        self.lift_access_diameter.setValue(
            float(mounting["lift_access_diameter_mm"]))
        self.fastener_holes.setChecked(bool(mounting["fastener_holes_enabled"]))
        self.hole_diameter.setValue(float(mounting["fastener_hole_diameter_mm"]))
        self.hole_edge.setValue(float(mounting["fastener_edge_offset_mm"]))
        self.custom_holes.setText("; ".join(
            "%.3f,%.3f" % (item["x_mm"], item["y_mm"])
            for item in mounting["custom_fastener_holes"]))
        splitting = panel["splitting"]
        self.keyed_panel_split.setChecked(bool(splitting["keyed_alignment"]))
        self.split_key_size.setValue(float(splitting["key_size_mm"]))
        self.split_key_clearance.setValue(float(splitting["key_clearance_mm"]))
        self.mode_combo.setCurrentIndex(3 if panel["enabled"] else 0)
        self.project_canvas.set_objects(project["objects"])
        self.object_list.clear()
        for obj in project["objects"]:
            self._add_object_list_item(obj)
        self._object_counter = max(self._object_counter, len(project["objects"]))
        self._layout_unplaced = list(project.get("unplaced", []))
        self._layout_snapshot = json.loads(json.dumps(
            self.project_canvas.to_objects())) if self._layout_unplaced else None
        if project.get("layout_strategy"):
            layout_index = self.layout_strategy.findData(
                project["layout_strategy"])
            if layout_index >= 0:
                self.layout_strategy.setCurrentIndex(layout_index)
        self._base_project = json.loads(json.dumps(project))
        self._initial_project_controls = self._project_controls()
        self._update_canvas_case()
        self._update_layer_budget()
        self._panel_pattern_changed()
        self._update_lid_generation_gate()
        self._generation_signature = self._request_signature(
            (self.mode_combo.currentIndex(), project))
        self.status.setText(
            "Loaded editable project from %s" % self._document_name)
        return True

    def _load_legacy_parameters(self, params):
        if not isinstance(params, dict):
            raise ValueError("Stored insert parameters must be a JSON object")
        stored = json.loads(json.dumps(params))
        # These are the optional defaults used by generate_insert,
        # build_divider_insert, build_svg_insert, and _lid_clearance.
        # Numeric values read by _as_float are required, not defaults.
        optional = {
            "case_model": "Custom Case", "insert_type": "SVG Cutout",
            "rows": 1, "columns": 1, "divider_layout": "Equal grid",
            "bed_x": DEFAULT_BED, "bed_y": DEFAULT_BED, "bed_margin": 5.0,
            "split_for_bed": False, "through_cut": False, "invert_svg": False,
            "lid_clearance_source": "unknown", "lid_clearance": 0.0,
        }
        params = dict(optional, **params)
        modes = {"SVG Cutout": 1, "Dividers": 2, "Case Blank": 4}
        mode = params.get("insert_type")
        if mode not in modes:
            raise ValueError("Stored legacy insert mode is not supported: %s" % mode)
        self._set_case_selection(params.get("case_model", "Custom Case"))
        numeric = {
            "internal_length": self.internal_l, "internal_width": self.internal_w,
            "insert_depth": self.insert_depth, "corner_radius": self.corner_radius,
            "side_clearance": self.side_clearance, "bottom_clearance": self.bottom_clearance,
            "taper_allowance": self.taper_allowance, "bed_x": self.bed_x,
            "bed_y": self.bed_y, "bed_margin": self.bed_margin,
            "lid_length": self.lid_length, "lid_width": self.lid_width,
            "lid_clearance": self.lid_clearance,
            "svg_scale": self.svg_scale, "svg_x": self.svg_x, "svg_y": self.svg_y,
            "svg_rotation": self.svg_rotation, "cutout_depth": self.cutout_depth,
            "svg_clearance": self.svg_clearance, "rows": self.rows,
            "columns": self.columns, "base_thickness": self.base_thickness,
            "outer_wall": self.outer_wall, "divider_wall": self.divider_wall,
            "divider_height": self.divider_height,
        }
        for key, widget in numeric.items():
            if key in params and params[key] is not None:
                value = float(params[key])
                if not math.isfinite(value):
                    raise ValueError("Stored %s must be finite" % key)
                widget.setValue(int(value) if key in ("rows", "columns") else value)
        for key, widget in (("lid_envelope_source", self.lid_envelope_source),
                            ("lid_clearance_source", self.lid_clearance_source)):
            if key in params:
                index = widget.findData(params[key])
                if index < 0:
                    raise ValueError("Stored %s is not supported" % key)
                widget.setCurrentIndex(index)
        # Setting the source can clear an unknown value, so restore a known one last.
        if params.get("lid_clearance_source") in ("measured", "cad-derived"):
            self.lid_clearance.setValue(float(params.get("lid_clearance") or 0.0))
        for key, widget in (("split_for_bed", self.split_for_bed),
                            ("through_cut", self.through_cut),
                            ("invert_svg", self.invert_svg)):
            if key in params:
                widget.setChecked(bool(params[key]))
        self.svg_path.setText(str(params.get("svg_path", "")))
        layouts = ("Equal grid", "Rows only", "Columns only", "Measured bay sizes")
        layout = params.get("divider_layout", "Equal grid")
        if layout not in layouts:
            raise ValueError("Stored divider layout is not supported: %s" % layout)
        self.div_layout.setCurrentIndex(layouts.index(layout))
        for key, editor in (("length_bays", self.column_bays),
                            ("width_bays", self.row_bays)):
            if params.get(key):
                entries = []
                for token in str(params[key]).split(","):
                    token = token.strip()
                    entries.append((50.0 if token == "*" else float(token), token == "*"))
                editor.set_bays(entries)
        self.mode_combo.setCurrentIndex(modes[mode])
        self._base_legacy_params = stored
        self._initial_legacy_controls = self._legacy_controls()
        self._generation_signature = self._request_signature(self._current_request())
        self.status.setText("Loaded editable %s from %s" % (mode, self._document_name))
        return True

    def _load_case(self):
        name = self._selected_case_name()
        custom = name == "Custom Case"
        for widget in (self.internal_l, self.internal_w, self.corner_radius,
                       self.lid_length, self.lid_width):
            widget.setEnabled(custom)
        self.lid_envelope_source.setEnabled(custom)
        if not custom:
            model = self.models[name]
            verification = model.get("_verification", {})
            label = str(verification.get("label") or
                        verification.get("level", "Unverified").replace("_", " ").title())
            basis = str(verification.get("geometry_basis") or
                        "Stored synthetic geometry")
            self.verification_guide.setText(
                "%s — %s. This is not a physical-fit claim." % (label, basis))
            self.internal_l.setValue(float(model["internal_length"]))
            self.internal_w.setValue(float(model["internal_width"]))
            bottom_depth = float(model.get("bottom_depth") or
                                 model["internal_depth"])
            self.insert_depth.setValue(bottom_depth)
            usable_depth = bottom_depth - self.bottom_clearance.value()
            self.divider_height.setValue(
                max(0.1, usable_depth - self.base_thickness.value()))
            radius = float(model["bottom_corner_radius"])
            self.corner_radius.setValue(radius)
            lid_dimensions = _verified_lid_dimensions(model)
            self.lid_length.setValue(
                lid_dimensions[0] if lid_dimensions else 0.0)
            self.lid_width.setValue(
                lid_dimensions[1] if lid_dimensions else 0.0)
            lid_metadata = dict(model.get("_lid") or {})
            envelope_source = str(
                lid_metadata.get("envelope_source", "unknown"))
            envelope_index = self.lid_envelope_source.findData(
                envelope_source)
            self.lid_envelope_source.setCurrentIndex(
                max(0, envelope_index))
            clearance_source = str(
                lid_metadata.get("clearance_source", "unknown"))
            clearance_index = self.lid_clearance_source.findData(
                clearance_source)
            self.lid_clearance_source.setCurrentIndex(
                max(0, clearance_index))
            self.lid_clearance.setValue(
                float(lid_metadata.get("clearance_mm") or 0.0))
            total_depth = float(model["internal_depth"])
            lid_depth = float(model.get("lid_depth") or
                              max(0.0, total_depth - bottom_depth))
            self.corner_radius.setSpecialValueText("")
            self.corner_radius.setToolTip(
                "Stored rounded-corner value for this synthetic preset.")
            self.depth_guide.setText(
                "Synthetic bottom: %.1f mm. Unverified lid allowance: %.1f mm. "
                "Total demonstration envelope: %.1f mm. Replace these values "
                "with measurements for a real case." %
                (bottom_depth, lid_depth, total_depth))
            self.status.setText(
                "Synthetic preset loaded: %.1f × %.1f × %.1f mm. "
                "No compatibility or fit is asserted." %
                (float(model["internal_length"]),
                 float(model["internal_width"]), bottom_depth))
        else:
            self.verification_guide.setText(
                "Your measurements — verify the physical case, taper, ribs, and lid clearance.")
            self.corner_radius.setSpecialValueText("")
            self.corner_radius.setToolTip(
                "Enter the inside plan corner radius measured in the custom case.")
            self.depth_guide.setText(
                "For a custom case, enter the bottom compartment depth available to the insert.")
            self.status.setText(
                "Custom measurements selected. Physical fit remains unverified until measured, printed, and tested.")
        self._update_lid_availability()
        self._update_canvas_case()
        self._update_layer_budget()

    def _lid_mode_available(self):
        name = self._selected_case_name()
        if name == "Custom Case":
            return self.lid_length.value() > 0.0 and self.lid_width.value() > 0.0
        return bool(_verified_lid_dimensions(self.models.get(name)))

    def _update_lid_availability(self, *_args):
        dimensions_present = self._lid_mode_available()
        item = self.mode_combo.model().item(3)
        if item:
            item.setEnabled(True)
        name = self._selected_case_name()
        envelope_source = str(self.lid_envelope_source.currentData())
        if dimensions_present and envelope_source in ("measured", "cad-derived"):
            message = (
                "%.1f × %.1f mm %s lid envelope recorded. Printable generation "
                "also requires evidenced closed-lid clearance and a passing height budget." %
                (self.lid_length.value(), self.lid_width.value(), envelope_source))
        elif dimensions_present:
            message = (
                "Dimensions are configured, but lid-envelope evidence is Unknown. "
                "Preview/save is allowed; printable generation is blocked.")
        elif name == "Custom Case":
            message = (
                "Lid-panel configuration is available. Enter the envelope length/width "
                "and record measured or CAD-derived evidence before printing.")
        else:
            message = (
                "This synthetic preset stores no evidenced lid-panel envelope. "
                "The panel can be configured, but printable generation remains blocked.")
        self.lid_availability.setText(message)
        self._update_lid_generation_gate()

    def _mode_changed(self):
        self.stack.setCurrentIndex(self.mode_combo.currentIndex())
        self._update_lid_generation_gate()

    def _divider_layout_changed(self):
        index = self.div_layout.currentIndex()
        measured = index == 3
        show_rows = index in (0, 1)
        show_columns = index in (0, 2)
        for widget, visible in ((self.rows, show_rows),
                                (self.columns, show_columns)):
            widget.setVisible(visible)
            label = self.divider_form.labelForField(widget)
            if label:
                label.setVisible(visible)
        self.measured_tabs.setVisible(measured)

    def _browse_svg(self):
        filename = self.QtWidgets.QFileDialog.getOpenFileName(
            self.dialog, "Select SVG cutout drawing", macro_directory(),
            "SVG files (*.svg *.SVG);;All files (*)")[0]
        if filename:
            self.svg_path.setText(filename)

    def _legacy_controls(self):
        insert_modes = ("Project Composer", "SVG Cutout", "Dividers", "Lid Panel", "Case Blank")
        divider_layouts = ("Equal grid", "Rows only", "Columns only",
                           "Measured bay sizes")
        return {
            "case_model": self._selected_case_name(),
            "internal_length": self.internal_l.value(), "internal_width": self.internal_w.value(),
            "lid_length": self.lid_length.value(), "lid_width": self.lid_width.value(),
            "lid_envelope_source": str(self.lid_envelope_source.currentData()),
            "lid_clearance_source": str(self.lid_clearance_source.currentData()),
            "lid_clearance": self.lid_clearance.value(),
            "insert_depth": self.insert_depth.value(), "corner_radius": self.corner_radius.value(),
            "side_clearance": self.side_clearance.value(), "bottom_clearance": self.bottom_clearance.value(),
            "taper_allowance": self.taper_allowance.value(), "bed_x": self.bed_x.value(), "bed_y": self.bed_y.value(),
            "bed_margin": self.bed_margin.value(), "split_for_bed": self.split_for_bed.isChecked(),
            "insert_type": insert_modes[self.mode_combo.currentIndex()], "svg_path": self.svg_path.text(),
            "svg_scale": self.svg_scale.value(), "svg_x": self.svg_x.value(), "svg_y": self.svg_y.value(),
            "svg_rotation": self.svg_rotation.value(), "cutout_depth": self.cutout_depth.value(),
            "through_cut": self.through_cut.isChecked(), "invert_svg": self.invert_svg.isChecked(),
            "svg_clearance": self.svg_clearance.value(),
            "divider_layout": divider_layouts[self.div_layout.currentIndex()],
            "rows": self.rows.value(), "columns": self.columns.value(), "base_thickness": self.base_thickness.value(),
            "length_bays": self.column_bays.schedule_text(),
            "width_bays": self.row_bays.schedule_text(),
            "outer_wall": self.outer_wall.value(), "divider_wall": self.divider_wall.value(),
            "divider_height": self.divider_height.value(), "edge_inset": self.edge_inset.value(),
            "panel_thickness": self.panel_thickness.value(), "panel_corner_radius": self.panel_radius.value(),
            "payload_thickness": self.payload_thickness.value(),
            "slot_width": self.slot_width.value(), "slot_height": self.slot_height.value(),
            "slot_pitch_x": self.slot_pitch_x.value(), "slot_pitch_y": self.slot_pitch_y.value(),
            "hole_diameter": self.hole_diameter.value(), "hole_edge_offset": self.hole_edge.value(),
            "custom_holes": self.custom_holes.text(),
            "lid_dimensions_verified": self._lid_mode_available(),
        }

    def _params(self):
        controls = self._legacy_controls()
        if (self._initial_legacy_controls and
                controls == self._initial_legacy_controls):
            # An unchanged API-authored file keeps its omitted optional keys,
            # instead of converting unrelated GUI starter values to settings.
            return json.loads(json.dumps(self._base_legacy_params))
        return _overlay_edited_controls(
            self._base_legacy_params, self._initial_legacy_controls,
            controls)

    def _refresh_export_parts(self, *_args, **kwargs):
        """Refresh the generated-part checklist without losing user choices."""
        select_all = bool(kwargs.get("select_all", False))
        old_names = []
        old_checked = set()
        for index in range(self.export_parts.count()):
            item = self.export_parts.item(index)
            name = str(item.data(self.QtCore.Qt.UserRole) or "")
            old_names.append(name)
            if item.checkState() == self.QtCore.Qt.Checked:
                old_checked.add(name)
        try:
            objects = active_results(self._bound_document())
        except RuntimeError:
            objects = []
        self._generated_has_parts = bool(objects)
        new_names = [obj.Name for obj in objects]
        keep_checks = bool(old_names) and not select_all
        self.export_parts.blockSignals(True)
        self.export_parts.clear()
        for obj in objects:
            label = str(getattr(obj, "Label", "") or obj.Name)
            item = self.QtWidgets.QListWidgetItem(
                "%s  [%s]" % (label, obj.Name))
            item.setData(self.QtCore.Qt.UserRole, obj.Name)
            item.setFlags(item.flags() | self.QtCore.Qt.ItemIsUserCheckable)
            checked = not keep_checks or obj.Name in old_checked
            item.setCheckState(
                self.QtCore.Qt.Checked if checked else self.QtCore.Qt.Unchecked)
            self.export_parts.addItem(item)
        self.export_parts.blockSignals(False)
        self._export_part_selection_changed()
        self._update_export_availability()

    def _set_export_parts_checked(self, checked):
        state = (self.QtCore.Qt.Checked if checked
                 else self.QtCore.Qt.Unchecked)
        self.export_parts.blockSignals(True)
        for index in range(self.export_parts.count()):
            self.export_parts.item(index).setCheckState(state)
        self.export_parts.blockSignals(False)
        self._export_part_selection_changed()

    def _export_part_selection_changed(self, *_args):
        total = self.export_parts.count()
        selected = sum(
            self.export_parts.item(index).checkState() == self.QtCore.Qt.Checked
            for index in range(total))
        if total == 0:
            text = "Generate or open a model to choose export parts."
        elif selected == total:
            text = "All %d generated part%s selected." % (
                total, "" if total == 1 else "s")
        elif selected == 0:
            text = "No parts selected — choose at least one before STL or STEP export."
        else:
            text = "%d of %d generated parts selected." % (selected, total)
        self.export_part_summary.setText(text)

    def _selected_export_names(self):
        if self.export_parts.count() == 0:
            self._refresh_export_parts()
        available = []
        selected = []
        for index in range(self.export_parts.count()):
            item = self.export_parts.item(index)
            name = str(item.data(self.QtCore.Qt.UserRole) or "")
            available.append(name)
            if item.checkState() == self.QtCore.Qt.Checked:
                selected.append(name)
        return _resolve_export_names(available, selected)

    def _show_error(self, exc):
        self.status.setText("Error: %s" % exc)
        self.QtWidgets.QMessageBox.warning(self.dialog, "Case Insert Generator", str(exc))

    def _current_request(self):
        mode = self.mode_combo.currentIndex()
        if mode in (0, 3):
            spec = self._project_spec(lid_panel_enabled=mode == 3)
            return mode, _project_module().validate_project(spec)
        return mode, _resolved_params(self._params())

    @staticmethod
    def _request_signature(request):
        mode, settings = request
        settings = json.loads(json.dumps(settings))
        if mode in (0, 3):
            for key in ("result", "results", "parts", "warnings", "lid_panel_report"):
                settings.pop(key, None)
            settings.setdefault("unplaced", [])
            # The composer placement inset does not shape the lid-panel model.
            if mode == 3:
                settings.get("case", {}).pop("layout_inset", None)
        return json.dumps([mode, settings], sort_keys=True)

    def _generate_current(self, request):
        mode, settings = request
        doc = self._bound_document(create=True)
        self._assert_geometry_unchanged(doc)
        if mode == 0:
            authored = dict(settings)
            # Auto Layout resolves canvas positions when explicitly applied.
            # A strategy saved by an earlier API call is historical here,
            # not an instruction to repack the user's current manual edits.
            authored.pop("layout_strategy", None)
            report = generate_project(authored, document=doc)
        elif mode == 3:
            budget = _project_module().lid_panel_height_budget(settings)
            report = (generate_lid_panel_project(settings, document=doc)
                      if budget["printable"]
                      else preview_lid_panel_project(settings, document=doc))
        else:
            report = generate_insert(settings, document=doc)
        self._source_record = self._document_record(doc)
        if mode in (0, 3):
            self._base_project = load_project(doc)
            self._initial_project_controls = self._project_controls()
        else:
            params = _find_parameter_object(doc)
            self._base_legacy_params = json.loads(str(params.ParameterJSON))
            self._initial_legacy_controls = self._legacy_controls()
        self._generation_signature = self._request_signature(self._current_request())
        self._generated_controls_signature = self._controls_signature()
        self._geometry_snapshot = self._geometry_state(doc)
        self._refresh_export_parts()
        if not isinstance(report, dict) and hasattr(report, "to_mapping"):
            report = report.to_mapping()
        return report

    def _generate(self):
        try:
            report = self._generate_current(self._current_request())
            if report["parts"]:
                message = "Generated %d printable part%s: valid geometry, %.0f mm³ total" % (
                    report["parts"], "" if report["parts"] == 1 else "s", report["volume"])
            else:
                message = (
                    "Configuration preview saved in the FreeCAD document; "
                    "printable STL/STEP generation remains blocked.")
            if report["warnings"]:
                message += "\n" + "\n".join(report["warnings"])
            self.status.setText(message)
            self._fit_view()
        except Exception as exc:
            self._show_error(exc)

    def _fit_view(self):
        try:
            import FreeCADGui as Gui
            doc = self._bound_document()
            view = Gui.getDocument(doc.Name).activeView()
            view.viewAxonometric()
            view.fitAll()
        except Exception as exc:
            previous = self.status.text()
            self.status.setText("%s\nFit View unavailable: %s" % (previous, exc))

    def _save_path(self, title, pattern, suffix):
        filename = self.QtWidgets.QFileDialog.getSaveFileName(self.dialog, title, macro_directory(), pattern)[0]
        if filename and not filename.lower().endswith(suffix):
            filename += suffix
        return filename

    def _confirm_export_overwrite(self, paths):
        box = self.QtWidgets.QMessageBox(self.dialog)
        box.setWindowTitle("Replace existing export files?")
        box.setIcon(self.QtWidgets.QMessageBox.Warning)
        box.setText("%d export file%s already exist." %
                    (len(paths), "" if len(paths) == 1 else "s"))
        box.setInformativeText(
            "Replace these files? Open Details to inspect the complete destination list.")
        box.setDetailedText("\n".join(paths))
        box.setStandardButtons(self.QtWidgets.QMessageBox.Yes | self.QtWidgets.QMessageBox.No)
        box.setDefaultButton(self.QtWidgets.QMessageBox.No)
        return box.exec_() == self.QtWidgets.QMessageBox.Yes

    def _export_model(self, format_name, exporter, pattern, suffix):
        try:
            doc = self._bound_document()
            self._assert_geometry_unchanged(doc)
            request = self._current_request()
            if request[0] == 3:
                budget = _project_module().lid_panel_height_budget(request[1])
                if not budget["printable"]:
                    self.status.setText(
                        "%s export blocked: %s" %
                        (format_name, " ".join(budget["reasons"])))
                    return
            if self._request_signature(request) != self._generation_signature:
                raise RuntimeError(
                    "Settings changed. Generate or save the updated model before exporting.")
            selected_names = self._selected_export_names()
            path = self._save_path("Export %s" % format_name, pattern, suffix)
            if path:
                destinations = export_paths(path, doc=doc, selected_names=selected_names)
                collisions = [name for name in destinations if os.path.lexists(name)]
                if collisions and not self._confirm_export_overwrite(collisions):
                    self.status.setText("Export cancelled; existing files were kept.")
                    return
                # A file dialog is modeless with respect to document events.
                # Recheck ownership after the picker and collision confirmation.
                self._bound_document()
                self._assert_geometry_unchanged(doc)
                if self._request_signature(self._current_request()) != self._generation_signature:
                    raise RuntimeError("Settings changed while choosing export files. Generate again.")
                outputs = exporter(path, doc=doc, selected_names=selected_names,
                                   overwrite=bool(collisions))
                count = len(outputs) if isinstance(outputs, list) else 1
                self.status.setText("Exported %d %s file%s from %s" %
                                    (count, format_name, "" if count == 1 else "s",
                                     self._document_name))
        except Exception as exc:
            self._show_error(exc)

    def _export_stl(self):
        self._export_model("STL", export_stl, "STL mesh (*.stl)", ".stl")

    def _export_step(self):
        self._export_model("STEP", export_step, "STEP model (*.step *.stp)", ".step")

    def _save_fcstd(self):
        try:
            path = self._save_path("Save FreeCAD document", "FreeCAD document (*.FCStd)", ".FCStd")
            if path:
                request = self._current_request()
                if self._request_signature(request) != self._generation_signature:
                    self._generate_current(request)
                doc = self._bound_document()
                self._assert_geometry_unchanged(doc)
                save_fcstd(path, doc=doc)
                self.status.setText("Saved current settings and model to %s" % path)
        except Exception as exc:
            self._show_error(exc)

    def _reset(self):
        self._base_project = {}
        self._initial_project_controls = {}
        self._base_legacy_params = {}
        self._initial_legacy_controls = {}
        self._set_case_selection("Custom Case")
        self.internal_l.setValue(300.0); self.internal_w.setValue(200.0); self.insert_depth.setValue(40.0)
        self.corner_radius.setValue(8.0); self.side_clearance.setValue(1.0)
        self.lid_length.setValue(0.0); self.lid_width.setValue(0.0)
        self.bottom_clearance.setValue(0.5); self.taper_allowance.setValue(0.5)
        self.bed_x.setValue(DEFAULT_BED); self.bed_y.setValue(DEFAULT_BED)
        self.bed_margin.setValue(5.0); self.split_for_bed.setChecked(False)
        self.mode_combo.setCurrentIndex(0)
        self.lid_envelope_source.setCurrentIndex(0)
        self.lid_clearance_source.setCurrentIndex(0)
        self.lid_clearance.setValue(0.0)
        self.layers_enabled.setChecked(False)
        self.layer_ratio.setValue(50)
        self.containment_mode.setCurrentIndex(0)
        self._layout_snapshot = None
        self._layout_unplaced = []
        self.panel_pattern.setCurrentIndex(0)
        self.panel_thickness.setValue(3.0); self.payload_thickness.setValue(0.0)
        self.edge_inset.setValue(4.0); self.panel_radius.setValue(6.0)
        self.slot_width.setValue(25.0); self.slot_height.setValue(4.0)
        self.slot_pitch_x.setValue(38.0); self.slot_pitch_y.setValue(25.0)
        self.slot_margin_x.setValue(10.0); self.slot_margin_y.setValue(10.0)
        self.slot_orientation.setCurrentIndex(0)
        self.perforation_diameter.setValue(5.0)
        self.perforation_pitch_x.setValue(12.0); self.perforation_pitch_y.setValue(12.0)
        self.perforation_margin_x.setValue(10.0); self.perforation_margin_y.setValue(10.0)
        self.rim_keepout.setValue(4.0); self.seal_keepout.setValue(2.0)
        self.hinge_keepout.setValue(12.0); self.hinge_edge.setCurrentIndex(0)
        self.clearance_keepout_margin.setValue(3.0); self.custom_keepouts.clear()
        self.perimeter_mounting.setChecked(True); self.printable_retainers.setChecked(True)
        self.retainer_count.setValue(4); self.retainer_width.setValue(10.0)
        self.retainer_projection.setValue(3.0); self.retainer_clearance.setValue(0.35)
        self.panel_lift_access.setChecked(True); self.lift_access_diameter.setValue(18.0)
        self.fastener_holes.setChecked(False)
        self.hole_diameter.setValue(3.5); self.hole_edge.setValue(12.0)
        self.custom_holes.clear()
        self.keyed_panel_split.setChecked(True); self.split_key_size.setValue(8.0)
        self.split_key_clearance.setValue(0.25)
        self.div_layout.setCurrentIndex(0); self.rows.setValue(3); self.columns.setValue(3)
        self.column_bays.set_bays([(55.0, False), (70.0, False), (50.0, True)])
        self.row_bays.set_bays([(40.0, False), (50.0, True)])
        self._panel_pattern_changed()
        self._update_lid_generation_gate()
        self.status.setText("Parameters reset")

    def show(self):
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


def show_dialog():
    global _dialog
    _dialog = CaseInsertDialog()
    _dialog.show()
    return _dialog
