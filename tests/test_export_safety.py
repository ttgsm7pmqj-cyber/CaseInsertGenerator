# SPDX-License-Identifier: LGPL-2.1-or-later
"""Output collision and failure recovery tests without a FreeCAD runtime."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _engine():
    previous = {name: sys.modules.get(name) for name in ("FreeCAD", "Part")}
    sys.modules["FreeCAD"] = types.ModuleType("FreeCAD")
    sys.modules["Part"] = types.ModuleType("Part")
    try:
        return importlib.import_module("freecad.CaseInsertGenerator.engine")
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class ExportSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = _engine()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="caseinsert-export-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / "parts.step"
        self.paths = self.engine._numbered_export_paths(str(self.base), 3)
        self.calls = []

    def write_part(self, part, path):
        self.calls.append(part)
        Path(path).write_bytes(("new part %d" % part).encode())

    def batch(self, **kwargs):
        return self.engine._write_export_batch(
            [1, 2, 3], self.paths, self.write_part, **kwargs)

    def assert_no_staging(self):
        self.assertEqual(list(self.root.glob(".caseinsert-export-*")), [])

    def test_numbered_collision_rejects_before_any_writer_is_called(self):
        Path(self.paths[1]).write_bytes(b"keep numbered output")
        self.assertFalse(self.base.exists())
        with self.assertRaisesRegex(FileExistsError, "parts_part_02.step"):
            self.batch()
        self.assertEqual(self.calls, [])
        self.assertEqual(Path(self.paths[1]).read_bytes(), b"keep numbered output")
        self.assertFalse(Path(self.paths[0]).exists())
        self.assertFalse(Path(self.paths[2]).exists())
        self.assert_no_staging()

    def test_explicit_overwrite_replaces_the_whole_numbered_set(self):
        Path(self.paths[0]).write_bytes(b"old output")
        self.assertEqual(self.batch(overwrite=True), self.paths)
        for index, output in enumerate(self.paths, 1):
            self.assertEqual(Path(output).read_bytes(), ("new part %d" % index).encode())
        self.assertFalse(self.base.exists())
        self.assert_no_staging()

    def test_single_output_keeps_return_path_and_requires_confirmation(self):
        self.base.write_bytes(b"old single output")
        with self.assertRaises(FileExistsError):
            self.engine._write_export_batch(
                [1], [str(self.base)], self.write_part)
        returned = self.engine._write_export_batch(
            [1], [str(self.base)], self.write_part, overwrite=True)
        self.assertEqual(returned, str(self.base))
        self.assertEqual(self.base.read_bytes(), b"new part 1")
        self.assert_no_staging()

    def test_later_writer_failure_preserves_old_files_and_creates_none(self):
        Path(self.paths[0]).write_bytes(b"old first")
        Path(self.paths[2]).write_bytes(b"old third")

        def fail_later(part, path):
            self.write_part(part, path)
            if part == 2:
                raise OSError("injected later writer failure")

        with self.assertRaisesRegex(OSError, "later writer failure"):
            self.engine._write_export_batch(
                [1, 2, 3], self.paths, fail_later, overwrite=True)
        self.assertEqual(Path(self.paths[0]).read_bytes(), b"old first")
        self.assertFalse(Path(self.paths[1]).exists())
        self.assertEqual(Path(self.paths[2]).read_bytes(), b"old third")
        self.assert_no_staging()

    def test_later_replace_failure_restores_old_files_and_removes_new_files(self):
        Path(self.paths[0]).write_bytes(b"old first")
        Path(self.paths[2]).write_bytes(b"old third")
        replace = os.replace

        def fail_third(source, destination):
            if str(destination) == self.paths[2]:
                raise OSError("injected later replacement failure")
            return replace(source, destination)

        with patch.object(self.engine.os, "replace", side_effect=fail_third):
            with self.assertRaisesRegex(OSError, "later replacement failure"):
                self.batch(overwrite=True)
        self.assertEqual(Path(self.paths[0]).read_bytes(), b"old first")
        self.assertFalse(Path(self.paths[1]).exists())
        self.assertEqual(Path(self.paths[2]).read_bytes(), b"old third")
        self.assert_no_staging()

    def test_collision_created_during_staging_is_preserved(self):
        def another_export_appears(part, path):
            self.write_part(part, path)
            if part == 3:
                Path(self.paths[1]).write_bytes(b"created by another operation")

        with self.assertRaises(FileExistsError):
            self.engine._write_export_batch(
                [1, 2, 3], self.paths, another_export_appears)
        self.assertEqual(
            Path(self.paths[1]).read_bytes(), b"created by another operation")
        self.assertFalse(Path(self.paths[0]).exists())
        self.assertFalse(Path(self.paths[2]).exists())
        self.assert_no_staging()

    def test_directory_destination_is_rejected_even_with_overwrite(self):
        Path(self.paths[1]).mkdir()
        sentinel = Path(self.paths[1]) / "keep.txt"
        sentinel.write_bytes(b"unrelated folder content")
        with self.assertRaises(IsADirectoryError):
            self.batch(overwrite=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(sentinel.read_bytes(), b"unrelated folder content")
        self.assert_no_staging()

    def test_missing_staged_file_is_rejected_before_replacement(self):
        Path(self.paths[0]).write_bytes(b"old first")
        with self.assertRaisesRegex(RuntimeError, "produced no file"):
            self.engine._write_export_batch(
                [1, 2, 3], self.paths, lambda *_: None, overwrite=True)
        self.assertEqual(Path(self.paths[0]).read_bytes(), b"old first")
        self.assert_no_staging()

    def test_failed_rollback_retains_recovery_copy_and_reports_its_location(self):
        Path(self.paths[0]).write_bytes(b"recoverable original")
        replace = os.replace

        def fail_replace_and_restore(source, destination):
            if str(destination) == self.paths[1] or "previous" in Path(source).parts:
                raise OSError("injected output volume failure")
            return replace(source, destination)

        with patch.object(self.engine.os, "replace", side_effect=fail_replace_and_restore):
            with self.assertRaisesRegex(RuntimeError, "Recovery copies are in") as error:
                self.batch(overwrite=True)
        staging = list(self.root.glob(".caseinsert-export-*"))
        self.assertEqual(len(staging), 1)
        recovery = staging[0] / "previous" / Path(self.paths[0]).name
        self.assertEqual(recovery.read_bytes(), b"recoverable original")
        self.assertIn(str(recovery.parent), str(error.exception))

    def test_symlink_is_restored_without_modifying_its_target(self):
        target = self.root / "other-project.step"
        target.write_bytes(b"unrelated target")
        Path(self.paths[0]).symlink_to(target)
        replace = os.replace

        def fail_second(source, destination):
            if str(destination) == self.paths[1]:
                raise OSError("injected failure after replacing symlink")
            return replace(source, destination)

        with patch.object(self.engine.os, "replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.batch(overwrite=True)
        self.assertTrue(Path(self.paths[0]).is_symlink())
        self.assertEqual(os.readlink(self.paths[0]), str(target))
        self.assertEqual(target.read_bytes(), b"unrelated target")
        self.assert_no_staging()

    def test_export_paths_respects_selection_before_numbering(self):
        objects = [object(), object()]
        document = object()
        with patch.object(self.engine, "active_results", return_value=objects) as resolve:
            paths = self.engine.export_paths(
                self.base, doc=document, selected_names=["PartB", "PartD"])
        resolve.assert_called_once_with(
            document, selected_names=["PartB", "PartD"])
        self.assertEqual(paths, self.paths[:2])

    def test_no_printable_results_is_actionable(self):
        with patch.object(self.engine, "active_results", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "No printable generated parts"):
                self.engine.export_paths(self.base)


if __name__ == "__main__":
    unittest.main()
