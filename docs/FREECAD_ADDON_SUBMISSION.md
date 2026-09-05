<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# FreeCAD Addon addition issue draft

This is a local draft only. Submission requires separate user approval.
The version and evidence below describe the published v0.1.0 baseline; refresh
them after publishing subsequent changes and before submitting this draft.

## Repository URL

https://github.com/ttgsm7pmqj-cyber/CaseInsertGenerator

## Notes

Please consider adding Case Insert Generator to the FreeCAD Addon Index.

- Stable branch: `main`
- Release: `v0.1.0`
- FreeCAD compatibility: `1.1.3` through `1.1.x`
- Licences: LGPL-2.1-or-later for source code; CC-BY-SA-4.0 for original
  documentation, examples, and visual assets
- Package/workbench: `CaseInsertGenerator` / `CaseInsertGeneratorWorkbench`
- Runtime: local-only; no network access, telemetry, account, paid feature, or
  third-party Python dependency
- Verification: 85/85 Python tests, 23/23 FreeCAD contracts, 23/23 synthetic
  themed examples producing 46 FCStd files and 48 PNGs, and 1/1 synthetic lid
  example producing two cold-reopened FCStd files and three inspected PNGs
- GUI verification: clean FreeCAD 1.1.3 profile; lazy workbench startup; three
  workflow tabs; unknown-clearance printing blocked; measured-clearance
  printable generation passed
- Packaging: `CaseInsertGenerator-v0.1.0.zip` has one
  `CaseInsertGenerator/` top-level directory and was installed into a fresh
  profile for the repeated GUI smoke test
- Limitation: physical fit, lid closure, retention under load, and loaded
  carrying are not claimed and were not tested for v0.1.0

Overview screenshot:
`examples/lid-panel/lid-panel-controls-unknown-clearance.png`
