# SPDX-License-Identifier: LGPL-2.1-or-later
"""Internal FreeCAD licence metadata must agree with the example assignment."""

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from scripts.artifact_audit import EXAMPLE_LICENSE, EXAMPLE_LICENSE_URL, scan_fcstd
from scripts.release_audit import _scan_fcstd


class ExampleLicenceTests(unittest.TestCase):
    def _check(self, license_name=EXAMPLE_LICENSE, license_url=EXAMPLE_LICENSE_URL):
        document = ET.Element("Document")
        properties = ET.SubElement(document, "Properties")
        for name, value in (("License", license_name), ("LicenseURL", license_url)):
            if value is not None:
                prop = ET.SubElement(properties, "Property", name=name)
                ET.SubElement(prop, "String", value=value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.FCStd"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Document.xml", ET.tostring(document))
            source_scan = scan_fcstd(path, require_example_license=True)
            release_findings = []
            _scan_fcstd(path, path.name, release_findings)
            return source_scan, release_findings

    def test_declared_example_licence_passes_both_audits(self):
        source, release = self._check()
        self.assertEqual(source, {"ok": True, "findings": []})
        self.assertEqual(release, [])

    def test_default_freecad_licence_is_rejected(self):
        source, release = self._check(
            "All rights reserved", "https://en.wikipedia.org/wiki/All_rights_reserved")
        self.assertFalse(source["ok"])
        self.assertEqual(len(source["findings"]), 2)
        self.assertEqual(len(release), 2)

    def test_missing_or_mismatched_properties_are_rejected(self):
        for name, url in ((None, EXAMPLE_LICENSE_URL), (EXAMPLE_LICENSE, None),
                          (EXAMPLE_LICENSE, "https://example.invalid/")):
            with self.subTest(license=name, url=url):
                source, release = self._check(name, url)
                self.assertFalse(source["ok"])
                self.assertEqual(len(release), 1)


if __name__ == "__main__":
    unittest.main()
