# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from freecad.CaseInsertGenerator.project_model import layout_project, validate_project
from scripts.artifact_audit import scan_fcstd, text_findings
from scripts.themed_example_catalog import themed_packs


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = ROOT / "examples" / "themed-packs"
EXPECTED_PACK_COUNT = 23


def _sha256(target):
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ThemedExampleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.packs = themed_packs()

    def test_catalog_has_exactly_twenty_three_stable_unique_examples(self):
        self.assertEqual(len(self.packs), EXPECTED_PACK_COUNT)
        self.assertEqual(
            [pack["number"] for pack in self.packs],
            list(range(1, EXPECTED_PACK_COUNT + 1)),
        )
        for field in ("id", "slug", "title"):
            values = [pack[field] for pack in self.packs]
            self.assertEqual(len(values), len(set(values)), field)

    def test_every_example_is_synthetic_generic_and_physically_unverified(self):
        for pack in self.packs:
            with self.subTest(pack=pack["slug"]):
                project = pack["project"]
                self.assertEqual(pack["geometry_provenance"], "synthetic-demonstration")
                self.assertEqual(pack["physical_fit_status"], "unverified")
                self.assertEqual(project["case"]["case_model"], "Custom Case")
                self.assertEqual(
                    project["case"]["geometry_provenance"],
                    "synthetic-demonstration",
                )
                self.assertEqual(project["case"]["compatibility_claim"], "none")
                self.assertEqual(project["lid"], {"source": "unknown", "clearance_mm": None})

    def test_catalog_contains_no_compatibility_claims_or_external_asset_paths(self):
        serialized = json.dumps(self.packs, sort_keys=True).lower()
        self.assertNotIn('"compatibility_claim": "compatible"', serialized)
        for pack in self.packs:
            for item in pack["project"]["objects"]:
                self.assertNotEqual(item["type"], "svg_pocket")
                self.assertNotIn("svg_path", item)

    def test_every_project_validates_and_locked_layout_places_every_object(self):
        for pack in self.packs:
            with self.subTest(pack=pack["slug"]):
                project = validate_project(pack["project"])
                self.assertTrue(all(item["locked"] for item in project["objects"]))
                result = layout_project(project, "balanced")
                self.assertEqual(result.unplaced, ())
                self.assertEqual(result.placed_count, len(project["objects"]))

    def test_examples_exercise_the_portable_v1_feature_set(self):
        object_types = {
            item["type"]
            for pack in self.packs
            for item in pack["project"]["objects"]
        }
        self.assertEqual(
            object_types,
            {
                "circular_pocket",
                "rectangular_pocket",
                "removable_bin",
                "existing_container_bay",
                "divider_region",
            },
        )
        self.assertGreaterEqual(
            sum(pack["project"]["layers"]["enabled"] for pack in self.packs),
            5,
        )
        containment_modes = {
            pack["project"]["containment"]["mode"] for pack in self.packs
        }
        self.assertEqual(containment_modes, {"shared_panel", "individual_lids"})

    def test_requested_tcg_mesh_node_and_six_pack_examples_are_explicit(self):
        by_slug = {pack["slug"]: pack for pack in self.packs}
        requested = {
            "tcg-deck-holder": "TCG Deck Holder Insert",
            "portable-mesh-radio-node": "Portable Mesh-Radio Node Insert",
            "six-pack-beer": "Six-Pack Beer Organizer Insert",
        }
        self.assertEqual(
            {slug: by_slug[slug]["title"] for slug in requested},
            requested,
        )
        tcg = by_slug["tcg-deck-holder"]["project"]
        self.assertTrue(tcg["layers"]["enabled"])
        self.assertEqual(len(tcg["objects"]), 8)
        mesh = by_slug["portable-mesh-radio-node"]["project"]
        self.assertTrue(mesh["layers"]["enabled"])
        self.assertIn("protected-power", {item["id"] for item in mesh["objects"]})
        beer = by_slug["six-pack-beer"]["project"]
        self.assertEqual(beer["containment"]["mode"], "shared_panel")
        self.assertEqual(
            sum(item["type"] == "circular_pocket" for item in beer["objects"]),
            6,
        )

    def test_generated_bundle_has_twenty_three_audited_assembled_and_exploded_examples(self):
        manifest_path = EXAMPLE_ROOT / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["ok"])
        self.assertTrue(manifest["render_requested"])
        self.assertTrue(manifest["source_inputs_tracked"])
        self.assertFalse(manifest["tracked_worktree_dirty"])
        self.assertTrue(manifest["source_commit_verified"])
        self.assertEqual(manifest["compatibility_claim"], "none")
        self.assertEqual(
            manifest["summary"],
            {
                "exploded_models": EXPECTED_PACK_COUNT,
                "exploded_rendered": EXPECTED_PACK_COUNT,
                "failed": 0,
                "passed": EXPECTED_PACK_COUNT,
                "rendered": EXPECTED_PACK_COUNT,
                "total": EXPECTED_PACK_COUNT,
            },
        )
        for relative, expected_hash in manifest["source_sha256"].items():
            self.assertEqual(_sha256(ROOT / relative), expected_hash, relative)
        for sheet_key in ("contact_sheet", "exploded_contact_sheet"):
            record = manifest[sheet_key]
            target = EXAMPLE_ROOT / record["path"]
            self.assertTrue(target.is_file())
            self.assertEqual(_sha256(target), record["sha256"])
            self.assertEqual(target.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(record["cells"], EXPECTED_PACK_COUNT)
            self.assertEqual(record["height"], 1890)
            self.assertEqual(record["layout"], "4 columns x 6 rows")

        hashed_artifacts = 0
        for entry in manifest["examples"]:
            with self.subTest(pack=entry["slug"]):
                self.assertEqual(entry["status"], "pass")
                for key in (
                    "fcstd", "exploded_fcstd", "render", "exploded_render", "spec"
                ):
                    record = entry[key]
                    target = EXAMPLE_ROOT / record["path"]
                    self.assertTrue(target.is_file(), key)
                    self.assertEqual(_sha256(target), record["sha256"], key)
                    hashed_artifacts += 1
                self.assertNotEqual(
                    entry["render"]["sha256"],
                    entry["exploded_render"]["sha256"],
                )
                difference = entry["render_pair_difference"]
                self.assertGreaterEqual(
                    difference["changed_ratio"], difference["minimum_changed_ratio"]
                )
                for key in ("fcstd", "exploded_fcstd"):
                    audit = entry[key]["portable_source_scan"]
                    self.assertEqual(audit, {"findings": [], "ok": True})
                    self.assertEqual(
                        scan_fcstd(EXAMPLE_ROOT / entry[key]["path"]), audit
                    )
                layout = entry["exploded_fcstd"]["layout"]
                reopen = entry["exploded_fcstd"]["reopen_audit"]
                self.assertEqual(layout["preview_part_count"], entry["part_count"])
                self.assertGreaterEqual(
                    layout["minimum_observed_gap_mm"], layout["gap_mm"]
                )
                self.assertTrue(reopen["ok"])
                self.assertEqual(reopen["placement_checks"], entry["part_count"])
                self.assertEqual(reopen["preview_count"], entry["part_count"])
                self.assertEqual(reopen["source_map_count"], entry["part_count"])
                self.assertEqual(reopen["visibility_checks"], entry["part_count"])
        self.assertEqual(hashed_artifacts, EXPECTED_PACK_COUNT * 5)


class GeneratedArtifactAuditTests(unittest.TestCase):
    def _scan_member(self, content):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "example.FCStd"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("Document.xml", content)
                archive.writestr("GuiDocument.xml", "<gui/>")
            return scan_fcstd(target)

    def test_clean_generated_text_passes(self):
        result = self._scan_member(
            '<project provenance="synthetic-demonstration" fit="unverified"/>'
        )
        self.assertEqual(result, {"ok": True, "findings": []})

    def test_local_paths_file_uris_and_restricted_markers_fail_without_leaking_paths(self):
        fixtures = {
            "unix": "/Users/example/private/source.FCStd",
            "linux": "/home/example/private/source.FCStd",
            "file_uri": "file:///Users/example/private/source.svg",
            "windows": "C:\\Users\\example\\private\\source.FCStd",
            "marker": "restricted-" + "cad",
        }
        for label, content in fixtures.items():
            with self.subTest(label=label):
                result = self._scan_member(content)
                self.assertFalse(result["ok"])
                self.assertTrue(result["findings"])
                self.assertNotIn("example", json.dumps(result).lower())

    def test_plain_text_finding_records_are_machine_readable(self):
        marker = "license-" + "required"
        findings = text_findings(marker, "project.json")
        self.assertEqual(
            findings,
            [{
                "member": "project.json",
                "kind": "prohibited-source-marker",
                "marker": marker,
            }],
        )


if __name__ == "__main__":
    unittest.main()
