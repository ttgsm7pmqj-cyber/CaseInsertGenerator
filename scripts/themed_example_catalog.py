# SPDX-License-Identifier: LGPL-2.1-or-later
"""Original, vendor-neutral themed example specifications.

The dimensions are synthetic demonstration envelopes and layout assumptions.
They are not copied from, or claimed compatible with, any commercial case or
payload.  Every placement is intentionally spaced and locked so the rendered
examples remain legible while the real generator still performs contour,
height, collision, and solid validation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CATALOG_VERSION = 1
GEOMETRY_PROVENANCE = "synthetic-demonstration"
PHYSICAL_FIT_STATUS = "unverified"


def _object(
    object_id: str,
    object_type: str,
    name: str,
    length: float,
    width: float,
    height: float,
    x: float,
    y: float,
    *,
    layer: str = "lower",
    **options: Any,
) -> dict[str, Any]:
    value = {
        "id": object_id,
        "type": object_type,
        "name": name,
        "x": float(x),
        "y": float(y),
        "rotation": 0.0,
        "layer": layer,
        "locked": True,
        "width": float(width),
        "length": float(length),
        "height": float(height),
        "rotatable": False,
    }
    value.update(options)
    return value


def pocket(
    object_id: str,
    name: str,
    length: float,
    width: float,
    height: float,
    x: float,
    y: float,
    *,
    layer: str = "lower",
) -> dict[str, Any]:
    return _object(
        object_id,
        "rectangular_pocket",
        name,
        length,
        width,
        height,
        x,
        y,
        layer=layer,
        clearance=0.5,
    )


def bay(
    object_id: str,
    name: str,
    length: float,
    width: float,
    height: float,
    x: float,
    y: float,
    *,
    layer: str = "lower",
) -> dict[str, Any]:
    return _object(
        object_id,
        "existing_container_bay",
        name,
        length,
        width,
        height,
        x,
        y,
        layer=layer,
        clearance=0.6,
    )


def removable_bin(
    object_id: str,
    name: str,
    length: float,
    width: float,
    height: float,
    x: float,
    y: float,
    *,
    layer: str = "lower",
) -> dict[str, Any]:
    return _object(
        object_id,
        "removable_bin",
        name,
        length,
        width,
        height,
        x,
        y,
        layer=layer,
        clearance=0.4,
        wall=1.8,
    )


def divider(
    object_id: str,
    name: str,
    length: float,
    width: float,
    height: float,
    x: float,
    y: float,
    rows: int,
    columns: int,
    *,
    layer: str = "lower",
) -> dict[str, Any]:
    return _object(
        object_id,
        "divider_region",
        name,
        length,
        width,
        height,
        x,
        y,
        layer=layer,
        rows=int(rows),
        columns=int(columns),
        wall=1.6,
    )


def circle(
    object_id: str,
    name: str,
    diameter: float,
    height: float,
    x: float,
    y: float,
    *,
    layer: str = "lower",
) -> dict[str, Any]:
    return _object(
        object_id,
        "circular_pocket",
        name,
        diameter,
        diameter,
        height,
        x,
        y,
        layer=layer,
        diameter=float(diameter),
        clearance=0.5,
    )


def _project(
    length: float,
    width: float,
    depth: float,
    objects: list[dict[str, Any]],
    *,
    containment: str,
    layers: bool = False,
    ratio: float = 0.5,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case": {
            "case_model": "Custom Case",
            "internal_length": float(length),
            "internal_width": float(width),
            "insert_depth": float(depth),
            "corner_radius": 12.0,
            "side_clearance": 1.0,
            "bottom_clearance": 0.8,
            "taper_allowance": 0.4,
            "geometry_provenance": GEOMETRY_PROVENANCE,
            "compatibility_claim": "none",
        },
        "lid": {"source": "unknown", "clearance_mm": None},
        "layers": {"enabled": bool(layers), "ratio": float(ratio), "floor_mm": 2.4},
        "containment": {
            "mode": containment,
            "clearance_mm": 0.4,
            "panel_thickness_mm": 2.0,
        },
        "printer": {
            "bed_x": 256.0,
            "bed_y": 256.0,
            "margin": 5.0,
            "split": True,
        },
        "objects": objects,
    }


def _pack(
    number: int,
    slug: str,
    title: str,
    description: str,
    safety_note: str,
    project: dict[str, Any],
) -> dict[str, Any]:
    return {
        "number": number,
        "id": f"theme.{slug}.v1",
        "slug": slug,
        "title": title,
        "description": description,
        "geometry_provenance": GEOMETRY_PROVENANCE,
        "physical_fit_status": PHYSICAL_FIT_STATUS,
        "safety_note": safety_note,
        "project": project,
    }


THEMED_PACKS = (
    _pack(
        1,
        "field-mending",
        "Field Mending Kit",
        "Portable fabric repair, thread, fasteners, shears, and small tools.",
        "Tool and spool dimensions are assumptions; needles must remain in closed cards.",
        _project(
            280, 200, 72,
            [
                pocket("shears", "Shears", 150, 38, 28, 55, 45),
                pocket("seam-pliers", "Seam pliers", 145, 50, 32, 55, 105),
                circle("tape-roll", "Tape roll", 48, 18, 215, 75),
                removable_bin("thread-bin", "Thread bin", 82, 58, 20, 20, 20, layer="upper"),
                removable_bin("fastener-bin", "Fastener bin", 82, 58, 20, 115, 20, layer="upper"),
                divider("needle-cards", "Needle cards", 115, 48, 18, 20, 105, 1, 4, layer="upper"),
                circle("spool-a", "Spool A", 42, 20, 170, 100, layer="upper"),
                circle("spool-b", "Spool B", 42, 20, 220, 100, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.60,
        ),
    ),
    _pack(
        2,
        "watercolour-field-studio",
        "Watercolour Field Studio",
        "Palette, brushes, pigment pans, cups, and accessories.",
        "Wet items must be dried before storage; cup and palette fit are unverified.",
        _project(
            300, 220, 58,
            [
                bay("palette", "Palette", 130, 92, 18, 20, 20),
                pocket("brush-roll", "Brush roll", 200, 38, 24, 20, 150),
                circle("cup-a", "Water cup A", 46, 35, 175, 20),
                circle("cup-b", "Water cup B", 46, 35, 235, 20),
                removable_bin("pigment-bin", "Pigment bin", 105, 55, 22, 175, 90),
            ],
            containment="shared_panel",
        ),
    ),
    _pack(
        3,
        "linocut-printmaking",
        "Linocut Printmaking Pack",
        "Lino blocks, brayer, cutters, and ink tins.",
        "Cutters require caps or sheaths; ink-tin dimensions and residue control are unverified.",
        _project(
            320, 230, 66,
            [
                bay("lino-plate", "Lino plate", 150, 110, 18, 20, 20),
                divider("cutter-set", "Cutter set", 110, 80, 28, 190, 20, 2, 4),
                pocket("brayer", "Brayer", 185, 40, 35, 20, 160),
                circle("ink-a", "Ink tin A", 42, 32, 220, 125),
                circle("ink-b", "Ink tin B", 42, 32, 270, 125),
                circle("ink-c", "Ink tin C", 42, 32, 220, 177),
            ],
            containment="shared_panel",
        ),
    ),
    _pack(
        4,
        "calligraphy-wax-seal",
        "Calligraphy and Wax Seal Pack",
        "Pen holders, nibs, ink, wax, and seal handles.",
        "The insert is not leakproof; capped upright ink still requires physical testing.",
        _project(
            280, 190, 55,
            [
                pocket("pen-holders", "Pen holders", 170, 30, 22, 20, 20),
                divider("wax-sticks", "Wax sticks", 150, 45, 22, 20, 70, 1, 5),
                circle("ink-pot", "Ink pot", 45, 35, 205, 20),
                circle("seal-a", "Seal handle A", 32, 28, 195, 85),
                circle("seal-b", "Seal handle B", 32, 28, 235, 85),
                removable_bin("nib-bin", "Nib bin", 110, 45, 18, 20, 130),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        5,
        "analog-camera-care",
        "Analog Camera Care Pack",
        "Camera body, lens, blower, film or media, and cleaning tools.",
        "Optics require measured clearances and soft lining; no impact protection is claimed.",
        _project(
            300, 210, 70,
            [
                bay("camera-body", "Camera body", 150, 95, 34, 35, 55),
                circle("lens", "Lens", 70, 34, 205, 65),
                pocket("blower", "Blower", 55, 85, 20, 20, 20, layer="upper"),
                removable_bin("film-bin", "Film bin", 70, 55, 20, 90, 20, layer="upper"),
                removable_bin("media-bin", "Media bin", 70, 55, 20, 175, 20, layer="upper"),
                divider("cleaning-tools", "Cleaning tools", 120, 50, 18, 20, 120, 2, 3, layer="upper"),
                pocket("meter", "Light meter", 80, 45, 20, 165, 120, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.60,
        ),
    ),
    _pack(
        6,
        "electronics-soldering",
        "Electronics Soldering Pack",
        "Cooled iron, meter, desoldering tool, tips, cable, and consumables.",
        "Tools must be cold before storage; the example makes no ESD or electrical-safety claim.",
        _project(
            330, 230, 76,
            [
                pocket("iron", "Soldering iron", 235, 32, 28, 45, 45),
                pocket("desolder-pump", "Desoldering pump", 180, 30, 26, 140, 85),
                bay("multimeter", "Multimeter", 120, 80, 36, 45, 120),
                removable_bin("consumables-a", "Consumables A", 65, 50, 20, 20, 20, layer="upper"),
                removable_bin("consumables-b", "Consumables B", 65, 50, 20, 95, 20, layer="upper"),
                removable_bin("consumables-c", "Consumables C", 65, 50, 20, 170, 20, layer="upper"),
                divider("cables", "Cables", 130, 55, 20, 20, 105, 2, 3, layer="upper"),
                divider("tips", "Tips", 110, 55, 20, 175, 105, 2, 5, layer="upper"),
                circle("sponge", "Sponge cup", 50, 20, 255, 20, layer="upper"),
            ],
            containment="shared_panel", layers=True, ratio=0.62,
        ),
    ),
    _pack(
        7,
        "microcontroller-prototyping",
        "Microcontroller Prototyping Pack",
        "Breadboard, development boards, sensors, and jumpers.",
        "Printed plastic is not ESD-safe unless the finished material system is separately qualified.",
        _project(
            300, 210, 58,
            [
                bay("breadboard", "Breadboard", 170, 65, 18, 20, 20),
                removable_bin("jumper-bin", "Jumper bin", 85, 60, 22, 200, 20),
                pocket("devboard-a", "Development board A", 70, 42, 14, 20, 110),
                pocket("devboard-b", "Development board B", 70, 42, 14, 100, 110),
                pocket("devboard-c", "Development board C", 70, 42, 14, 180, 110),
                divider("sensors", "Sensor grid", 145, 42, 18, 20, 155, 1, 5),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        8,
        "bicycle-puncture-repair",
        "Bicycle Puncture Repair Pack",
        "Mini pump, levers, tube, patches, and cartridges.",
        "Check cartridge transport rules and all real component dimensions independently.",
        _project(
            280, 190, 56,
            [
                pocket("pump", "Mini pump", 210, 32, 30, 20, 20),
                divider("levers", "Tyre levers", 135, 42, 20, 20, 70, 1, 3),
                bay("tube", "Tube", 92, 72, 28, 170, 68),
                removable_bin("patch-bin", "Patch bin", 90, 42, 18, 20, 130),
                circle("cartridge-a", "Cartridge A", 22, 40, 125, 150),
                circle("cartridge-b", "Cartridge B", 22, 40, 160, 150),
                circle("cartridge-c", "Cartridge C", 22, 40, 195, 150),
            ],
            containment="shared_panel",
        ),
    ),
    _pack(
        9,
        "fly-tying",
        "Fly-Tying Bench Pack",
        "Compact vise, tying tools, threads, and materials.",
        "Hooks require closed containers; vise geometry and spool bores are unmeasured.",
        _project(
            300, 220, 60,
            [
                bay("vise", "Vise", 180, 45, 35, 20, 20),
                divider("tools", "Tying tools", 180, 55, 24, 20, 90, 1, 6),
                removable_bin("materials-a", "Materials A", 70, 50, 20, 215, 20),
                removable_bin("materials-b", "Materials B", 70, 50, 20, 215, 85),
                removable_bin("materials-c", "Materials C", 70, 50, 20, 215, 150),
                circle("thread-a", "Thread A", 34, 22, 20, 165),
                circle("thread-b", "Thread B", 34, 22, 62, 165),
                circle("thread-c", "Thread C", 34, 22, 104, 165),
                circle("thread-d", "Thread D", 34, 22, 146, 165),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        10,
        "tabletop-rpg",
        "Tabletop RPG Session Pack",
        "Dice sets, miniatures, tokens, and cards.",
        "Tall or fragile miniatures need measured bespoke pockets and padding.",
        _project(
            260, 190, 50,
            [
                removable_bin("dice-a", "Dice bin A", 50, 50, 18, 20, 20),
                removable_bin("dice-b", "Dice bin B", 50, 50, 18, 82, 20),
                removable_bin("dice-c", "Dice bin C", 50, 50, 18, 144, 20),
                removable_bin("dice-d", "Dice bin D", 50, 50, 18, 206, 20),
                circle("mini-a", "Miniature A", 42, 30, 20, 90),
                circle("mini-b", "Miniature B", 42, 30, 72, 90),
                circle("mini-c", "Miniature C", 42, 30, 124, 90),
                circle("mini-d", "Miniature D", 42, 30, 176, 90),
                divider("tokens", "Token grid", 90, 32, 18, 20, 145, 1, 4),
                bay("cards", "Card deck", 115, 32, 20, 125, 145),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        11,
        "board-game-tokens",
        "Board-Game Token Library",
        "Two card decks and six removable component trays.",
        "Component counts and sleeved-card dimensions vary by game.",
        _project(
            300, 220, 56,
            [
                bay("cards-a", "Card deck A", 110, 70, 24, 20, 20),
                bay("cards-b", "Card deck B", 110, 70, 24, 145, 20),
                removable_bin("tokens-a", "Token bin A", 75, 42, 20, 20, 110),
                removable_bin("tokens-b", "Token bin B", 75, 42, 20, 105, 110),
                removable_bin("tokens-c", "Token bin C", 75, 42, 20, 190, 110),
                removable_bin("tokens-d", "Token bin D", 75, 42, 20, 20, 165),
                removable_bin("tokens-e", "Token bin E", 75, 42, 20, 105, 165),
                removable_bin("tokens-f", "Token bin F", 75, 42, 20, 190, 165),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        12,
        "bead-jewellery",
        "Bead and Jewellery Work Pack",
        "Beads, findings, hand tools, and a small work mat.",
        "Bead retention depends on printed lid tolerance; print a coupon first.",
        _project(
            280, 200, 54,
            [
                *[
                    removable_bin(
                        f"beads-{row}-{column}",
                        f"Bead bin {row * 4 + column + 1}",
                        55, 42, 18,
                        (20, 85, 150, 215)[column],
                        (20, 75)[row],
                    )
                    for row in range(2)
                    for column in range(4)
                ],
                pocket("tool-tray", "Tool tray", 135, 32, 22, 20, 145),
                bay("bead-mat", "Bead mat", 90, 40, 12, 175, 140),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        13,
        "watch-strap-service",
        "Watch Strap Service Pack",
        "Watch cushion, spare straps, drivers, and spring bars.",
        "Not intended for exposed movements; all tool and cushion fits are assumptions.",
        _project(
            250, 175, 48,
            [
                circle("watch-cushion", "Watch cushion", 55, 25, 20, 20),
                pocket("strap-a", "Strap A", 145, 28, 16, 85, 20),
                pocket("strap-b", "Strap B", 145, 28, 16, 85, 60),
                divider("tools", "Service tools", 105, 45, 20, 20, 110, 1, 5),
                removable_bin("spring-bars", "Spring bars", 80, 42, 16, 150, 110),
            ],
            containment="shared_panel",
        ),
    ),
    _pack(
        14,
        "miniature-painting",
        "Miniature Painting Pack",
        "Paints, brushes, miniatures, palette, and rinse cup.",
        "Paint-pot height and leak resistance are unverified; miniatures need measured profiles.",
        _project(
            300, 220, 68,
            [
                *[
                    circle(
                        f"paint-{row}-{column}",
                        f"Paint {row * 6 + column + 1}",
                        35, 28,
                        (35, 80, 125, 170, 215, 260)[column],
                        (45, 95)[row],
                    )
                    for row in range(2)
                    for column in range(6)
                ],
                bay("palette", "Palette", 160, 45, 18, 70, 155),
                pocket("brushes", "Brushes", 200, 28, 18, 20, 20, layer="upper"),
                circle("mini-a", "Miniature A", 45, 22, 20, 80, layer="upper"),
                circle("mini-b", "Miniature B", 45, 22, 75, 80, layer="upper"),
                circle("mini-c", "Miniature C", 45, 22, 130, 80, layer="upper"),
                circle("mini-d", "Miniature D", 45, 22, 185, 80, layer="upper"),
                circle("rinse-cup", "Rinse cup", 50, 25, 235, 140, layer="upper"),
            ],
            containment="shared_panel", layers=True, ratio=0.55,
        ),
    ),
    _pack(
        15,
        "household-first-aid",
        "Household First-Aid Organizer",
        "Packaged dressings, wraps, tape, gloves, and a cold pack.",
        "Organizer only: it is not a sterile barrier, medical device, or expiry-control system.",
        _project(
            320, 230, 72,
            [
                bay("bandage-pack", "Bandage pack", 130, 85, 25, 45, 45),
                bay("wraps", "Wraps", 130, 60, 35, 45, 145),
                bay("cold-pack", "Cold pack", 100, 80, 20, 190, 70),
                removable_bin("supplies-a", "Supply bin A", 80, 55, 20, 20, 20, layer="upper"),
                removable_bin("supplies-b", "Supply bin B", 80, 55, 20, 115, 20, layer="upper"),
                removable_bin("supplies-c", "Supply bin C", 80, 55, 20, 210, 20, layer="upper"),
                divider("dressings", "Dressings", 140, 50, 18, 20, 110, 2, 3, layer="upper"),
                bay("gloves", "Gloves", 105, 50, 20, 180, 110, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.60,
        ),
    ),
    _pack(
        16,
        "travel-coffee",
        "Travel Coffee Station",
        "Hand grinder, brewer, scale, cup, coffee, and filters.",
        "Printed parts have no food-contact certification; contents must be clean and dry.",
        _project(
            330, 230, 76,
            [
                circle("grinder", "Hand grinder", 75, 38, 45, 60),
                circle("brewer", "Brewer", 95, 40, 150, 55),
                bay("scale", "Scale", 120, 70, 20, 95, 155),
                circle("cup", "Cup", 70, 35, 250, 90),
                removable_bin("coffee-bin", "Coffee bin", 100, 60, 22, 20, 20, layer="upper"),
                bay("filters", "Filters", 100, 60, 18, 135, 20, layer="upper"),
                divider("tools", "Coffee tools", 150, 45, 18, 20, 110, 1, 4, layer="upper"),
                pocket("spoon", "Spoon", 120, 25, 16, 185, 115, layer="upper"),
            ],
            containment="shared_panel", layers=True, ratio=0.62,
        ),
    ),
    _pack(
        17,
        "dry-bar-tools",
        "Dry Bar Tools Pack",
        "Empty shaker, mixing glass, jigger, spoon, strainer, and dry garnishes.",
        "Dry storage only; no liquid sealing or food-contact claim is made.",
        _project(
            340, 230, 74,
            [
                circle("shaker", "Shaker", 90, 38, 50, 60),
                circle("mixing-glass", "Mixing glass", 85, 36, 175, 60),
                circle("jigger", "Jigger", 45, 30, 275, 80),
                pocket("bar-spoon", "Bar spoon", 250, 22, 18, 45, 175),
                circle("strainer", "Strainer", 80, 20, 20, 20, layer="upper"),
                removable_bin("garnish-a", "Dry garnish A", 70, 55, 20, 120, 20, layer="upper"),
                removable_bin("garnish-b", "Dry garnish B", 70, 55, 20, 205, 20, layer="upper"),
                divider("picks", "Picks", 120, 45, 18, 20, 120, 1, 5, layer="upper"),
                bay("coasters", "Coasters", 100, 45, 18, 175, 120, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.58,
        ),
    ),
    _pack(
        18,
        "drone-field-repair",
        "Drone Field Repair Pack",
        "Propellers, drivers, fasteners, and protected battery containers.",
        "No loose lithium-battery cavity is proposed; use approved protective containers.",
        _project(
            320, 220, 60,
            [
                pocket("prop-a", "Propeller tray A", 135, 28, 14, 20, 20),
                pocket("prop-b", "Propeller tray B", 135, 28, 14, 20, 60),
                pocket("prop-c", "Propeller tray C", 135, 28, 14, 20, 100),
                pocket("prop-d", "Propeller tray D", 135, 28, 14, 20, 140),
                divider("drivers", "Drivers", 110, 45, 22, 180, 20, 1, 5),
                removable_bin("fasteners", "Fasteners", 95, 50, 18, 180, 75),
                bay("protected-pack-a", "Protected pack A", 55, 80, 30, 180, 128),
                bay("protected-pack-b", "Protected pack B", 55, 80, 30, 250, 128),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        19,
        "action-camera-creator",
        "Action Camera Creator Pack",
        "Cameras, battery carrier, mounts, cable, and grip.",
        "Batteries should remain in purpose-made carriers; real camera dimensions vary.",
        _project(
            280, 200, 58,
            [
                bay("camera-a", "Camera A", 70, 55, 28, 20, 20),
                bay("camera-b", "Camera B", 70, 55, 28, 105, 20),
                removable_bin("battery-carrier", "Battery carrier", 65, 50, 20, 195, 20),
                divider("mounts", "Mounts", 120, 55, 24, 20, 100, 2, 4),
                removable_bin("cable-bin", "Cable bin", 90, 50, 20, 160, 100),
                pocket("grip", "Grip", 210, 25, 24, 20, 165),
            ],
            containment="individual_lids",
        ),
    ),
    _pack(
        20,
        "field-geology",
        "Field Geology Pack",
        "Rock hammer, chisel, loupe, gauge, sample bins, and labels.",
        "The hammer point needs a sheath; loaded strength and carry retention are unverified.",
        _project(
            340, 240, 78,
            [
                pocket("rock-hammer", "Rock hammer", 220, 65, 32, 55, 55),
                pocket("chisel", "Chisel", 170, 30, 32, 55, 145),
                circle("loupe", "Loupe", 45, 22, 285, 65),
                pocket("field-gauge", "Field gauge", 110, 28, 16, 210, 185),
                removable_bin("sample-a", "Sample bin A", 65, 60, 22, 20, 20, layer="upper"),
                removable_bin("sample-b", "Sample bin B", 65, 60, 22, 95, 20, layer="upper"),
                removable_bin("sample-c", "Sample bin C", 65, 60, 22, 170, 20, layer="upper"),
                removable_bin("sample-d", "Sample bin D", 65, 60, 22, 245, 20, layer="upper"),
                divider("labels", "Labels", 150, 50, 18, 20, 115, 2, 5, layer="upper"),
                bay("sample-bags", "Sample bags", 120, 50, 18, 190, 115, layer="upper"),
            ],
            containment="shared_panel", layers=True, ratio=0.60,
        ),
    ),
    _pack(
        21,
        "tcg-deck-holder",
        "TCG Deck Holder Insert",
        "Three sleeved-deck bays with lidded tokens, dice, spares, and play aids.",
        "Deck sizes vary; printed plastic is not archival, waterproof, or crush-rated.",
        _project(
            310, 220, 74,
            [
                bay("deck-a", "Sleeved deck A", 82, 110, 34, 20, 30),
                bay("deck-b", "Sleeved deck B", 82, 110, 34, 115, 30),
                bay("deck-c", "Sleeved deck C", 82, 110, 34, 210, 30),
                removable_bin("tokens", "Token bin", 80, 55, 18, 20, 20, layer="upper"),
                removable_bin("dice", "Dice and counters", 80, 55, 18, 115, 20, layer="upper"),
                removable_bin("spares", "Sleeves and spares", 80, 55, 18, 210, 20, layer="upper"),
                divider("sideboard", "Sideboard divider", 130, 50, 16, 20, 100, 1, 4, layer="upper"),
                bay("play-aids", "Play aids", 120, 50, 16, 170, 100, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.58,
        ),
    ),
    _pack(
        22,
        "portable-mesh-radio-node",
        "Portable Mesh-Radio Node Insert",
        "Generic enclosed radio node, protected power module, antennas, cables, and field spares.",
        "Unofficial generic layout; no specific device fit is claimed. Use only protected, enclosed power modules and validate charging, thermal, weather, connector, and RF clearances separately.",
        _project(
            280, 200, 68,
            [
                bay("node", "Portable mesh node", 120, 75, 32, 25, 35),
                bay("protected-power", "Protected power module", 80, 55, 30, 175, 35),
                pocket("antenna", "Antenna", 190, 24, 18, 45, 155),
                pocket("coax", "Short coax lead", 60, 35, 18, 205, 110),
                removable_bin("adapters", "Adapter bin", 70, 50, 18, 20, 20, layer="upper"),
                removable_bin("cables", "Cable bin", 90, 50, 18, 105, 20, layer="upper"),
                removable_bin("mounts", "Mount hardware", 55, 50, 18, 210, 20, layer="upper"),
                divider("field-spares", "Field spares", 120, 45, 16, 20, 95, 1, 4, layer="upper"),
                bay("instructions", "Map and instructions", 120, 45, 12, 150, 95, layer="upper"),
            ],
            containment="individual_lids", layers=True, ratio=0.60,
        ),
    ),
    _pack(
        23,
        "six-pack-beer",
        "Six-Pack Beer Organizer Insert",
        "Six deep circular pockets for sealed beverage cans plus a bottle-opener bay.",
        "Synthetic dimensions only; no food-contact, insulation, leakproofing, impact, or loaded-carry claim is made. Test retention with inert cylinders, keep pressurized cans away from heat and puncture hazards, and do not use glass without separate breakage testing.",
        _project(
            330, 235, 155,
            [
                *[
                    circle(
                        f"can-{row}-{column}",
                        f"Sealed can {row * 3 + column + 1}",
                        70, 125,
                        (20, 105, 190)[column],
                        (30, 135)[row],
                    )
                    for row in range(2)
                    for column in range(3)
                ],
                pocket("opener", "Bottle opener", 28, 130, 20, 285, 45),
            ],
            containment="shared_panel",
        ),
    ),
)


def themed_packs() -> list[dict[str, Any]]:
    """Return a defensive copy of the stable 23-pack catalog."""

    return deepcopy(list(THEMED_PACKS))
