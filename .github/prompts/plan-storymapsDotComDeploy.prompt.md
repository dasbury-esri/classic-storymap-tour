## Deployment Implementation Checklist: Classic Map Tour on IIS

### Goal
Produce a repeatable deploy artifact from source and host a viewer-only Classic Map Tour endpoint at /templates/maptour that supports public appid query loading.

### Scope
- In scope: build pipeline validation, IIS deploy package, public appid behavior, viewer-only hardening, release workflow.
- Out of scope: public builder/edit workflow parity and broad UX redesign.

### Owners
- App owner: you (feature decisions, review, final sign-off)
- Build owner: repo maintainer (build scripts, packaging, docs)
- Infra owner: IIS admin (site config, deployment, cache, monitoring)
- QA owner: shared between app owner and repo maintainer

### Work Checklist

Tag legend:
- Effort: XS (<= 0.5d), S (1d), M (2-3d), L (4-5d)
- Depends-On: task IDs that must complete first

#### Phase 1: Build Baseline
- [x] [T1] Task: Verify production build emits deploy artifact from MapTour source.
	- Owner: Build owner
	- Effort: S
	- Depends-On: None
	- Deliverable: Successful npx grunt output under MapTour/deploy
	- Acceptance criteria:
		- MapTour/deploy/index.html exists
		- Minified app bundles exist in deploy output
		- Resource folders required by runtime are present

- [x] [T2] Task: Document canonical build commands and expected output layout.
	- Owner: Build owner
	- Effort: XS
	- Depends-On: T1
	- Deliverable: Updated build section in repo docs
	- Acceptance criteria:
		- A new contributor can run build with no undocumented steps
		- Output tree is explicitly described

#### Phase 2: Public appid Runtime Policy
- [x] [T3] Task: Ensure runtime honors appid from URL query under IIS path /templates/maptour.
	- Owner: Build owner
	- Effort: M
	- Depends-On: T1
	- Deliverable: Deployment profile/config that allows public appid loading
	- Acceptance criteria:
		- /templates/maptour/index.html?appid=2c62acf3468c4cbbba6b82f1035bfe22 loads successfully
		- Additional known valid appids load without code changes

- [x] [T4] Task: Keep static default appid unset so query appid is authoritative.
	- Owner: Build owner
	- Effort: XS
	- Depends-On: T3
	- Deliverable: Config verification in deploy output
	- Acceptance criteria:
		- No hardcoded appid blocks query parameter behavior

#### Phase 3: Viewer-Only Hardening
- [x] [T5] Task: Disable or hide edit/builder entry points in deployed experience.
	- Owner: Build owner
	- Effort: M
	- Depends-On: T3
	- Deliverable: Viewer-only runtime behavior
	- Acceptance criteria:
		- Requests with edit flags do not open builder mode
		- UI does not expose edit actions for public users

- [x] [T6] Task: Define behavior for edit URLs (block, redirect, or graceful fallback).
	- Owner: App owner
	- Effort: XS
	- Depends-On: T5
	- Deliverable: Explicit policy and implementation note
	- Acceptance criteria:
		- Behavior is consistent and documented
		- Smoke test confirms selected behavior

#### Phase 4: IIS Packaging and Deployment
- [x] [T7] Task: Define deploy boundary as MapTour/deploy contents only.
	- Owner: Build owner
	- Effort: XS
	- Depends-On: T1, T3, T5
	- Deliverable: Package checklist for release handoff
	- Acceptance criteria:
		- No source-only files are required at runtime

- [x] [T8] Task: Configure IIS site/app for /templates/maptour.
	- Owner: Infra owner
	- Effort: M
	- Depends-On: T7
	- Deliverable: IIS configuration and publish process
	- Build-owner status: deploy artifact now includes web.config (default document and static MIME mappings).
	- Acceptance criteria:
		- index.html served as default document
		- Static assets resolve under nested path
		- Cache headers applied per policy

#### Phase 5: Release and Operations
- [x] [T9] Task: Define release workflow (sync upstream, build, test, publish).
	- Owner: Build owner
	- Effort: S
	- Depends-On: T2, T7, T8
	- Deliverable: Release runbook
	- Acceptance criteria:
		- Each release records commit SHA and build date
		- Rollback steps are documented

- [x] [T10] Task: Define smoke test suite for every release.
	- Owner: QA owner
	- Effort: S
	- Depends-On: T3, T5, T8
	- Deliverable: Short test checklist
	- Acceptance criteria:
		- Viewer load with known appid passes
		- Invalid or missing appid behavior is validated
		- edit URL behavior matches viewer-only policy

### Risks and Mitigations
- Risk: Legacy self-hosted logic may gate appid in production.
	- Mitigation: Add IIS-specific deployment profile and test appid query behavior before publish.

- Risk: Hardcoded hosted path assumptions may not match /templates/maptour.
	- Mitigation: Validate path handling in deployed index and runtime URL logic.

- Risk: Viewer-only lockout is incomplete, exposing builder links.
	- Mitigation: Add explicit negative tests for edit flags and builder actions.

- Risk: Upstream sync regressions in retired codebase.
	- Mitigation: Keep patch set minimal and maintain a release checklist tied to upstream merge points.

### Exit Criteria
- [x] Build is reproducible on clean environment.
- [x] IIS endpoint serves viewer successfully with known appid.
- [x] Viewer-only behavior is enforced and documented.
- [x] Release runbook and smoke tests are committed.
- [x] Operations handoff completed.

### Suggested Execution Order
1. Phase 1 build baseline
2. Phase 2 appid behavior
3. Phase 3 viewer-only hardening
4. Phase 4 IIS deployment setup
5. Phase 5 release runbook and recurring smoke tests

### Suggested PR Batches and Merge Order

#### PR1: Build Baseline and Documentation
- Tasks: T1, T2
- Intent: lock in reproducible deploy artifact generation before runtime behavior changes.
- Merge gate:
	- Build passes and deploy artifact is produced locally
	- Build docs are updated and reviewed

#### PR2: Runtime Policy and Viewer-Only Hardening
- Tasks: T3, T4, T5, T6
- Intent: enable public appid query loading and enforce viewer-only behavior for public endpoint.
- Merge gate:
	- Known appid renders on test environment
	- edit URL behavior matches policy and does not expose builder workflow

#### PR3: IIS Deployment and Operations Runbook
- Tasks: T7, T8, T9, T10
- Intent: package for IIS, deploy to target path, and operationalize release/testing flow.
- Merge gate:
	- IIS endpoint smoke tests pass for valid appid and edit-path handling
	- Release runbook and smoke test checklist are committed

#### Merge Sequence
1. Merge PR1 to establish stable build baseline.
2. Rebase PR2 on latest master, then merge after runtime/viewer-only validation.
3. Rebase PR3 on latest master, then merge once IIS deployment validation and ops docs are complete.

#### Optional PR Split (if PR2 gets too large)
- PR2a: T3, T4 (appid behavior only)
- PR2b: T5, T6 (viewer-only enforcement)
- Merge order in that case: PR1 -> PR2a -> PR2b -> PR3
