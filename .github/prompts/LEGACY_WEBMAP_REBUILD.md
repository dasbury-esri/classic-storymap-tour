## Legacy Web Map Rebuild Effort Summary

### Status
Paused by request after multiple implementation iterations. Current recommendation is to pause further custom migration engineering until Esri Technical Support provides a more streamlined supported workflow, or until a viable Map Viewer Classic path is identified for edge-case recovery.

### Objective
Migrate legacy Web Map items (notably version < 2.0) to modernized maps while preserving operational behavior and minimizing downstream app breakage.

### Scope Attempted
- Detect legacy maps and prioritize them by impact.
- Rebuild legacy web maps as new modernized items.
- Preserve critical payloads (featureCollection/map notes).
- Optionally replace basemap and problematic legacy layer URLs.
- Rewire dependent web mapping applications to new map IDs.
- Validate rewired apps with smoke tests.

### Key Artifacts Created
- `misc/webmap_version_audit.py`
  - Org-wide scan for legacy web maps and app impact metadata.
- `misc/webmap_impact_triage_summary.py`
  - Generates markdown + action CSV for practical triage.
- `misc/migrate_legacy_webmaps.py`
  - Main migration logic (rebuild + optional rewire).
- `misc/rebuild_legacy_webmaps.py`
  - Step 1 wrapper: rebuild only, report mapping.
- `misc/rewire_dependent_apps.py`
  - Step 2 wrapper: dependent app rewiring from mapping report.
- `misc/smoke_test_rewired_apps.py`
  - Post-rewire smoke checks for stale old IDs.
- `misc/layer_replacements.example.json`
  - Example replacement rules for operational layer URL swaps.

### High-Value Outputs Generated
- `misc/legacy_webmaps_impact.csv`
- `misc/legacy_webmaps_triage_full_org.md`
- `misc/legacy_webmaps_actions_full_org.csv`
- `misc/legacy_webmap_rebuild_report.csv`
- `misc/legacy_webmap_rewire_report_dryrun.csv`
- `misc/legacy_webmap_rewire_smoke_test.csv`

### Implementation Notes
1. Two-step migration architecture was established:
- Step 1: rebuild/report.
- Step 2: rewire dependents using `id_map_json` from step 1 report.

2. Compatibility workarounds added for ArcGIS API for Python 2.4.2 behavior:
- Item creation path moved toward Folder-based add flow with `ItemProperties` handling.
- Additional error handling and fallback logic added around item creation.

3. Layer replacement support added:
- Rules can match by layer id/url/url-contains.
- Replacement can come from direct URL or replacement item ID.

### Example Legacy Map Investigated
- Source web map: `75d536a69270440a952ae2e5c0143242`
- Observed legacy indicators:
  - `version: 1.5`
  - legacy `widgets` block
  - old service URL references (including LandsatGLS URL)

### Example Replacement Flow Added
- Replacement candidate item for Landsat layer: `52484b28322542ce97cff79046978f9e`
- Rule-driven replacement mechanism now exists and was validated in dry run reporting.

### What Worked Reliably
- Org-scale legacy identification and impact triage.
- Actionable prioritization output for migration planning.
- Step split (rebuild then rewire) with reports between phases.
- Dry-run and smoke-test workflows for safer validation.

### What Became Costly / Fragile
- End-to-end legacy rebuild normalization for all edge cases is high-effort.
- ArcGIS API behavior differences and deprecations increase maintenance overhead.
- Some dependency discovery paths can undercount references in targeted mode.
- Operational confidence for broad batch conversion still requires manual oversight.

### Decision: Pause
Given engineering effort vs. confidence tradeoff, pausing this track is reasonable until:
- Esri Technical Support publishes a clearer supported conversion workflow for legacy web maps (< 2.0), or
- a dependable/approved Classic recovery workflow is available for difficult cases.

### Recommended Restart Conditions
Resume only when one of the following is true:
1. Esri Support provides a supported conversion sequence with known caveats.
2. A tested tool/workflow can convert legacy map JSON with low-touch remediation.
3. Priority business cases justify controlled manual migration per map with strict QA gates.

### Suggested Next Actions During Pause
1. Preserve current scripts and reports as a reproducible baseline.
2. Continue using triage outputs for owner outreach and risk communication.
3. Escalate representative failing maps and reports to Esri Support for authoritative guidance.
4. Re-run only discovery/triage scripts periodically to monitor drift while migration is paused.

### Handoff Notes for Esri Technical Support
Please review:
- `misc/migrate_legacy_webmaps.py`
- `misc/rebuild_legacy_webmaps.py`
- `misc/rewire_dependent_apps.py`
- `misc/smoke_test_rewired_apps.py`
- `misc/legacy_webmap_rebuild_report.csv`
- `misc/legacy_webmap_rewire_report_dryrun.csv`

Focus questions:
- Best-practice supported path to modernize legacy web map JSON (<2.0).
- Recommended handling of legacy widget blocks and old service references.
- Supported approach for preserving map notes/featureCollections during conversion.
- Official guidance for downstream app rewiring and validation expectations.
