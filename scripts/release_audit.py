#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fail-closed release-tree, metadata, artifact, and provenance checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"
RELEASE_DATE = "2026-09-05"
REPOSITORY_URL = "https://github.com/ttgsm7pmqj-cyber/CaseInsertGenerator"

FORBIDDEN_MARKERS = {
    "vendor case brand": "peli" + "can",
    "radio-network trademark": "mesht" + "astic",
    "mounting-system trademark": "mo" + "lle",
    "power-tool trademark": "milwau" + "kee",
    "marketplace name": "maker" + "world",
    "legacy repository": "freecad-case-" + "insert-generator",
    "local username path": "/users/" + "boss",
    "restricted source marker": "restricted-" + "cad",
    "confidential source marker": "confidential-" + "source",
    "permission-required marker": "license-" + "required",
    "non-redistributable marker": "non" + "redistributable",
    "excluded powered feature": "powered_" + "adapter",
    "excluded powered feature path": "powered-" + "adapter",
    "excluded powered case": "powered-" + "case",
    "excluded powered generator": "generate_" + "powered_" + "adapter",
}

VENDOR_MODEL_PATTERNS = {
    "vendor V-series model": re.compile(
        r"\bv(?:100|200|250|300|525|550|600|700|730|770|800)\b", re.I),
    "vendor M-series model": re.compile(
        r"\b(?:micro|case|model)[\s_-]+m(?:40|50|60)\b", re.I),
    "vendor product family": re.compile(
        r"\b(?:vault|protector|storm|air|micro)[\s_-]+case\b", re.I),
}

CREDENTIAL_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "embedded URL credentials": re.compile(
        r"https?://[^\s/:]+:[^\s/@]+@", re.I),
}

TEXT_SUFFIXES = {
    "", ".fcmacro", ".json", ".md", ".py", ".svg", ".toml",
    ".txt", ".xml", ".yaml", ".yml",
}


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=root, check=check, capture_output=True, text=True,
        timeout=30.0,
    )


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True,
        capture_output=True, timeout=30.0,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _finding(findings: list[dict[str, str]], location: str, kind: str) -> None:
    record = {"location": location, "kind": kind}
    if record not in findings:
        findings.append(record)


def _scan_bytes(data: bytes, location: str, findings: list[dict[str, str]]) -> None:
    lowered = data.lower()
    for kind, marker in FORBIDDEN_MARKERS.items():
        if marker.encode("utf-8") in lowered:
            _finding(findings, location, kind)
    text = data.decode("utf-8", errors="ignore")
    for kind, pattern in VENDOR_MODEL_PATTERNS.items():
        if pattern.search(text):
            _finding(findings, location, kind)
    for kind, pattern in CREDENTIAL_PATTERNS.items():
        if pattern.search(text):
            _finding(findings, location, kind)


def _scan_fcstd(path: Path, relative: str, findings: list[dict[str, str]]) -> None:
    if not zipfile.is_zipfile(path):
        _finding(findings, relative, "invalid FCStd archive")
        return
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "Document.xml" not in names:
                _finding(findings, relative, "FCStd missing Document.xml")
            else:
                try:
                    document = ET.fromstring(archive.read("Document.xml"))
                    for name, expected in (
                            ("License", "CC-BY-SA-4.0"),
                            ("LicenseURL", "https://creativecommons.org/licenses/by-sa/4.0/")):
                        value = document.find(
                            "./Properties/Property[@name='%s']/String" % name)
                        if value is None or value.get("value") != expected:
                            _finding(findings, relative + "::Document.xml",
                                     "example " + name + " mismatch")
                except ET.ParseError:
                    _finding(findings, relative, "FCStd invalid Document.xml")
            _scan_bytes(archive.comment, relative + "::<archive-comment>", findings)
            for member in names:
                member_path = PurePosixPath(member)
                if member_path.is_absolute() or ".." in member_path.parts:
                    _finding(findings, relative + "::" + member, "unsafe archive path")
                    continue
                if member.endswith("/"):
                    continue
                _scan_bytes(
                    archive.read(member), relative + "::" + member, findings)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        _finding(findings, relative, "FCStd read error: " + type(error).__name__)


def _png_text_payload(chunk_type: bytes, payload: bytes) -> bytes:
    try:
        if chunk_type == b"tEXt":
            return payload
        if chunk_type == b"zTXt":
            keyword, compressed = payload.split(b"\0", 1)
            return keyword + b"\0" + zlib.decompress(compressed[1:])
        if chunk_type == b"iTXt":
            keyword, remainder = payload.split(b"\0", 1)
            compressed_flag = remainder[0]
            remainder = remainder[2:]
            language, remainder = remainder.split(b"\0", 1)
            translated, text = remainder.split(b"\0", 1)
            if compressed_flag:
                text = zlib.decompress(text)
            return b"\0".join((keyword, language, translated, text))
    except (IndexError, ValueError, zlib.error):
        return payload
    return b""


def _scan_png(path: Path, relative: str, findings: list[dict[str, str]]) -> None:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        _finding(findings, relative, "invalid PNG signature")
        return
    offset = 8
    saw_end = False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        crc_end = payload_end + 4
        if crc_end > len(data):
            _finding(findings, relative, "truncated PNG chunk")
            return
        payload = data[payload_start:payload_end]
        expected_crc = struct.unpack(">I", data[payload_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            _finding(findings, relative, "invalid PNG checksum")
        if chunk_type != b"IDAT":
            _scan_bytes(payload, relative + "::" + chunk_type.decode("ascii", "replace"), findings)
        text_payload = _png_text_payload(chunk_type, payload)
        if text_payload:
            _scan_bytes(text_payload, relative + "::metadata", findings)
        offset = crc_end
        if chunk_type == b"IEND":
            saw_end = True
            break
    if not saw_end:
        _finding(findings, relative, "PNG missing end chunk")


def _scan_tree(root: Path, tracked: list[Path], findings: list[dict[str, str]]) -> None:
    prohibited_parts = {"__pycache__", "artifacts", "dist", "vendor-cad"}
    prohibited_suffixes = {".bak", ".fcbak", ".pyc", ".zip"}
    for path in tracked:
        relative = path.relative_to(root).as_posix()
        parts = {part.casefold() for part in PurePosixPath(relative).parts}
        if parts & prohibited_parts:
            _finding(findings, relative, "prohibited tracked directory")
        if path.suffix.casefold() in prohibited_suffixes or path.name == ".DS_Store":
            _finding(findings, relative, "prohibited tracked file")
        _scan_bytes(relative.encode("utf-8"), relative + "::<path>", findings)
        suffix = path.suffix.casefold()
        if suffix == ".fcstd":
            _scan_fcstd(path, relative, findings)
        elif suffix == ".png":
            _scan_png(path, relative, findings)
        elif suffix in TEXT_SUFFIXES:
            _scan_bytes(path.read_bytes(), relative, findings)


def _text(element: ET.Element, name: str) -> str:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    return ""


def _metadata_checks(root: Path, findings: list[dict[str, str]]) -> None:
    try:
        package = ET.parse(root / "package.xml").getroot()
    except (OSError, ET.ParseError) as error:
        _finding(findings, "package.xml", "XML parse error: " + type(error).__name__)
        return
    expected = {
        "name": "CaseInsertGenerator",
        "version": VERSION,
        "date": RELEASE_DATE,
        "freecadmin": "1.1.3",
        "freecadmax": "1.1.99",
        "pythonmin": "3.11.0",
    }
    for key, value in expected.items():
        if _text(package, key) != value:
            _finding(findings, "package.xml", key + " mismatch")
    urls = {
        child.attrib.get("type", ""): ((child.text or "").strip(), child.attrib)
        for child in package
        if child.tag.rsplit("}", 1)[-1] == "url"
    }
    if urls.get("repository", ("", {}))[0] != REPOSITORY_URL:
        _finding(findings, "package.xml", "repository URL mismatch")
    if urls.get("repository", ("", {}))[1].get("branch") != "main":
        _finding(findings, "package.xml", "release branch mismatch")
    if urls.get("readme", ("", {}))[0] != REPOSITORY_URL + "/raw/main/README.md":
        _finding(findings, "package.xml", "readme URL mismatch")
    content = next(
        (child for child in package if child.tag.rsplit("}", 1)[-1] == "content"),
        None,
    )
    items = [] if content is None else list(content)
    if [item.tag.rsplit("}", 1)[-1] for item in items] != ["workbench"]:
        _finding(findings, "package.xml", "content must contain one workbench")
    elif _text(items[0], "classname") != "CaseInsertGeneratorWorkbench":
        _finding(findings, "package.xml", "workbench classname mismatch")
    init_text = (root / "freecad" / "CaseInsertGenerator" / "__init__.py").read_text(
        encoding="utf-8")
    if not re.search(rf'^__version__\s*=\s*["\']{re.escape(VERSION)}["\']\s*$', init_text, re.M):
        _finding(findings, "freecad/CaseInsertGenerator/__init__.py", "version mismatch")
    model_text = (root / "freecad" / "CaseInsertGenerator" / "project_model.py").read_text(
        encoding="utf-8")
    if not re.search(r"^SCHEMA_VERSION\s*=\s*1\s*$", model_text, re.M):
        _finding(findings, "freecad/CaseInsertGenerator/project_model.py", "schema mismatch")


def _artifact_checks(root: Path, findings: list[dict[str, str]]) -> None:
    themed = root / "examples" / "themed-packs"
    pack_dirs = sorted(path for path in themed.iterdir() if path.is_dir())
    if len(pack_dirs) != 23:
        _finding(findings, "examples/themed-packs", "expected 23 pack directories")
    if len(list(themed.glob("*/*.FCStd"))) != 46:
        _finding(findings, "examples/themed-packs", "expected 46 FCStd files")
    if len(list(themed.rglob("*.png"))) != 48:
        _finding(findings, "examples/themed-packs", "expected 48 PNG files")
    expected_pack = themed / "22-portable-mesh-radio-node"
    if not expected_pack.is_dir():
        _finding(findings, "examples/themed-packs", "neutral example 22 is missing")
    try:
        manifest = json.loads((themed / "manifest.json").read_text(encoding="utf-8"))
        summary = manifest["summary"]
        expected_summary = {
            "total": 23, "passed": 23, "failed": 0,
            "exploded_models": 23, "rendered": 23, "exploded_rendered": 23,
        }
        if not manifest.get("ok") or summary != expected_summary:
            _finding(findings, "examples/themed-packs/manifest.json", "summary mismatch")
        if not manifest.get("source_commit_verified"):
            _finding(findings, "examples/themed-packs/manifest.json", "source commit not verified")
        if "git_head" in manifest:
            _finding(findings, "examples/themed-packs/manifest.json", "private commit metadata present")
        pack_22 = next(item for item in manifest["examples"] if item["number"] == 22)
        if (
            pack_22["slug"] != "portable-mesh-radio-node"
            or pack_22["id"] != "theme.portable-mesh-radio-node.v1"
            or pack_22["title"] != "Portable Mesh-Radio Node Insert"
        ):
            _finding(findings, "examples/themed-packs/manifest.json", "example 22 mismatch")
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as error:
        _finding(findings, "examples/themed-packs/manifest.json", "manifest error: " + type(error).__name__)

    lid = root / "examples" / "lid-panel"
    if len(list(lid.glob("*.FCStd"))) != 2:
        _finding(findings, "examples/lid-panel", "expected 2 FCStd files")
    if len(list(lid.glob("*.png"))) != 3:
        _finding(findings, "examples/lid-panel", "expected 3 PNG files")
    try:
        lid_manifest = json.loads((lid / "manifest.json").read_text(encoding="utf-8"))
        if not lid_manifest.get("ok") or not lid_manifest.get("rendered"):
            _finding(findings, "examples/lid-panel/manifest.json", "lid manifest mismatch")
    except (OSError, ValueError, TypeError) as error:
        _finding(findings, "examples/lid-panel/manifest.json", "manifest error: " + type(error).__name__)


def _single_commit_checks(root: Path, findings: list[dict[str, str]]) -> None:
    if _run(root, "git", "status", "--porcelain").stdout.strip():
        _finding(findings, ".git", "working tree is dirty")
    if _run(root, "git", "rev-list", "--all", "--count").stdout.strip() != "1":
        _finding(findings, ".git", "repository does not have exactly one commit")
    head = _run(root, "git", "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    if len(head) != 1:
        _finding(findings, ".git", "release commit is not a root commit")
    branches = _run(
        root, "git", "for-each-ref", "--format=%(refname)", "refs/heads"
    ).stdout.splitlines()
    if branches != ["refs/heads/main"]:
        _finding(findings, ".git", "main is not the only local branch")
    remotes = _run(root, "git", "remote", "-v").stdout.casefold()
    legacy = ("freecad-case-" + "insert-generator")
    if legacy in remotes or (("peli" + "can") in remotes):
        _finding(findings, ".git", "legacy remote present")


def audit(root: Path, require_single_commit: bool = False) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    tracked = _tracked_files(root)
    _scan_tree(root, tracked, findings)
    _metadata_checks(root, findings)
    _artifact_checks(root, findings)
    if require_single_commit:
        _single_commit_checks(root, findings)
    return {
        "ok": not findings,
        "version": VERSION,
        "tracked_files": len(tracked),
        "single_commit_required": require_single_commit,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--require-single-commit", action="store_true")
    arguments = parser.parse_args()
    report = audit(arguments.root.resolve(), arguments.require_single_commit)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
