# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import unittest

from scripts.artifact_audit import scan_fcstd


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lid-panel"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_size(path):
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG")
    return struct.unpack(">II", data[16:24])


class LidPanelExampleTests(unittest.TestCase):
    def test_synthetic_assembled_and_exploded_example_is_complete_and_unverified(self):
        manifest_path = EXAMPLE / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["ok"])
        self.assertTrue(manifest["rendered"])
        self.assertEqual(manifest["geometry_provenance"], "synthetic-demonstration")
        self.assertEqual(manifest["compatibility_claim"], "none")
        self.assertEqual(manifest["physical_fit_status"], "unverified")
        self.assertEqual(manifest["panel_pattern"], "slot_grid")
        self.assertGreater(manifest["pattern_count"], 0)
        self.assertTrue(manifest["height_budget"]["printable"])
        self.assertGreaterEqual(manifest["printable_parts"], 2)

        for presentation in ("assembled", "exploded"):
            fcstd_record = manifest[presentation]["fcstd"]
            fcstd = EXAMPLE / fcstd_record["path"]
            self.assertTrue(fcstd.is_file())
            self.assertEqual(_sha256(fcstd), fcstd_record["sha256"])
            self.assertEqual(scan_fcstd(fcstd), {"ok": True, "findings": []})
            png_record = manifest[presentation]["png"]
            png = EXAMPLE / png_record["path"]
            self.assertTrue(png.is_file())
            self.assertEqual(_sha256(png), png_record["sha256"])
            self.assertEqual(_png_size(png), (1400, 1000))
            self.assertGreater(png.stat().st_size, 20_000)

        self.assertNotEqual(
            manifest["assembled"]["fcstd"]["sha256"],
            manifest["exploded"]["fcstd"]["sha256"],
        )
        self.assertNotEqual(
            manifest["assembled"]["png"]["sha256"],
            manifest["exploded"]["png"]["sha256"],
        )

        spec_path = EXAMPLE / manifest["spec"]["path"]
        self.assertEqual(_sha256(spec_path), manifest["spec"]["sha256"])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(spec["case"]["case_model"], "Custom Case")
        self.assertEqual(spec["case"]["compatibility_claim"], "none")
        self.assertFalse(spec["verification"]["physical_fit"])
        self.assertEqual(spec["verification"]["status"], "physical-fit unverified")

        controls_png = EXAMPLE / "lid-panel-controls-unknown-clearance.png"
        self.assertTrue(controls_png.is_file())
        self.assertEqual(_png_size(controls_png), (2080, 1800))
        self.assertGreater(controls_png.stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
