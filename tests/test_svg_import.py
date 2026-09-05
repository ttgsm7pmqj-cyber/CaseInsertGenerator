# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure unit tests for fail-closed SVG preflight and normalization."""

from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

from freecad.CaseInsertGenerator.svg_import import (
    PX_TO_MM,
    SvgPreflightError,
    apply_matrix,
    parse_length_mm,
    path_winding_signs,
    preflight_svg,
    preflight_svg_file,
)


def svg(body: str, root_attributes: str = 'width="100mm" height="50mm" viewBox="0 0 100 50"') -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" {root_attributes}>{body}</svg>'


def diagnostic_codes(result) -> set[str]:
    return {item.code for item in result.diagnostics}


class LengthTests(unittest.TestCase):
    def test_absolute_units_are_normalized_at_96_dpi(self) -> None:
        expected = {
            "1mm": 1.0,
            "1cm": 10.0,
            "1in": 25.4,
            "72pt": 25.4,
            "6pc": 25.4,
            "96px": 25.4,
            "96": 25.4,
        }
        for raw, millimetres in expected.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(parse_length_mm(raw), millimetres)

    def test_relative_and_unknown_units_are_rejected(self) -> None:
        for raw in ("50%", "1em", "12q", "auto", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_length_mm(raw)

    def test_nonfinite_lengths_and_unit_conversion_overflow_are_rejected(self) -> None:
        for raw in ("1e309mm", "-1e309mm", "1e308in"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "finite"):
                parse_length_mm(raw)


class ViewportTests(unittest.TestCase):
    def test_viewbox_none_maps_axes_independently_to_millimetres(self) -> None:
        result = preflight_svg(
            svg(
                '<rect width="10" height="10"/>',
                'width="200mm" height="50mm" viewBox="10 20 100 100" preserveAspectRatio="none"',
            )
        )
        self.assertTrue(result.is_importable)
        matrix = result.metadata.viewport.user_to_mm
        self.assertEqual(apply_matrix(matrix, 10, 20), (0.0, 0.0))
        self.assertEqual(apply_matrix(matrix, 110, 120), (200.0, 50.0))

    def test_default_meet_centres_viewbox(self) -> None:
        result = preflight_svg(
            svg(
                '<rect width="10" height="10"/>',
                'width="200mm" height="100mm" viewBox="0 0 100 100"',
            )
        )
        self.assertTrue(result.is_importable)
        matrix = result.metadata.viewport.user_to_mm
        self.assertEqual(apply_matrix(matrix, 0, 0), (50.0, 0.0))
        self.assertEqual(apply_matrix(matrix, 100, 100), (150.0, 100.0))

    def test_missing_dimensions_are_derived_from_viewbox_at_css_dpi(self) -> None:
        result = preflight_svg(svg('<circle r="5"/>', 'viewBox="0 0 96 48"'))
        self.assertTrue(result.is_importable)
        self.assertAlmostEqual(result.metadata.viewport.width_mm, 25.4)
        self.assertAlmostEqual(result.metadata.viewport.height_mm, 12.7)
        self.assertIn("VIEWPORT_DERIVED_FROM_VIEWBOX", diagnostic_codes(result))

    def test_missing_viewbox_and_dimensions_is_fatal(self) -> None:
        result = preflight_svg(svg('<circle r="5"/>', ""))
        self.assertFalse(result.is_importable)
        self.assertIn("MISSING_VIEWPORT", diagnostic_codes(result))

    def test_percent_viewport_is_fatal(self) -> None:
        result = preflight_svg(svg('<rect width="5" height="5"/>', 'width="100%" height="50mm"'))
        self.assertFalse(result.is_importable)
        self.assertIn("PERCENT_VIEWPORT_UNSUPPORTED", diagnostic_codes(result))


class TransformTests(unittest.TestCase):
    def test_transform_and_viewport_overflow_are_fatal(self) -> None:
        sources = (
            svg('<rect width="5" height="5" transform="scale(1e309)"/>'),
            svg('<rect width="5" height="5" transform="scale(1e200) scale(1e200)"/>'),
            svg('<g transform="scale(1e200)"><rect width="5" height="5" transform="scale(1e200)"/></g>'),
            svg('<rect width="5" height="5"/>', 'width="1e200mm" height="50mm" viewBox="0 0 1e-200 50" preserveAspectRatio="none"'),
        )
        for source in sources:
            with self.subTest(source=source):
                result = preflight_svg(source)
                self.assertFalse(result.is_importable)
                self.assertTrue(any("finite" in item.message for item in result.fatal_diagnostics))

    def test_nested_transforms_are_detected_and_normalized(self) -> None:
        result = preflight_svg(
            svg(
                '<g transform="translate(10 5)">'
                '<path id="pocket" transform="scale(2)" d="M0 0 L10 0 L10 5 L0 5 Z"/>'
                "</g>"
            )
        )
        self.assertTrue(result.is_importable)
        self.assertTrue(result.metadata.has_nested_transforms)
        self.assertEqual(result.metadata.nested_transform_paths, ("/svg[1]/g[1]/path[1]",))
        candidate = result.candidates[0]
        self.assertEqual(candidate.transform_depth, 2)
        x_mm, y_mm = apply_matrix(candidate.local_to_mm, 10, 5)
        self.assertAlmostEqual(x_mm, 30.0)
        self.assertAlmostEqual(y_mm, 15.0)

    def test_rotate_about_point_is_supported(self) -> None:
        result = preflight_svg(
            svg('<rect width="5" height="2" transform="rotate(90 10 10)"/>')
        )
        self.assertTrue(result.is_importable)
        x_mm, y_mm = apply_matrix(result.candidates[0].local_to_mm, 11, 10)
        self.assertAlmostEqual(x_mm, 10.0)
        self.assertAlmostEqual(y_mm, 11.0)

    def test_malformed_transform_blocks_the_document(self) -> None:
        result = preflight_svg(svg('<rect width="5" height="5" transform="translate(nope)"/>'))
        self.assertFalse(result.is_importable)
        self.assertIn("INVALID_TRANSFORM", diagnostic_codes(result))


class GeometryTests(unittest.TestCase):
    def test_important_effect_wins_over_a_later_normal_declaration(self) -> None:
        for effect in ("clip-path", "mask", "filter"):
            for marker in ("!important", "! IMPORTANT"):
                source = svg(
                    f'<rect width="20" height="10" '
                    f'style="{effect}:url(#half) {marker}; {effect}:none"/>'
                )
                with self.subTest(effect=effect, marker=marker):
                    result = preflight_svg(source)
                    self.assertFalse(result.is_importable)
                    self.assertIn("UNSUPPORTED_GEOMETRY_EFFECT", diagnostic_codes(result))

    def test_important_none_is_valid_and_obeys_same_priority_source_order(self) -> None:
        for effect in ("clip-path", "mask", "filter"):
            for declarations, importable in (
                (f"{effect}:none !important", True),
                (f"{effect}:none !important; {effect}:url(#half)", True),
                (f"{effect}:url(#half) !important; {effect}:none !important", True),
                (f"{effect}:none !important; {effect}:url(#half) !important", False),
            ):
                source = svg(f'<rect width="20" height="10" style="{declarations}"/>')
                with self.subTest(declarations=declarations):
                    result = preflight_svg(source)
                    self.assertEqual(result.is_importable, importable, result.to_dict())
                    self.assertEqual(
                        "UNSUPPORTED_GEOMETRY_EFFECT" in diagnostic_codes(result),
                        not importable,
                    )

    def test_effects_on_visible_geometry_and_ancestors_are_rejected(self) -> None:
        for effect in ("clip-path", "mask", "filter"):
            for body in (
                f'<rect width="20" height="10" {effect}="url(#effect)"/>',
                f'<rect width="20" height="10" style="{effect}:url(#effect)"/>',
                f'<g {effect}="url(#effect)"><rect width="20" height="10"/></g>',
                f'<g style="{effect}:url(#effect)"><g><rect width="20" height="10"/></g></g>',
                f'<g {effect}="url(#effect)"><rect width="20" height="10" style="{effect}:none"/></g>',
                f'<g visibility="hidden" {effect}="url(#effect)"><rect width="20" height="10" visibility="visible"/></g>',
            ):
                with self.subTest(effect=effect, body=body):
                    result = preflight_svg(svg(body))
                    self.assertFalse(result.is_importable)
                    self.assertIn("UNSUPPORTED_GEOMETRY_EFFECT", diagnostic_codes(result))
                    with self.assertRaisesRegex(SvgPreflightError, "flatten"):
                        result.require_importable()

    def test_root_effect_and_case_insensitive_css_property_are_rejected(self) -> None:
        result = preflight_svg(svg(
            '<rect width="20" height="10"/>',
            'width="100mm" height="50mm" style="CLIP-PATH:url(#effect)"',
        ))
        self.assertIn("UNSUPPORTED_GEOMETRY_EFFECT", diagnostic_codes(result))

    def test_unused_hidden_and_explicitly_disabled_effects_do_not_block(self) -> None:
        for body in (
            '<defs><g clip-path="url(#unused)"><rect width="20" height="10"/></g></defs>',
            '<g display="none" clip-path="url(#unused)"><rect width="20" height="10"/></g>',
            '<g opacity="0" filter="url(#unused)"><rect width="20" height="10"/></g>',
            '<g visibility="hidden" mask="url(#unused)"><rect width="20" height="10"/></g>',
            '<rect width="20" height="10" clip-path="url(#unused)" style="clip-path:none"/>',
            '<rect width="20" height="10" clip-path="none" mask="none" filter="none"/>',
        ):
            with self.subTest(body=body):
                result = preflight_svg(svg(body + '<rect width="5" height="5"/>'))
                self.assertTrue(result.is_importable, result.to_dict())

    def test_nonfinite_path_values_and_relative_overflow_are_rejected(self) -> None:
        for data in (
            'M0 0 L1e309 0 L10 10 Z',
            'M0 0 C1e309 0 10 10 0 10 Z',
            'M0 0 A10 10 -1e309 0 0 10 10 Z',
            'M1e308 0 l1e308 0 l0 10 Z',
        ):
            with self.subTest(data=data):
                result = preflight_svg(svg(f'<path d="{data}"/>'))
                self.assertFalse(result.is_importable)
                self.assertIn("MALFORMED_PATH_DATA", diagnostic_codes(result))
                self.assertTrue(any("finite" in item.message for item in result.fatal_diagnostics))

    def test_optional_shape_coordinates_and_radii_must_be_finite(self) -> None:
        for tag, required, names in (
            ("rect", 'width="20" height="10"', ("x", "y", "rx", "ry")),
            ("circle", 'r="5"', ("cx", "cy")),
            ("ellipse", 'rx="5" ry="3"', ("cx", "cy")),
        ):
            for name in names:
                with self.subTest(tag=tag, name=name):
                    result = preflight_svg(svg(f'<{tag} {required} {name}="1e309"/>'))
                    self.assertFalse(result.is_importable)
                    self.assertIn("INVALID_GEOMETRY_VALUE", diagnostic_codes(result))

    def test_linear_compound_path_preserves_source_winding(self) -> None:
        same = "M0 0 H40 V40 H0 Z M10 10 H30 V30 H10 Z"
        opposite = "M0 0 H40 V40 H0 Z M10 10 V30 H30 V10 Z"

        self.assertEqual(path_winding_signs(same), (1, 1))
        self.assertEqual(path_winding_signs(opposite), (1, -1))

    def test_curved_nonzero_compound_winding_fails_actionably(self) -> None:
        with self.assertRaisesRegex(ValueError, "fill-rule=evenodd"):
            path_winding_signs(
                "M0 0 C10 0 10 10 20 10 Z M5 5 H10 V10 H5 Z")

    def test_supported_closed_geometry_becomes_candidates(self) -> None:
        result = preflight_svg(
            svg(
                '<path id="path" d="M0 0 L10 0 L10 10 L0 10 Z"/>'
                '<rect id="rect" width="10" height="5"/>'
                '<circle id="circle" r="4"/>'
                '<ellipse id="ellipse" rx="4" ry="2"/>'
                '<polygon id="polygon" points="0,0 8,0 4,6"/>'
            )
        )
        self.assertTrue(result.is_importable)
        self.assertEqual([candidate.tag for candidate in result.candidates], [
            "path",
            "rect",
            "circle",
            "ellipse",
            "polygon",
        ])
        self.assertEqual(result.metadata.candidate_count, 5)

    def test_compound_path_holes_and_disjoint_contours_are_retained(self) -> None:
        result = preflight_svg(
            svg(
                '<path fill-rule="evenodd" d="'
                'M0 0 H40 V40 H0 Z '
                'M10 10 H30 V30 H10 Z '
                'M50 0 H60 V10 H50 Z"/>'
            )
        )
        self.assertTrue(result.is_importable)
        self.assertEqual(result.candidates[0].subpath_count, 3)
        self.assertEqual(result.candidates[0].fill_rule, "evenodd")

    def test_fill_rule_is_inherited_from_a_group(self) -> None:
        result = preflight_svg(
            svg('<g fill-rule="evenodd"><path d="M0 0 H20 V20 H0 Z M5 5 H15 V15 H5 Z"/></g>')
        )
        self.assertTrue(result.is_importable)
        self.assertEqual(result.candidates[0].fill_rule, "evenodd")

    def test_open_path_and_polyline_are_actionable_fatal_findings(self) -> None:
        for body in (
            '<path d="M0 0 L10 0 L10 10"/>',
            '<polyline points="0,0 10,0 10,10" fill="none" stroke="black"/>',
            '<line x1="0" y1="0" x2="10" y2="10"/>',
        ):
            with self.subTest(body=body):
                result = preflight_svg(svg(body))
                self.assertFalse(result.is_importable)
                self.assertTrue(
                    {"OPEN_PATH", "STROKE_ONLY_GEOMETRY"} & diagnostic_codes(result)
                )

    def test_stroke_only_artwork_is_never_silently_imported(self) -> None:
        result = preflight_svg(
            svg('<path fill="none" stroke="black" d="M0 0 L10 0 L10 10 Z"/>')
        )
        self.assertFalse(result.is_importable)
        self.assertIn("STROKE_ONLY_GEOMETRY", diagnostic_codes(result))
        self.assertEqual(result.candidates, ())

    def test_malformed_path_commands_and_arc_flags_are_fatal(self) -> None:
        cases = (
            '<path d="M0 0 L10"/>',
            '<path d="M0 0 A10 10 0 2 0 20 20 Z"/>',
            '<path d="M0 0 R10 10 Z"/>',
        )
        for body in cases:
            with self.subTest(body=body):
                result = preflight_svg(svg(body))
                self.assertFalse(result.is_importable)
                self.assertIn("MALFORMED_PATH_DATA", diagnostic_codes(result))

    def test_self_intersecting_linear_path_and_polygon_are_fatal(self) -> None:
        cases = (
            '<path d="M0 0 L10 10 L0 10 L10 0 Z"/>',
            '<polygon points="0,0 10,10 0,10 10,0"/>',
        )
        for body in cases:
            with self.subTest(body=body):
                result = preflight_svg(svg(body))
                self.assertFalse(result.is_importable)
                self.assertIn("SELF_INTERSECTING_GEOMETRY", diagnostic_codes(result))

    def test_polygon_may_repeat_its_first_point_at_the_end(self) -> None:
        result = preflight_svg(svg('<polygon points="0,0 10,0 10,10 0,10 0,0"/>'))
        self.assertTrue(result.is_importable)

    def test_zero_size_primitives_are_fatal(self) -> None:
        for body in ('<rect width="0" height="5"/>', '<circle r="0"/>', '<ellipse rx="1" ry="0"/>'):
            with self.subTest(body=body):
                result = preflight_svg(svg(body))
                self.assertFalse(result.is_importable)
                self.assertIn("ZERO_AREA_GEOMETRY", diagnostic_codes(result))

    def test_hidden_geometry_is_explicitly_reported_but_safe(self) -> None:
        result = preflight_svg(
            svg('<g display="none"><rect width="20" height="20"/></g><circle r="4"/>')
        )
        self.assertTrue(result.is_importable)
        self.assertEqual(len(result.candidates), 1)
        self.assertIn("HIDDEN_GEOMETRY_IGNORED", diagnostic_codes(result))


class UnsupportedContentTests(unittest.TestCase):
    def test_visible_text_image_and_use_each_block_the_whole_document(self) -> None:
        unsupported = {
            '<text x="0" y="10">Label</text>': "UNSUPPORTED_TEXT",
            '<image href="part.png" width="10" height="10"/>': "UNSUPPORTED_IMAGE",
            '<use href="#shape"/>': "UNSUPPORTED_USE",
        }
        for extra, expected_code in unsupported.items():
            with self.subTest(extra=extra):
                result = preflight_svg(svg('<rect width="10" height="10"/>' + extra))
                self.assertFalse(result.is_importable)
                self.assertEqual(len(result.candidates), 1)
                self.assertIn(expected_code, diagnostic_codes(result))
                with self.assertRaises(SvgPreflightError):
                    result.require_importable()

    def test_embedded_stylesheet_is_fatal(self) -> None:
        result = preflight_svg(
            svg('<style>.pocket { fill: black; }</style><path class="pocket" d="M0 0 H10 V10 H0 Z"/>')
        )
        self.assertFalse(result.is_importable)
        self.assertIn("STYLESHEET_UNSUPPORTED", diagnostic_codes(result))

    def test_clip_mask_or_filter_requires_flattening(self) -> None:
        result = preflight_svg(
            svg('<rect width="10" height="10" clip-path="url(#clip)"/>')
        )
        self.assertFalse(result.is_importable)
        self.assertIn("UNSUPPORTED_GEOMETRY_EFFECT", diagnostic_codes(result))

    def test_common_editor_metadata_is_ignored_explicitly(self) -> None:
        result = preflight_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'width="20mm" height="20mm" viewBox="0 0 20 20">'
            '<sodipodi:namedview/><metadata><rdf:RDF/></metadata>'
            '<rect width="10" height="10"/></svg>'
        )
        self.assertTrue(result.is_importable)
        self.assertIn("FOREIGN_METADATA_IGNORED", diagnostic_codes(result))


class XmlSafetyAndContractTests(unittest.TestCase):
    def test_doctype_and_entity_declarations_are_rejected_before_xml_parse(self) -> None:
        source = (
            '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            '<text>&xxe;</text></svg>'
        )
        result = preflight_svg(source)
        self.assertFalse(result.is_importable)
        self.assertIn("UNSAFE_XML_DECLARATION", diagnostic_codes(result))

    def test_utf16_doctype_and_external_stylesheet_are_rejected(self) -> None:
        utf16 = (
            '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        ).encode("utf-16")
        self.assertIn("UNSAFE_XML_DECLARATION", diagnostic_codes(preflight_svg(utf16)))
        stylesheet = (
            '<?xml-stylesheet href="https://example.test/shape.css"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            '<rect width="5" height="5"/></svg>'
        )
        self.assertIn("UNSAFE_XML_DECLARATION", diagnostic_codes(preflight_svg(stylesheet)))

    def test_excessive_element_nesting_is_fatal(self) -> None:
        result = preflight_svg(svg("<g>" * 130 + '<rect width="1" height="1"/>' + "</g>" * 130))
        self.assertFalse(result.is_importable)
        self.assertIn("SVG_NESTING_TOO_DEEP", diagnostic_codes(result))

    def test_malformed_xml_and_non_svg_roots_are_fatal_results(self) -> None:
        malformed = preflight_svg("<svg><path></svg>")
        self.assertIn("MALFORMED_XML", diagnostic_codes(malformed))
        wrong_root = preflight_svg("<html></html>")
        self.assertIn("NOT_AN_SVG_ROOT", diagnostic_codes(wrong_root))

    def test_size_limit_fails_without_parsing(self) -> None:
        result = preflight_svg(svg('<rect width="1" height="1"/>'), max_bytes=10)
        self.assertFalse(result.is_importable)
        self.assertIn("SVG_TOO_LARGE", diagnostic_codes(result))

    def test_file_entrypoint_and_json_ready_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shape.svg"
            path.write_text(svg('<rect id="box" width="10" height="10"/>'), encoding="utf-8")
            result = preflight_svg_file(path)
        self.assertTrue(result.is_importable)
        payload = result.to_dict()
        self.assertEqual(payload["source_name"], str(path))
        self.assertEqual(payload["candidates"][0]["element_id"], "box")
        self.assertAlmostEqual(payload["metadata"]["viewport"]["width_mm"], 100.0)

    def test_missing_file_returns_an_actionable_fatal_result(self) -> None:
        result = preflight_svg_file("/definitely/not/a/real/file.svg")
        self.assertFalse(result.is_importable)
        self.assertIn("SVG_READ_ERROR", diagnostic_codes(result))

    def test_no_geometry_is_fatal(self) -> None:
        result = preflight_svg(svg("<title>Empty drawing</title>"))
        self.assertFalse(result.is_importable)
        self.assertIn("NO_IMPORTABLE_GEOMETRY", diagnostic_codes(result))

    def test_result_reports_fatal_and_warning_separately(self) -> None:
        result = preflight_svg(
            svg(
                '<g display="none"><rect width="2" height="2"/></g>'
                '<path d="M0 0 L10 0"/>'
            )
        )
        self.assertGreaterEqual(len(result.fatal_diagnostics), 1)
        self.assertGreaterEqual(len(result.warning_diagnostics), 1)
        self.assertTrue(all(item.severity == "fatal" for item in result.fatal_diagnostics))
        self.assertTrue(all(item.severity == "warning" for item in result.warning_diagnostics))


if __name__ == "__main__":
    unittest.main()
