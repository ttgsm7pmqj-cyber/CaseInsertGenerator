<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Case Insert Generator — Project Assessment

## 1. Executive snapshot

| Field | Assessment |
|---|---|
| Report mode | `full_assessment` |
| Assessed at | 2026-09-06T03:16:05+10:00 — Australia/Melbourne |
| Repository / project root | `/Users/boss/Code/CaseInsertGenerator` |
| Branch | `codex/review-polish`, tracking `origin/codex/review-polish` at the final inspection |
| Base commit | `4b847ac60defd06efdfaa16f814ee1d77bc8646c` — `main`, `v0.1.0` |
| Head commit | `1c7a1c8bdfcf8ca362d08cc7ebadb57a342f19df` — v0.1.1 candidate |
| Overall health | **AMBER** |
| Delivery confidence | **MEDIUM overall; strong for the exercised macOS software paths.** Physical fit, broader platform support and publication of the update remain separate. |
| Completion | **Not reliably calculable.** No agreed, weighted project-wide acceptance baseline exists. |
| Main user-facing path | **VERIFIED** for the bounded software workflow: fresh launch, edit/generate, save/reopen, and selected-part export. Physical use remains **UNKNOWN**. |
| Largest blocker | No software blocker reproduced in the standard tested path. Real-case measurements and physical test evidence are missing before fitted-use or loaded-carry claims can be made. |
| Recommended next move | Complete review of the existing v0.1.1 update, then validate one measured case before expanding the feature set. |

### Bottom-line assessment

A usable local FreeCAD workbench exists, with editable insert designs, layered carriers, bins, SVG pockets, containment options and evidence-controlled lid panels. This assessment reproduced **129/129 standalone tests, 63/63 native CAD/GUI checks, a fresh-profile launch, 48/48 example reopens, and eight-part STEP/STL output validation** (EV-002–EV-007). The current candidate archive matches all **174/174 tracked files**, while the latest published release remains v0.1.0; candidate CI passed at the assessed commit (EV-009–EV-010). No failure was reproduced in the standard software path, but physical fit, lid closure, retention and loaded carrying have no fresh or retained success evidence. A custom-catalog validation defect and an inaccurate provenance description remain small, concrete follow-ups; concentrated engine code is maintenance debt rather than a reason to redesign. The project shows material progress toward its stated local CAD purpose, and its next useful work is completing the existing update and obtaining real-use evidence.

## 2. Intended outcome and scope

### Original goal

**Inferred from the authoritative repository**, especially [README.md](/Users/boss/Code/CaseInsertGenerator/README.md:5): provide a free, local FreeCAD tool that turns measured storage-case dimensions and an object layout into editable inserts and printable parts. The initiating product brief is not stored in this checkout; this assessment does not substitute details from another repository for that brief.

### Current agreed scope

The README, package metadata and release history establish a generic namespaced FreeCAD workbench, schema-v1 projects stored inside FCStd, three workflow tabs, six object types, two layers, deterministic layouts, containment, SVG import, bed splitting, STL/STEP exports and optional lid mounting panels. The 23 themed examples and one lid example are synthetic demonstrations. The current software target is FreeCAD 1.1.3; the declared compatibility range extends through 1.1.x. Sources: [README.md](/Users/boss/Code/CaseInsertGenerator/README.md:22), [CHANGELOG.md](/Users/boss/Code/CaseInsertGenerator/CHANGELOG.md:5), [package.xml](/Users/boss/Code/CaseInsertGenerator/package.xml:6).

### Non-goals

- Accounts, telemetry, paid tiers and cloud services are explicitly excluded.
- Bundled commercial case dimensions and claims of tested compatibility are excluded; independently measured or otherwise redistributable profiles have separate evidence requirements.
- Geometry and software checks do not establish material suitability or physical-fit approval.
- This assessment does not implement fixes, merge, publish, submit an Addon Index request or change existing worktrees. The older private/vendor-derived checkout and powered-case experiments are outside this project boundary.

### Acceptance criteria

All criteria below are **INFERRED**, unweighted and limited to the documented purpose. Counts of passing tests are not completion percentages.

| ID | Acceptance criterion | Weight, if defined | Status | Evidence |
|---|---|---|---|---|
| AC-01 | Install and launch the current local workbench with its three workflow tabs | Not defined | VERIFIED | EV-005: isolated profile; lazy startup; three tabs; five valid lid parts |
| AC-02 | Generate representative insert, layer, bin, SVG and lid geometry from supported settings | Not defined | VERIFIED | EV-006: 23 integration checks plus validation/recovery suites; EV-007 sample |
| AC-03 | Preserve editable state through save/reopen and avoid redirecting actions to another document | Not defined | VERIFIED | EV-006: 21 real Qt GUI tests including cold reopen and document switching |
| AC-04 | Export current selected printable geometry and protect existing outputs on failure/cancel | Not defined | VERIFIED | EV-006 recovery/GUI checks; EV-007: 8 valid STEP solids and 8 closed STL meshes |
| AC-05 | Block printable lid output when evidence or current geometry is insufficient | Not defined | VERIFIED | EV-005 unknown clearance block; EV-006 dirty-export regression |
| AC-06 | Supply intact, editable synthetic examples and a consistent current package | Not defined | VERIFIED | EV-008: 122 hashes; EV-006: 48 reopens; EV-009: 174 archive matches |
| AC-07 | Make the assessed fixes available in the normal published release | Not defined | PARTIAL | EV-010: candidate CI succeeds, but `main` and release still v0.1.0 |
| AC-08 | Demonstrate fitted use, lid closure, retention and loaded carrying in one real case | Not defined | UNKNOWN | Repository explicitly says untested; no physical apparatus or test record available |

## 3. Current state by capability

**Status vocabulary:** VERIFIED = reproduced in this assessment; IMPLEMENTED_UNVERIFIED = code/files exist but behavior was not reproduced; PARTIAL = a required path remains incomplete; BLOCKED = a dependency, decision or fix prevents progress; NOT_STARTED = no meaningful implementation found; DEFERRED = explicitly outside current scope; UNKNOWN = insufficient evidence.

### Works end-to-end

| Capability or user journey | Status | What exists | What was verified | Gap remaining |
|---|---|---|---|---|
| Local install and launch | VERIFIED | Namespaced workbench and compatibility launcher | A byte-identical copy of current tracked files launched in its own profile; lazy engine loading and all three tabs passed (EV-005) | Addon Manager installation and other OS versions not exercised |
| Insert design and geometry | VERIFIED | Six object types, two layers, layouts, containment, splitting | Native contracts and fresh field-mending sample (EV-006–EV-007) | Finite cases tested; no exhaustive parameter or packing-optimality proof |
| Save, reopen and document ownership | VERIFIED | Embedded schema JSON, owner-bound dialog, undoable regeneration | Editing/save sequences, reopened settings, two-document isolation, closed-document rejection and recovery (EV-006) | Native file chooser presentation was replaced by test paths |
| SVG pockets | VERIFIED | Preflight plus FreeCAD runtime importer | Unit rejection/normalization coverage and native imported geometry (EV-002, EV-006) | Unsupported SVG forms are rejected; sources remain external |
| Lid panel evidence and generation | VERIFIED | Solid, slot and perforated panels; mounting/keep-outs/keyed split | Unknown evidence blocks output; measured inputs generate valid parts; changed settings block stale exports (EV-005–EV-006) | Measurements used in checks are synthetic |
| Current selected-part export | VERIFIED | Separate STL/STEP files and editable FCStd | Eight sample STEP solids reopened valid, eight STL meshes reopened closed; GUI selected-output and collision tests (EV-006–EV-007) | Slicer, print and physical results not tested |
| Example library | VERIFIED | 23 themed packs and one lid example | All 48 FCStd files reopened with valid non-null shapes and loadable project specs; 122 listed hashes matched (EV-006, EV-008) | Whole library was not freshly regenerated or physically tested |

### Partially working

The release-delivery path is **PARTIAL**: v0.1.1 source, exact package and successful candidate CI exist, but users taking the public default branch/release still receive v0.1.0 (EV-009–EV-010). The broader measured-case-to-loaded-use journey is also incomplete: digital output is demonstrated while physical acceptance remains UNKNOWN. Fourteen themed packs retain warnings about uncovered loose-storage regions under individual-bin-lid containment; those are recorded limitations, not proof that all payloads are retained (EV-008).

### Present but not connected or reachable

No orphan core feature was established in the inspected flow. The API and compatibility macro route through the same lazy bridge/engine. The Addon Index submission document is a draft, not an installed/indexed distribution path. Native CAD/GUI suites are reachable through the regression macro but are not wired into CI. A custom-catalog path exists at API level and accepts malformed dimensions in the bounded probe (R-03).

### Not started, removed, or abandoned

No meaningful physical validation record was found; absence of records does not establish whether a person has ever attempted a print. Commercial compatibility profiles and powered-case functionality are DEFERRED from this assessment. The initial fresh-history boundary deliberately excludes legacy vendor material; it is not missing functionality to recover. No additional abandoned implementation was established in this checkout.

## 4. Progress since the previous baseline

No previous `PROJECT_ASSESSMENT.md` existed here. The comparison baseline is tagged v0.1.0 at `4b847ac`, supplemented by the existing [v0.1.0 review](/Users/boss/Documents/Codex/2026-09-05/caseinsertgenerator-full-review/outputs/CaseInsertGenerator-v0.1.0-review.md:30). That earlier review's F01–F12 describe the baseline, not automatically the current branch.

The current branch adds **10 commits**, with **139 files changed, 3,374 insertions and 793 deletions** relative to `main`; many changed files are regenerated binary examples. EV-001 preserves the commit log and diff summary.

| Change | Why it matters | Files or commits | Validation performed | Result |
|---|---|---|---|---|
| Transactional generation and safer multipart exports | Protects existing projects and files after failure | `8161bd2`; engine/export tests | Recovery, export safety and GUI collision checks | VERIFIED for tested cases |
| SVG rejection and uncovered-storage warnings | Reduces silently partial cuts and misleading containment status | `1bac70e`, `ac7959e`, `2efb1ce` | SVG, CAD validation and containment checks | VERIFIED for tested cases |
| Owner-bound dialog, current edits, legacy/manual state preservation | Prevents wrong-document changes and stale saved/exported results | `04aeb53`, `c68a2b2`, `1326025` | 21 GUI state tests | VERIFIED |
| Repeatable regressions and consistent example metadata | Makes native failures reproducible and removes embedded licence conflicts | `2147e92`, `6ad70df` | 48 fresh reopens; licence/hash audits; native runner | VERIFIED |
| v0.1.1 release metadata and candidate package | Prepares the repaired branch for distribution | `1c7a1c8` | Metadata audit, exact archive comparison, candidate CI | VERIFIED as candidate; publication PARTIAL |

### Net progress assessment

**MATERIAL_FORWARD_PROGRESS.** The work preserves the same product shape while fixing state loss, export validity, recovery and example consistency. Fresh behavior checks substantiate the improvement; larger code volume and refreshed screenshots are not the basis for this verdict. This assessment does not claim that every earlier finding has been exhaustively retested outside the current regression cases.

## 5. Milestones and deliverables

Only existing release states and deliverables are listed; no new milestones are introduced.

| Milestone / deliverable | Intended outcome | Status | Evidence | Remaining work |
|---|---|---|---|---|
| v0.1.0 release | Initial public generic workbench | VERIFIED as published artifact | EV-010 | It remains the older behavior baseline |
| v0.1.1 candidate | Repair reviewed behavior and update examples | VERIFIED locally; publication PARTIAL | EV-002–EV-010 | Complete the existing release review/publication process |
| Synthetic example library | Editable demonstrations | VERIFIED | EV-006, EV-008 | No real-fit claim follows |
| Addon Index submission draft | Prepare directory inclusion | IMPLEMENTED_UNVERIFIED | `docs/FREECAD_ADDON_SUBMISSION.md` | Submission/installation acceptance not reproduced |

### Deliverable inventory

| Deliverable | Path or location | Status | Opens / builds / runs? | Notes |
|---|---|---|---|---|
| Workbench and public API | `freecad/CaseInsertGenerator/` | VERIFIED | Launch and exercised runtime pass | Seven Python modules |
| Compatibility and regression launchers | `CaseInsertGenerator.FCMacro`, `RunRegressionChecks.FCMacro` | IMPLEMENTED_UNVERIFIED for macro-menu selection | Underlying bridge/runner executed | No mouse-driven Macro menu test |
| Themed examples | `examples/themed-packs/` | VERIFIED | 46/46 FCStd reopens | 23 JSON specifications, 46 model PNGs, two contact sheets |
| Lid example | `examples/lid-panel/` | VERIFIED | 2/2 FCStd reopens | Two model PNGs and one existing GUI PNG |
| Current release ZIP | [CaseInsertGenerator-v0.1.1.zip](/Users/boss/Documents/Codex/2026-09-05/caseinsertgenerator-full-review/outputs/CaseInsertGenerator-v0.1.1.zip) | VERIFIED | Archive integrity and all 174 file bytes match HEAD | Located outside repository `dist/`; not missing |
| Existing local `dist/` archive | `dist/CaseInsertGenerator-v0.1.0.zip` | VERIFIED as historical artifact | Archive inspected separately | Do not mistake it for current candidate |
| Assessment and evidence | This report and `artifacts/full_assessment/2026-09-06/` | VERIFIED on final document checks | Markdown, JSON, logs, PNG and CAD outputs | Evidence is Git-ignored and local |

## 6. Verification and evidence ledger

All EV paths below are durable assessment evidence unless explicitly identified as source or historical material. Test runs used the assessed source before this report was added. Native tests ran in a separate FreeCAD process against a fresh profile copy whose **174 files matched the checkout byte-for-byte**. No existing user document was used for these tests.

| Evidence ID | Claim being tested | Evidence type | Exact command, test, path, or artifact | Result | Status |
|---|---|---|---|---|---|
| EV-001 | Repository identity and baseline | Git snapshot | [repository-state.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/repository-state.json), [git-baseline.log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/git-baseline.log) | HEAD `1c7a1c8`; initially clean; 10 commits beyond base | VERIFIED |
| EV-002 | Standalone logic | Executed tests | `python3 -m unittest discover -s tests -v`; [unit-tests.log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/unit-tests.log), [checks.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/checks.json) | 129/129; zero failures or skips; 0.327 seconds; Python 3.14.7 | VERIFIED |
| EV-003 | Current tracked-tree metadata/provenance checks | Executed audit | `python3 scripts/release_audit.py`; [release-audit.log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/release-audit.log) | 174 files; zero findings; version 0.1.1 | VERIFIED |
| EV-004 | Licence/copyright declarations | Executed REUSE lint | Existing review venv `bin/reuse lint`; [reuse-lint.log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/reuse-lint.log) | 171/171 applicable files; zero reported issues | VERIFIED |
| EV-005 | Fresh local workbench startup | Real Qt launch | [installation.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/installation.json), [gui-smoke-result.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke-result.json), [process log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke-process.log) | Exit 0; lazy import; 3 tabs; Unknown blocks export; measured synthetic clearance generates 5 valid parts | VERIFIED |
| EV-006 | Native CAD/GUI and all example reopens | Real FreeCAD execution | [native-assessment.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/native-assessment.json), [regression results](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/regressions/results.json), [process log](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/native-process.log) | Integration 23/23; validation 5/5; recovery 14/14; GUI 21/21; zero failures/skips. FCStd reopens 48/48 | VERIFIED |
| EV-007 | Representative generated output | New files and reopen inspection | [sample_outputs](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/sample_outputs), `native-assessment.json` → `sample` | 8 generated parts; 8/8 valid one-solid STEP files; 8/8 closed STL meshes; FCStd saved | VERIFIED |
| EV-008 | Example integrity and provenance | Fresh archive/hash inspection | [example-integrity.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/example-integrity.json) | 122/122 listed artifact hashes; 6/6 themed source hashes; 48/48 ZIP/XML checks | VERIFIED |
| EV-009 | Candidate package matches source | Per-file Git blob comparison | [package-verification.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/package-verification.json) | 174/174 bytes match; ZIP CRC valid; one root; no missing/extra/unsafe/duplicate files | VERIFIED |
| EV-010 | Actual published and candidate CI state | Read-only live GitHub API | [remote-status.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/remote-status.json); [candidate CI run](https://github.com/ttgsm7pmqj-cyber/CaseInsertGenerator/actions/runs/33979499947) | 2/2 jobs succeeded at `1c7a1c8`; only release/tag v0.1.0; public `main` stays `4b847ac` | VERIFIED |
| EV-011 | Custom catalog input rejection | Reproduction probe | [catalog-probe.py](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/catalog-probe.py), [catalog-probe.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/catalog-probe.json) | 1 valid input accepted; 6 malformed inputs also accepted; rejection expectation not met | VERIFIED |
| EV-012 | Visible workflow and sample appearance | PNG inspection | [fresh GUI screenshot](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke.png), [five-image contact sheet](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-regressions-contact-sheet.png), [sample render](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/sample_outputs/field-mending.png) | Tabs, evidence warning and disabled exports visible. Sample viewport has surface/framing artifacts; not presentation-ready | VERIFIED observation |
| EV-013 | Environment failures and boundaries | Recorded attempts | [environment-notes.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/environment-notes.json) | MCP had no GUI; wrapper help attempt stopped; direct isolated launch succeeded; optional 3Dconnexion library absent | VERIFIED |

**Build:** this is a Python FreeCAD add-on with no separate compiled build step. Current source import, installation and real native execution were used instead of claiming a nonexistent clean compiler build. The unit tests include lightweight FreeCAD/Part stubs; native geometry and real Qt are established only by EV-005–EV-007. GUI regression file pickers, overwrite responses and error-message presentation are replaced with deterministic test handlers; widgets, controller actions, CAD, persistence and exports remain real.

**Run identity:** native execution ran from **2026-09-05T17:03:22+00:00 to 17:04:11+00:00** (03:03–03:04 on 6 September in Melbourne), using FreeCAD 1.1.3 and Python 3.11.14. The fresh smoke was a separate process; results are not presented as one monolithic all-green run. Missing `skipped` keys in the native unittest summaries were checked against their successful total/pass counts; no skipped result was recorded.

**Visual evidence:** the five regression captures show document ownership, stale-export blocking, preserved warnings, manual placement saving and saving current controls. The 3D sample image contains visible triangular surface artifacts and a cropped-looking rear edge; their cause was not isolated. Valid BREP/mesh checks do not erase that visual limitation.

![Fresh FreeCAD lid panel workflow with unknown clearance blocking print exports](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke.png)

## 7. Architecture as actually implemented

### Main components

| Component | Responsibility | Location | Connected to real flow? | Verification status |
|---|---|---|---|---|
| Startup and commands | Register workbench, expose command and load lazily | [init_gui.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/init_gui.py:16), [commands.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/commands.py:17) | Yes | VERIFIED launch |
| Bridge and public API | Shared entry points for commands/macros/Python | [bridge.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/bridge.py:21), `__init__.py` | Yes | VERIFIED underlying launch/API calls |
| Project model | Normalize/validate schema, compute layouts and warnings | [project_model.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/project_model.py:358) | Yes | VERIFIED tested contracts |
| Geometry and state | Build solids, split parts, preserve project JSON, manage transactions and exports | [engine.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/engine.py:1939) | Yes | VERIFIED representative paths |
| Dialog/controller | Collect three-tab settings, track current geometry, bind to a document | [engine.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/engine.py:3494) | Yes | VERIFIED 21 GUI cases |
| SVG adapter | Validate/normalize SVG and invoke installed FreeCAD importer | [svg_import.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/svg_import.py:267), [engine wrapper](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/engine.py:845) | Yes | VERIFIED supported/rejected examples |
| Examples and checks | Generate demonstration projects and audit artifact consistency | `scripts/`, `tests/`, `examples/` | Development workflow | VERIFIED audits/reopens; full regeneration not rerun |

### Data or control flow

1. FreeCAD discovers the namespaced workbench and registers a command; the geometry engine loads when needed.
2. The dialog reads a catalog/custom envelope or embedded project specification and collects object, lid and printer settings.
3. Pure Python validation normalizes schema-v1 data and reports layout, evidence and clearance problems. Layout strategies use rectangular envelopes and bounded candidates.
4. The engine creates actual FreeCAD geometry, replaces generated objects inside an undoable transaction and stores the normalized project inside the document.
5. The controller compares current settings and geometry with the generated state. Invalid/stale lid output stays blocked; saving can retain an incomplete configuration as a preview.
6. FCStd stores editable state and geometry; selected results are staged and exported separately to numbered STL/STEP paths with collision/rollback handling.

### Important dependencies

| Dependency | Purpose | Pinned / reproducible? | Current issue |
|---|---|---|---|
| FreeCAD / Part / Mesh / Draft importer | CAD kernel, mesh export and SVG import | Manifest declares 1.1.3–1.1.99; tested installed version 1.1.3, build `145529fe741292ff0b3977a01195bf0247425794` | Full range/OS matrix not proved |
| Python | Logic and test runtime | Manifest minimum 3.11; native 3.11.14; local standalone 3.14.7; CI configured 3.11 | No exact cross-machine runtime lock |
| FreeCAD-provided PySide / Qt | Desktop UI | Supplied by FreeCAD build | Native GUI environment required |
| FreeCAD `importSVG` | SVG geometry | Runtime dependency; upstream reference recorded in NOTICE; no vendored importer | Not every SVG feature supported |
| REUSE | Development licence lint | Existing environment reused; no new install | Not a product runtime dependency |
| GitHub Actions | Unit/tree/licensing checks | Action major tags and Ubuntu/Python selector in workflow | Native CAD/GUI checks remain manual |

The application needs no server or credentials for local use. GitHub authentication was used only for read-only assessment metadata, not by the product.

## 8. Quality and maintainability

| Area | Rating | Evidence | Consequence |
|---|---|---|---|
| Build reproducibility | GOOD for current local candidate; MIXED across platforms | Exact archive match and fresh profile launch | Strong current-source proof; supported versions are broader than the tested environment |
| Test coverage of main path | GOOD for focused behavior | 129 standalone + 63 native checks, fresh smoke, saved/loaded examples | Meaningful state/failure cases covered; test count is not coverage percentage |
| Error handling | GOOD in exercised paths | Transaction abort/undo, export staging, stale geometry blocking, unsupported SVG rejection | Existing work is protected in tested failures; custom catalog validation remains weak |
| State / data integrity | GOOD in exercised paths | Cold reopen, owner binding, manual-layout preservation, metadata and precision checks | Earlier loss/redirect cases have direct regression evidence |
| Performance | UNKNOWN beyond small demonstrations | Full native assessment completed in about 50 seconds | No benchmark, stress bound or large-SVG responsiveness proof |
| Security / privacy | GOOD for documented local architecture; limited audit | No product network path found; source/artifact scans and SVG preflight | No formal penetration, dependency-vulnerability or legal certification performed |
| Documentation / handoff quality | MIXED | Useful README, runnable macro, example sources, exact package | Provenance wording and uneven manifest evidence; local evidence must travel with report |
| Maintainability | MIXED | Engine 5,525 lines; project model 2,366; SVG adapter 1,537 | CAD, persistence/export and UI remain concentrated; change narrowly with existing regressions |

The unit tests deliberately mock only import boundaries where FreeCAD is unavailable, while the native suites exercise actual CAD/Qt. This separation is useful and now verified. No coverage instrument was run, so neither branch coverage nor overall correctness is quantified. SVG file size rejection occurs after the file has been read ([svg_import.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/svg_import.py:267)); unusually large input remains a resource-handling limitation, not a reproduced ordinary-input failure.

## 9. Scope drift and overengineering check

| Finding | Evidence | Effect on user outcome | Recommended treatment |
|---|---|---|---|
| Requested-feature attribution is incomplete | No initiating brief in checkout; current README and changelog are coherent | Cannot prove every historical feature was individually authorized | INVESTIGATE only if scope is disputed |
| Infrastructure precedes core path | No service stack; core path works now | No material problem found | KEEP lightweight setup |
| Excessive milestone/process layers | Repository contains normal release/security docs and a submission draft | No process layer obstructs the user flow | KEEP |
| Single-use abstractions | Small bridge serves GUI, macro and API; pure model serves tests and engine | Separation has demonstrated uses | KEEP |
| Duplicate implementations | Compatibility launcher delegates; old private project not part of this tree | No second competing core implementation established here | KEEP boundary |
| Placeholder/speculative systems represented as progress | Draft submission explicitly marked draft; examples marked synthetic | Labels generally prevent false completion claims | KEEP; correct provenance wording |
| Rewrites without user-facing benefit | Current delta has corresponding state/recovery/GUI tests | Material benefit demonstrated | KEEP fixes |
| Hypothetical scale work | No cloud/scaling stack or benchmark-driven expansion | No present concern | DEFER scale work |
| Maintenance concentration and demonstration volume | Large engine and 23 packs | Maintenance cost exists, but no need for a new architecture | DEFER broad refactor and new packs |

### Drift verdict

**ON_SCOPE relative to the repository's documented generic workbench goal.** Feature-level authorization against the absent original brief is UNKNOWN. Current complexity has not prevented the tested user path. A new framework, service layer, generator rewrite or recovery project is not supported by this assessment.

## 10. Defects, blockers, risks, and technical debt

There is **no reproduced blocking software defect in the standard tested path**. Physical acceptance and publication are separate incomplete delivery concerns. Optional polish is not a blocker.

| ID | Type | Description | Severity | Evidence | Smallest practical response |
|---|---|---|---|---|---|
| R-01 | RISK | No physical fit, lid closure, retention, material or loaded-carry proof | HIGH if outputs will be relied on physically | [README limitations](/Users/boss/Code/CaseInsertGenerator/README.md:122), [CHANGELOG](/Users/boss/Code/CaseInsertGenerator/CHANGELOG.md:36), synthetic manifests | Measure one real case, print a tolerance sample, then dry-fit/close and test retention with an inert load |
| R-02 | RISK | Published users still receive the pre-fix v0.1.0 behavior | MEDIUM | EV-010; existing baseline review F01–F12 | Complete review of v0.1.1 and publish through the existing process when authorized |
| R-03 | DEFECT | Custom catalog loader accepts nonfinite, boolean and invalid geometry values | LOW for normal bundled use | EV-011; [engine.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/engine.py:93) | Add finite numeric/type/range validation at the catalog boundary with malformed-input tests |
| R-04 | DEFECT | Themed README says manifest records an exact source commit, but no identifier is stored | LOW | [example README](/Users/boss/Code/CaseInsertGenerator/examples/themed-packs/README.md:19), [manifest source hashes](/Users/boss/Code/CaseInsertGenerator/examples/themed-packs/manifest.json:7106), [audit policy](/Users/boss/Code/CaseInsertGenerator/scripts/release_audit.py:288) | Describe the actual source hashes and verified-commit boolean; do not add forbidden historical metadata by default |
| R-05 | DEBT | CI cannot detect native CAD/GUI regressions by itself | MEDIUM | [CI workflow](/Users/boss/Code/CaseInsertGenerator/.github/workflows/ci.yml:12), native runner | Retain a dated native regression/smoke report per candidate; automate further only if recurring cost warrants it |
| R-06 | RISK | Sharing/moving FCStd alone does not preserve SVG sources needed for regeneration | MEDIUM for SVG-based handoffs | [README.md](/Users/boss/Code/CaseInsertGenerator/README.md:95), [engine.py](/Users/boss/Code/CaseInsertGenerator/freecad/CaseInsertGenerator/engine.py:845) | Keep referenced SVGs with the project and verify regeneration after relocation |
| R-07 | DEBT | Lid manifest lacks generator source hashes, runtime/version and reopen evidence present in themed records | LOW | [lid manifest](/Users/boss/Code/CaseInsertGenerator/examples/lid-panel/manifest.json), [generator](/Users/boss/Code/CaseInsertGenerator/scripts/generate_lid_panel_example.py:251) | Retain this assessment's fresh 2/2 lid reopen proof; align evidence on a later regeneration |
| R-08 | DEBT | Geometry, export/state and a large UI share the engine module | LOW | `engine.py` 5,525 lines; `_build_ui` starts at 3602 | Keep fixes local and regression-backed; no broad refactor required now |
| R-09 | RISK | Automated sample viewport is not clean presentation evidence | LOW | EV-012 sample PNG shows triangular surface/framing artifacts | Diagnose view/reference rendering only when preparing visuals; do not infer invalid exported solids |

**R-03 reproduction boundary:** the probe imports the real catalog loader with FreeCAD/Part import stubs, writes temporary JSON and exercises loader return/error behavior. All six malformed variants were accepted, including ordinary JSON strings `"NaN"` and `"Infinity"`. It does not show that normal bundled presets fail generation or that malformed inputs bypass later CAD validation.

**Blockers:** no owner input is needed to finish this assessment. Physical acceptance cannot be established without a real case/printer/test record. The Addon Manager and additional-platform lanes are unverified, not declared broken. Cosmetic improvements and broad restructuring are optional.

## 11. Decisions genuinely required from the project owner

**No owner decision is currently required** to complete this assessment or use the validated local software workflow. Publication/merge of the candidate and any external Addon Index submission remain separate authorized actions; this report does not initiate them or request a redundant approval. Choosing a particular real case becomes necessary when starting physical validation, but it is not needed to establish the present project state.

## 12. Recommended next actions

These are recommendations only; none was implemented during the assessment.

| Order | Action | Expected deliverable | Validation / stop condition | Dependencies |
|---|---|---|---|---|
| 1 | Complete review of the existing v0.1.1 update and use the established release process | Reviewed candidate delivered through the intended channel | Published tag/archive points to the approved source and matches its hashes; if source changes, refresh affected proof | Existing review/publication authorization |
| 2 | Validate one measured real-case configuration before relying on a full loaded insert | Measurements, tolerance sample and a short physical test record | Fits, closes without interference and retains an inert load under the intended handling | Case, printer/material and physical access |
| 3 | Correct custom catalog validation and the exact-source-commit wording in a narrow follow-up | Small validation/documentation patch | Malformed catalog fixtures reject; valid bundled catalog and relevant tests continue to pass | Separate implementation task |
| 4 | Preserve current evidence and use equal manifest detail for the next lid regeneration | Native logs/reopens tied to the candidate; improved lid source/runtime record when next regenerated | A reviewer can trace sample files, source hashes, runtime and reopen results | Existing artifact workflow |

### Explicitly defer or avoid

Avoid new features/example packs, commercial compatibility claims, a broad engine rewrite, cloud infrastructure, optimal-packing work and expansion of the support matrix before there is a demonstrated need. Do not turn the software assessment into physical approval, a security certification or an Addon Index acceptance claim.

## 13. Reproduction and handoff

### Environment

- **Operating system:** macOS 27.0, build 26A5425a, arm64.
- **Runtime / toolchain:** installed FreeCAD 1.1.3, build `145529fe741292ff0b3977a01195bf0247425794`; native Python 3.11.14; local standalone Python 3.14.7.
- **Required services:** none for product use. A real desktop/Qt environment is needed for native suites and screenshot generation.
- **Required secrets or credentials:** none for product/test use. `gh api` metadata checks used the existing GitHub authentication.
- **Hardware assumptions:** this machine supplies CAD/GUI proof only; no measured case, printer commissioning or test payload was available.
- **Isolation:** assessment profile at `/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/freecad-profile`; it contains a copied Mod package, not a change to the user's normal installation.

### Exact commands

Run the following from the authoritative checkout. No dependency installation is necessary on the assessed machine.

```sh
cd /Users/boss/Code/CaseInsertGenerator

# Source identity and standalone checks
 git rev-parse HEAD
 /opt/homebrew/opt/python@3.14/bin/python3.14 -m unittest discover -s tests -v
 /opt/homebrew/opt/python@3.14/bin/python3.14 scripts/release_audit.py
 /Users/boss/Documents/Codex/2026-09-05/caseinsertgenerator-full-review/work/test-venv/bin/reuse lint

# No compiled build step: install the folder in FreeCAD's user Mod directory,
# restart FreeCAD, select Case Insert Generator, then invoke its command.
# To preserve a normal profile, use the already prepared assessment profile:
 env PYTHONHOME=/Applications/FreeCAD.app/Contents/Resources \
   PYTHONPATH=/Applications/FreeCAD.app/Contents/Resources/lib \
   FREECAD_USER_HOME=/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/freecad-profile \
   PYTHONDONTWRITEBYTECODE=1 \
   /Applications/FreeCAD.app/Contents/Resources/bin/freecad \
   -u /Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/freecad-profile/user.cfg \
   -s /Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/freecad-profile/system.cfg

# In the launched FreeCAD Python console: native regression checks.
# The runner creates a fresh dated result directory and leaves FreeCAD open.
```

```python
import runpy
from pathlib import Path
root = Path('/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/freecad-profile/Mod/CaseInsertGenerator')
runner = runpy.run_path(str(root / 'tests/run_regressions.py'))
runner['run']('/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/regressions')
```

To reproduce the output in the same FreeCAD console, choose a **new** directory so previously generated files remain intact:

```python
import json, tempfile
import FreeCAD as App
from freecad.CaseInsertGenerator import engine as E
output = Path(tempfile.mkdtemp(prefix='cig-assessment-sample-'))
spec = json.loads((root / 'examples/themed-packs/01-field-mending/field-mending.json').read_text())['project']
doc = App.newDocument('AssessmentSample')
E.generate_project(spec, document=doc)
E.save_fcstd(str(output / 'field-mending.FCStd'), doc=doc)
E.export_step(str(output / 'field-mending.step'), doc=doc)
E.export_stl(str(output / 'field-mending.stl'), doc=doc)
print(output)
```

The exact first-run orchestration used for this assessment is retained as [native-assessment.FCMacro](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/native-assessment.FCMacro) and [smoke.FCMacro](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/smoke.FCMacro). These terminate their dedicated test process. The native wrapper writes fixed sample paths and should not be rerun unchanged over existing outputs; use a new output directory. The ordinary repository regression macro remains the repeatable test entry point.

### Expected results

Standalone tests report 129 passing tests; release audit reports `ok: true`, version 0.1.1 and 174 tracked files for the assessed tree. REUSE reports 171/171 applicable files before this report is tracked. Native suites report integration 23, validation 5, recovery 14 and GUI 21 passing checks. The sample returns eight parts, writes one FCStd and **numbered** `field-mending_part_01` through `_08` STEP/STL files, not one combined STEP/STL file. Unknown closed-lid clearance keeps printable lid exports disabled; after sufficient evidence and fresh generation, they become enabled.

### Known deviations

The outer macOS launcher used with `--help` opened a separate GUI/help process; that process was stopped, then the direct binary above worked. The generic `FreeCAD/Mod` path does not exist on this machine; the normal installation uses `FreeCAD/v1-1/Mod`. The FreeCAD MCP context reported `GuiUp=0`, so GUI verification used the dedicated native process. Both successful launches logged missing optional `3DconnexionNavlib`; no mouse-controller test was made. Windows, Linux, other FreeCAD releases, Addon Manager installation, native chooser interaction, slicer output and physical results were not reproduced.

## 14. Repository and artifact summary

### Material file map

All paths in this tree are relative to `/Users/boss/Code/CaseInsertGenerator`.

```text
CaseInsertGenerator.FCMacro             compatibility launcher
RunRegressionChecks.FCMacro            native test launcher
package.xml                            package and runtime metadata
README.md / CHANGELOG.md                product use and existing releases
NOTICE / REUSE.toml / LICENSES/         licence and importer provenance
SECURITY.md                            supported-version/reporting policy
.github/workflows/ci.yml                unit, tree and REUSE jobs
freecad/CaseInsertGenerator/
  init_gui.py / commands.py / bridge.py startup and API routing
  __init__.py                          public exports and version
  engine.py                            geometry, document state, exports, UI
  project_model.py                     schema, validation and layouts
  svg_import.py                        SVG preflight and normalization
  case_catalog.json                    three synthetic demonstration presets
Resources/icons/                       workbench icon
scripts/                               example generation and artifact audits
tests/                                 standalone and native regression suites
examples/themed-packs/                  23 editable synthetic example packs
examples/lid-panel/                     synthetic lid model and evidence
docs/FREECAD_ADDON_SUBMISSION.md       existing submission draft
dist/                                  ignored historical v0.1.0 package
PROJECT_ASSESSMENT.md                   this new report
artifacts/full_assessment/2026-09-06/    ignored evidence, test profile and outputs
```

### Git state

The source remained at `1c7a1c8bdfcf8ca362d08cc7ebadb57a342f19df` throughout the native snapshot and final source checks. It was initially clean. This task adds only the untracked report outside ignored evidence; tracked product/example files were not changed. The other three existing review worktrees were inspected only by `git worktree list` and remain in place.

The upstream tracking annotation appeared during this assessment, and live GitHub showed a recent candidate push/PR CI run. These are externally observed state changes; this assessment did not push, fetch/update refs, open a PR or merge. The final repository snapshot and remote query timestamps control the delivery claims. Public `main` and `v0.1.0` still resolved to `4b847ac` when queried.

### Generated outputs

| Output | Path | Generated by | Verified? | Notes |
|---|---|---|---|---|
| Assessment | [PROJECT_ASSESSMENT.md](/Users/boss/Code/CaseInsertGenerator/PROJECT_ASSESSMENT.md) | This assessment using retained template | Structural, link and render checks | Markdown is the requested source of truth |
| Native evidence | [native-assessment.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/native-assessment.json) | Dedicated wrapper | VERIFIED | Source identity, runtime, suite counts, reopens and sample hashes |
| Editable sample | [field-mending.FCStd](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/sample_outputs/field-mending.FCStd) | `engine.generate_project` / `save_fcstd` | Saved; sample solids checked; all distributed examples separately reopened | Synthetic; no physical-fit claim |
| 16 exchange files | [sample_outputs](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/sample_outputs) | Separate STEP/STL exports | VERIFIED | Eight valid STEP solids; eight closed STL meshes |
| Fresh GUI and sample PNGs | [GUI](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke.png), [contact sheet](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-regressions-contact-sheet.png), [sample](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/sample_outputs/field-mending.png) | FreeCAD captures and QA contact sheet | Inspected | Sample visual limitations recorded |
| Evidence manifest | [EVIDENCE_MANIFEST.json](/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/EVIDENCE_MANIFEST.json) | Assessment consistency check | SHA-256 inventory | Keep with report when handing off |

Existing candidate ZIP SHA-256 is **`04172467126191d5e01052c11f1c0bda85e37e15cee28cdf40a4e08e32885336`**, size **9,606,547 bytes**, with **174 files and 39 directory entries**. Its source/package identity was verified; it was not rebuilt or uploaded here. Existing `examples/` totals **48 FCStd, 51 PNG, 26 JSON, 2 Markdown and 1 SVG**. Artifact counts do not measure project completion.

## 15. Assumptions and unknowns

| Item | Assumption or unknown | Why evidence is insufficient | How to resolve |
|---|---|---|---|
| Original brief | Current README/release scope is the assessment authority | Initiating brief is absent here | Supply original brief only if historical scope attribution matters |
| Real case and payload | Unknown | All supplied examples and test dimensions are synthetic | Measure and record a chosen case/payload |
| Physical outcomes | Unknown; documented as untested | No fit/closure/carry/material evidence | Run physical validation separately |
| Complete platform support | Unknown outside this macOS/FreeCAD build | One native environment exercised | Run the same suites on each actually targeted environment |
| Addon Index/Manager acceptance | Unknown | Draft exists; no installation/submission reproduced | Separate installation and submission verification |
| Overall completion percentage | Not reliably calculable | No agreed weighted acceptance definition | Keep capability status rather than invent a percentage |
| Exact historical source commit in manifests | Unavailable from manifest alone | Hashes and a verified boolean replace a commit ID | Describe stored hashes accurately; use Git/file comparisons when needed |
| Large-input behavior | Unknown | No stress/performance suite run | Test only when a representative user workload requires it |
| Sample viewport artifacts | Observed; cause unknown | CAD/mesh validation passes, but renderer/reference behavior not isolated | Reproduce view behavior with controlled visibility/settings |
| Live release status after snapshot | May change | Another task was active on the candidate branch remotely | Recheck GitHub before release actions |

## 16. Machine-readable summary

```json
{
  "project_name": "Case Insert Generator",
  "report_mode": "full_assessment",
  "assessed_at": "2026-09-06T03:16:05+10:00",
  "branch": "codex/review-polish",
  "base_commit": "4b847ac60defd06efdfaa16f814ee1d77bc8646c",
  "head_commit": "1c7a1c8bdfcf8ca362d08cc7ebadb57a342f19df",
  "overall_health": "AMBER",
  "delivery_confidence": "MEDIUM",
  "completion_percent": null,
  "completion_method": "not reliably calculable",
  "main_user_path_status": "VERIFIED",
  "progress_since_baseline": "MATERIAL_FORWARD_PROGRESS",
  "scope_drift": "ON_SCOPE",
  "acceptance_criteria": [
    {
      "id": "AC-01",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-005"
      ]
    },
    {
      "id": "AC-02",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-006",
        "EV-007"
      ]
    },
    {
      "id": "AC-03",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-006"
      ]
    },
    {
      "id": "AC-04",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-006",
        "EV-007"
      ]
    },
    {
      "id": "AC-05",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-005",
        "EV-006"
      ]
    },
    {
      "id": "AC-06",
      "status": "VERIFIED",
      "evidence_ids": [
        "EV-006",
        "EV-008",
        "EV-009"
      ]
    },
    {
      "id": "AC-07",
      "status": "PARTIAL",
      "evidence_ids": [
        "EV-010"
      ]
    },
    {
      "id": "AC-08",
      "status": "UNKNOWN",
      "evidence_ids": []
    }
  ],
  "blocking_issue_ids": [],
  "highest_priority_risk_ids": [
    "R-01",
    "R-02",
    "R-05",
    "R-06"
  ],
  "owner_decisions_required": [],
  "recommended_next_actions": [
    "Complete review and authorized delivery of the existing v0.1.1 candidate.",
    "Validate one measured case with a tolerance sample, dry closure and inert-load retention test.",
    "Fix malformed custom catalog validation and inaccurate source-commit wording in a separate narrow task.",
    "Retain native evidence and align lid-manifest provenance on the next regeneration."
  ],
  "evidence_files": [
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/repository-state.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/checks.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/unit-tests.log",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/release-audit.log",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/reuse-lint.log",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/installation.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke-result.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/native-assessment.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/example-integrity.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/package-verification.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/remote-status.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/catalog-probe.json",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-smoke.png",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/gui-regressions-contact-sheet.png",
    "/Users/boss/Code/CaseInsertGenerator/artifacts/full_assessment/2026-09-06/EVIDENCE_MANIFEST.json"
  ],
  "verification_counts": {
    "standalone_tests": {
      "passed": 129,
      "total": 129,
      "failed": 0,
      "skipped": 0
    },
    "native_checks": {
      "passed": 63,
      "total": 63,
      "failed": 0,
      "skipped": 0
    },
    "example_cold_reopens": {
      "passed": 48,
      "total": 48
    },
    "step_solids": {
      "passed": 8,
      "total": 8
    },
    "stl_closed_meshes": {
      "passed": 8,
      "total": 8
    },
    "candidate_file_matches": {
      "passed": 174,
      "total": 174
    }
  },
  "physical_validation_status": "UNKNOWN",
  "published_release_at_snapshot": "v0.1.0",
  "product_source_modified": false
}
```

## 17. Assessment limitations

This is a full assessment of the current local project and a bounded live distribution snapshot, not an exhaustive security audit, legal opinion, manufacturing approval or proof of all parameter combinations. All seven runtime Python modules, relevant tests/scripts, repository documentation/metadata, current Git state, existing release evidence and example files were inspected. The older private checkout and unrelated worktree contents were not inspected or changed.

Fresh native checks establish software behavior on **macOS arm64 with FreeCAD 1.1.3**. Native chooser/confirmation presentation was mocked in the GUI regressions; real widgets, controller actions, CAD, saving/reopening and exports were used. The fresh launch tested a byte-identical Mod-folder copy of current source; it did not execute Addon Manager installation. Distributed examples were freshly reopened and checked for non-null valid shapes and loadable project specifications, but not all regenerated, dimensionally compared against real objects or physically printed. The fresh sample's saved FCStd was not independently cold-reopened after export; distributed-example reopen and GUI save/reopen evidence are separate.

The supplied test runner's integration temporary outputs are not all retained after its internal cleanup; durable GUI outputs and the separately generated sample are retained. Historical manifest assertions are labeled as historical and not counted again as new runs. All acceptance statuses apply to the bounded criteria stated here, not universal correctness. No physical measurement, slicer, print, lid-closure, retention, carrying, thermal or material test was performed. The default sample viewport had visual artifacts, recorded rather than omitted.

The requested Markdown format intentionally replaces Word page furniture while preserving the retained template's report structure, status vocabulary, evidence tables and JSON contract. Supporting files are local and Git-ignored; share them alongside this report if independent reproduction or visual review is required. No product fixes, source commits, merges or publications were made during this assessment.
