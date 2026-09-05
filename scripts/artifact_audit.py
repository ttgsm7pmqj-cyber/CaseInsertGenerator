# SPDX-License-Identifier: LGPL-2.1-or-later
"""Portable-source checks for generated public example files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile


EXAMPLE_LICENSE = "CC-BY-SA-4.0"
EXAMPLE_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"

PROHIBITED_SOURCE_MARKERS = (
    "confidential-" + "source",
    "license-" + "required",
    "non" + "redistributable",
    "restricted-" + "cad",
)

_PATH_PATTERNS = (
    ("absolute-user-path", re.compile(r"/(?:users|home)/[^\s<>'\"]+", re.I)),
    ("file-uri", re.compile(r"\bfile://[^\s<>'\"]+", re.I)),
    ("windows-absolute-path", re.compile(r"\b[a-z]:[\\/][^\s<>'\"]+", re.I)),
)


def text_findings(text: str, member: str = "<text>") -> list[dict[str, str]]:
    """Return portable-source findings without retaining matched private paths."""

    lowered = text.lower()
    findings = [
        {"member": member, "kind": "prohibited-source-marker", "marker": marker}
        for marker in PROHIBITED_SOURCE_MARKERS
        if marker in lowered
    ]
    for kind, pattern in _PATH_PATTERNS:
        if pattern.search(text):
            findings.append({"member": member, "kind": kind})
    return findings


def scan_fcstd(fcstd_path: str | Path, *,
               require_example_license: bool = False) -> dict[str, Any]:
    """Check portable sources and, optionally, the distributed example licence."""

    findings: list[dict[str, str]] = []
    with zipfile.ZipFile(Path(fcstd_path)) as archive:
        if require_example_license:
            try:
                document = ET.fromstring(archive.read("Document.xml"))
                for name, expected in (("License", EXAMPLE_LICENSE),
                                       ("LicenseURL", EXAMPLE_LICENSE_URL)):
                    value = document.find(
                        "./Properties/Property[@name='%s']/String" % name)
                    if value is None or value.get("value") != expected:
                        findings.append({"member": "Document.xml",
                                         "kind": "example-licence-mismatch",
                                         "property": name})
            except (KeyError, ET.ParseError):
                findings.append({"member": "Document.xml",
                                 "kind": "invalid-example-document"})
        for member in archive.namelist():
            if not member.lower().endswith((".xml", ".json", ".txt")):
                continue
            try:
                content = archive.read(member).decode("utf-8", errors="ignore")
            except Exception:
                continue
            findings.extend(text_findings(content, member))
    return {"ok": not findings, "findings": findings}


__all__ = ["EXAMPLE_LICENSE", "EXAMPLE_LICENSE_URL", "PROHIBITED_SOURCE_MARKERS",
           "scan_fcstd", "text_findings"]
