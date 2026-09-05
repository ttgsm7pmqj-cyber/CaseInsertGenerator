<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Changelog

## [Unreleased]

- Bind each dialog to its document, preserve current edits when saving, and
  require current generated geometry before STL/STEP export.
- Restore legacy divider/SVG settings on reopen and preserve schema metadata,
  floor dimensions, and stable object IDs through GUI editing.
- Make generation undoable and roll back failed document updates; check actual
  numbered export destinations and preserve previous files on export failure.
- Reject unsupported SVG effects and nonfinite CAD dimensions, and warn when
  individual bin lids leave other loose-storage regions uncovered.
- Repair Fit model in view and preserve generation warnings.
- Assign consistent embedded CC-BY-SA-4.0 metadata to distributed FCStd examples
  and record their actual generation date.

## [0.1.0] - 2026-09-04

- Added the generic schema-v1 case-insert engine, editable FCStd projects,
  deterministic layouts, removable bins, layered carriers, containment parts,
  SVG pockets, bed splitting, and separate STL/STEP exports.
- Added optional evidence-gated inside-lid panels with solid, modular slot-grid,
  and perforated-grid patterns.
- Added twenty-three original synthetic themed examples and one original
  synthetic lid-panel example, each explicitly making no physical-fit claim.
- Added a local-only FreeCAD workbench and compatibility macro with no account,
  network access, telemetry, subscription, or paid feature.

Physical fit, lid closure, retention under load, and loaded carrying have not
been tested for this release. Users must measure their own case and validate a
small tolerance sample before relying on a full print.
