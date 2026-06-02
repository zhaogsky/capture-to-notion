# Capture to Notion Capability Matrix

This matrix records the current support boundary for the Capture to Notion backend and Skill orchestration. It is an implementation-facing checklist: every supported capability should have tests, partial capabilities must state their boundary, and unsupported capabilities must be surfaced as warnings/blockers instead of silently pretending success.

## Capability Status Legend

- **Supported**: implemented and covered by focused tests.
- **Partial**: implemented for a defined subset; unsupported cases must produce warnings, blockers, or `not_guaranteed` status.
- **Unsupported**: intentionally not implemented; code should not silently execute or claim success.

## Target and Location Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| Plain page child capture | Supported | Page-only targets create ordinary child pages with body blocks. | `tests/test_planner.py`, `tests/test_capture_apply.py` plain page / child page tests |
| Plain page append/update | Supported | Existing plain page operations live-check the page parent before appending planned blocks and verify title/body expectations. | `tests/test_capture_apply.py` child page verification and append safety tests |
| Data source page create/update | Supported | Writes use Notion data source parents, not database objects. | `tests/test_writer.py`, `tests/test_capture_apply.py` |
| View-backed data source write | Supported | Writes to the underlying data source and carries view context in plan/review. | `tests/test_planner.py`, `tests/test_view_constraints.py` |
| View constraint application | Partial | Only safely translatable filters/quick filters are converted into write constraints. Unsupported view rules remain warnings. | `tests/test_view_constraints.py`, `tests/test_planner.py` |
| View visibility verification | Partial | Apply verification reports `satisfied`, `failed`, or `not_guaranteed` for known constraints; it does not query actual Notion view membership. | `tests/test_capture_apply.py`, `tests/test_view_constraints.py` |
| Existing page update safety | Supported | Apply retrieves the live page and rejects archived/in_trash pages, parent data source mismatch, and explicit title mismatch. | `tests/test_capture_apply.py` update safety tests |
| Hierarchical target semantics | Partial | Backend exposes `target_path` / `visual_path`; Skill/AI must decide whether the path matches the user's words. | `tests/test_preflight.py`, `tests/test_planner.py`, `SKILL.md` |

## Property Type Capabilities

| Property type / behavior | Status | Boundary | Test coverage |
|---|---:|---|---|
| `title` | Supported | Writes title rich text. | `tests/test_schema.py` |
| `rich_text` | Supported | Writes single rich text value. | `tests/test_schema.py`, `tests/test_capture_apply.py` |
| `number` | Supported | Numeric strings are coerced for integer-like values. | `tests/test_schema.py`, `tests/test_capture_apply.py` |
| `select` | Supported | Writes option name; does not create or validate options beyond schema-driven checks. | `tests/test_schema.py`, `tests/test_view_constraints.py` |
| `status` | Supported | Writes status name; view status groups are normalized only when safe. | `tests/test_schema.py`, `tests/test_view_constraints.py` |
| `multi_select` | Supported | Writes list/string option names. | `tests/test_schema.py` |
| `date` | Supported | Supports scalar start date and `{start,end}` objects. | `tests/test_schema.py` |
| `checkbox` | Supported | Supports booleans and common textual booleans. | `tests/test_schema.py` |
| `url`, `email`, `phone_number` | Supported | Writes plain string values. | `tests/test_schema.py` |
| `files` external URL | Supported | `attach_external_url` writes external file objects. | `tests/test_assets.py`, `tests/test_schema.py` |
| `files` uploaded file | Supported | `download_and_attach` requires download/upload success; failures do not fallback to external URL. | `tests/test_assets.py`, `tests/test_writer.py`, `tests/test_capture_apply.py` |
| `relation` | Supported | Resolves IDs/exact title matches and can create missing targets when policy allows. | `tests/test_relations.py`, `tests/test_planner.py` |
| `people` | Supported | Supports user id passthrough, email/name resolution, candidates, and explicit decisions. | `tests/test_people.py`, `tests/test_planner.py`, `tests/test_notion_adapter.py` |
| `formula` / `rollup` writing | Unsupported | Read-only/computed fields are never written. | `tests/test_schema.py` |
| `formula` / `rollup` verification | Partial | Present and common scalar expected values are verified; complex rollup arrays are present-only. | `tests/test_capture_apply.py` |

## Relation Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| Relation ID passthrough | Supported | Existing page IDs are accepted directly. | `tests/test_relations.py` |
| Exact title resolution | Supported | Exact title query resolves single matches. | `tests/test_relations.py` |
| Missing relation target creation | Supported | Only when profile/target relation policy has `create_missing`. | `tests/test_relations.py`, `tests/test_planner.py` |
| Ambiguous relation candidates | Supported | Multiple matches produce structured candidates; Skill must ask the user to choose/create/skip. | `tests/test_relations.py`, `tests/test_planner.py`, `SKILL.md` |
| Relation decisions | Supported | `choose_existing` / `use_existing` selects a page ID; `skip` clears the relation. | `tests/test_relations.py`, `tests/test_planner.py` |
| Relation target completion | Supported | Completion operations update resolved relation target pages using scanned schema. | `tests/test_writer.py`, `tests/test_capture_apply.py` |
| Relation fuzzy matching | Unsupported | Backend does not guess fuzzy matches; candidates must come from explicit query results. | `tests/test_relations.py` ambiguity tests |

## People Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| User ID passthrough | Supported | IDs and lists of IDs pass through as people values. | `tests/test_people.py`, `tests/test_schema.py` |
| Email resolution | Supported | Single user match resolves to user ID. | `tests/test_people.py`, `tests/test_planner.py` |
| Name/display name candidates | Supported | Ambiguous matches produce structured candidates. | `tests/test_people.py`, `tests/test_planner.py` |
| People decisions | Supported | `choose_existing` / `use_existing` selects a user ID; `skip` clears the people field. | `tests/test_people.py`, `tests/test_planner.py` |
| Real user search endpoint | Partial | Current search is based on adapter user listing/filtering; no direct real write is performed in tests. | `tests/test_notion_adapter.py`, `tests/test_people.py` |

## Asset Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| External file URL attach | Supported | Explicit `attach_external_url` writes external file objects without upload. | `tests/test_assets.py` |
| Download and upload | Supported | Explicit `download_and_attach` downloads, caches, uploads, and writes `file_upload` objects only on success. | `tests/test_assets.py`, `tests/test_writer.py` |
| Failure semantics | Supported | Download/upload unavailable/failure does not write external URL and marks verification failed. | `tests/test_assets.py`, `tests/test_capture_apply.py`, `tests/test_writer.py` |
| Image byte type inference | Supported | JPEG, PNG, WebP are inferred from bytes. | `tests/test_assets.py` |
| Generic file type inference | Supported | GIF, PDF, and AVIF are inferred from bytes. | `tests/test_assets.py` |
| Upload metadata | Supported | Asset results include uploaded name, MIME type, cache path, and file upload ID when available. | `tests/test_assets.py`, `tests/test_notion_adapter.py` |
| Page cover upload | Partial | Page cover still uses cover/source URL semantics; files fields use upload semantics. | `tests/test_writer.py`, `tests/test_capture_apply.py` |
| Content hash/size validation | Unsupported | No hash or size validation is currently performed. | Not covered |

## Template and Default Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| Data source template facts scan | Supported | Scanner/graph can preserve template facts such as ID/title/default status when available. | `tests/test_notion_graph.py`, `tests/test_scanner.py` |
| Plan template options | Supported | Plan review exposes template options and decisions. | `tests/test_planner.py` |
| Template skip decision | Supported | User can explicitly choose not to use a template. | `tests/test_planner.py` |
| Template use decision | Partial | Plan records the selection but marks actual apply as unsupported when writer cannot apply templates. | `tests/test_planner.py`, `SKILL.md` |
| Actual Notion template application | Unsupported | Writer must not pretend to apply templates until the backend supports it. | `tests/test_planner.py` unsupported apply status tests |
| Profile defaults | Supported | Profile/default parser values can populate records when configured. | `tests/test_planner.py` |

## Verification Capabilities

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| Structured page field verification | Supported | Checks presence and expected values by mapped property type. | `tests/test_capture_apply.py` |
| Relation ID verification | Supported | Verifies actual relation page IDs against expected IDs. | `tests/test_capture_apply.py` |
| Files URL verification | Supported | Checks files URLs and accessibility; uploaded files do not need to match source URL. | `tests/test_capture_apply.py` |
| Asset failure verification | Supported | Asset failure warnings force overall verification failure. | `tests/test_capture_apply.py` |
| Plain page verification | Supported | Checks title, body block count, and text samples. | `tests/test_capture_apply.py` |
| Completion page verification | Supported | Verifies relation target completion pages separately. | `tests/test_capture_apply.py` |
| View visibility verification | Partial | Verifies known constraints; unsupported constraints yield `not_guaranteed`. | `tests/test_capture_apply.py` |
| Computed field verification | Partial | Formula/rollup scalar values are supported; complex rollups are present-only. | `tests/test_capture_apply.py` |

## Apply Safety and Orchestration Boundaries

| Capability | Status | Boundary | Test coverage |
|---|---:|---|---|
| Explicit confirmation gate | Supported | `capture apply` requires confirmed plans and workflow checks. | `tests/test_capture_apply.py`, `tests/test_workflow_gate.py` |
| Context integrity | Supported | Apply checks target/view/data source integrity before mutation. | `tests/test_capture_apply.py` |
| Update page safety | Supported | Existing page updates are live-checked before mutation. | `tests/test_capture_apply.py` |
| Safety failure recovery | Unsupported by backend | Backend stops and reports invariant failure; Skill/AI rebuilds fresh input/plan. | `SKILL.md`, `tests/test_capture_apply.py` |
| Notion MCP fallback | Unsupported | Capture/scan/write/verify must stay inside this CLI/Skill backend. | `SKILL.md` |

## Current Known Unsupported Scenarios

- Applying real Notion templates during page creation/update.
- Verifying actual Notion view membership beyond known filter constraints.
- Fuzzy relation matching or automatic choice among ambiguous relation candidates.
- Writing formula, rollup, created/edited, unique ID, or other read-only properties.
- Deep expected-value comparison for complex rollup arrays.
- File hash/size/content validation after upload.
- Automatic recovery from archived/missing/wrong-parent existing pages.

## Maintenance Rule

When a capability moves from **Partial** or **Unsupported** to **Supported**, add or update tests in the mapped test files and update this matrix in the same change set.
