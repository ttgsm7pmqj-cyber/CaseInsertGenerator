<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# FreeCAD Addon addition issue draft

This is a local draft for v0.1.1. The update has not been published or submitted
to the Addon Index. Publish the tested version, verify its public archive and
repository links, and obtain separate submission approval before using this draft.

## Repository URL

https://github.com/ttgsm7pmqj-cyber/CaseInsertGenerator

## Notes

Please consider adding Case Insert Generator to the FreeCAD Addon Index.

- Stable branch: `main`
- Release to publish: `v0.1.1`
- FreeCAD compatibility: `1.1.3` through `1.1.x`
- Licences: LGPL-2.1-or-later for source code; CC-BY-SA-4.0 for original
  documentation, examples, and visual assets
- Package/workbench: `CaseInsertGenerator` / `CaseInsertGeneratorWorkbench`
- Runtime: local-only; no network access, telemetry, account, paid feature, or
  third-party Python dependency
- Local software verification on 2026-09-05: 129/129 Python tests and 63/63
  FreeCAD checks (23 integration, 5 input-validation, 14 recovery, 21 GUI).
  The full CAD/GUI checks cover the unchanged geometry and controller source
  used by v0.1.1; the versioned archive is checked separately during packaging.
- Examples: 23/23 synthetic themed sets plus one synthetic lid panel, producing
  48 FCStd files and 51 PNGs. All 48 FCStd files cold-reopened with valid printable
  geometry and consistent embedded CC-BY-SA-4.0 metadata.
- GUI verification: clean FreeCAD 1.1.3 profile; lazy workbench startup; three
  workflow tabs; unknown-clearance printing blocked; measured-clearance
  printable generation passed
- Packaging target: `CaseInsertGenerator-v0.1.1.zip`, with one
  `CaseInsertGenerator/` top-level directory. Before submission, verify that the
  published archive matches the tested commit and passes fresh-profile startup.
- Limitation: physical fit, lid closure, retention under load, and loaded
  carrying are not claimed and were not tested for v0.1.1

Overview screenshot:
`examples/lid-panel/lid-panel-controls-unknown-clearance.png`
