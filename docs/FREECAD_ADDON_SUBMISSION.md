<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# FreeCAD Addon addition issue draft

This is a local draft for v0.1.2. The update has not been published or submitted
to the Addon Index. Publish the tested version, verify its public archive and
repository links, and obtain separate submission approval before using this draft.

## Repository URL

https://github.com/ttgsm7pmqj-cyber/CaseInsertGenerator

## Notes

Please consider adding Case Insert Generator to the FreeCAD Addon Index.

- Stable branch: `main`
- Release to publish: `v0.1.2`
- FreeCAD metadata range: `1.1.3` through `1.1.x`; tested on 1.1.4/macOS arm64
- Licences: LGPL-2.1-or-later for source code; CC-BY-SA-4.0 for original
  documentation, examples, and visual assets
- Package/workbench: `CaseInsertGenerator` / `CaseInsertGeneratorWorkbench`
- Runtime: local-only; no network access, telemetry, account, paid feature, or
  third-party Python dependency
- Current-main software verification on 7 October 2026: FreeCAD 1.1.4,
  macOS arm64; 162/162 standalone tests and 63/63 native CAD/GUI checks
  (23 integration, 5 input-validation, 14 recovery, 21 GUI state).
  Clean-profile startup and evidence-gated five-part lid generation passed.
  Validate the final versioned package separately before publication.
- The supported metadata range starts at FreeCAD 1.1.3; current-candidate
  minimum-version behavior and Windows/Linux remain unverified.
- New STL/STEP exports require filesystem hard links; unsupported destinations
  fail closed. Network/removable storage has not been validated.
- Historical v0.1.1 example validation: 23/23 synthetic themed sets plus one synthetic lid panel, producing
  48 FCStd files and 51 PNGs. All 48 FCStd files cold-reopened with valid printable
  geometry and consistent embedded CC-BY-SA-4.0 metadata.
- GUI verification: clean FreeCAD 1.1.4/macOS arm64 profile; lazy workbench startup; three
  workflow tabs; unknown-clearance printing blocked; measured-clearance
  printable generation passed
- Packaging target: `CaseInsertGenerator-v0.1.2.zip`, with one
  `CaseInsertGenerator/` top-level directory. Before submission, verify that the
  published archive matches the tested commit and passes fresh-profile startup.
- Limitation: physical fit, lid closure, retention under load, and loaded
  carrying are not claimed and were not tested for v0.1.2

Overview screenshot:
`examples/lid-panel/lid-panel-controls-unknown-clearance.png`
