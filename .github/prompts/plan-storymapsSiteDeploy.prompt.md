## Deployment Implementation Checklist: Classic Storymaps Site on IIS

### Goal
Deploy a multi-app Classic Storymaps landing and viewer-page experience at /templates/classic-storymaps with clear per-app launch guidance, appid-first validation, and viewer-only behavior.

### Scope
- In scope: landing page architecture, per-app viewer pages, shared validation UX, IIS package/deploy workflow, release smoke tests.
- Out of scope: modifying upstream app runtimes beyond necessary viewer-only launch constraints and full legacy map rebuild automation.

### Owners
- Product owner: you (app selection, UX decisions, acceptance)
- Build owner: repo maintainer (page templates, build/package scripts, docs)
- Infra owner: IIS admin (publish path, caching, static content policy)
- QA owner: shared between product owner and repo maintainer

### Work Checklist

Tag legend:
- Effort: XS (<= 0.5d), S (1d), M (2-3d), L (4-5d)
- Depends-On: task IDs that must complete first

#### Phase 1: Information Architecture and URL Contract
- [ ] [S1] Task: Define site map and route contract for /templates/classic-storymaps.
  - Owner: Product owner
  - Effort: S
  - Depends-On: None
  - Deliverable: Route matrix for landing and per-app pages
  - Acceptance criteria:
    - Landing route and each phase-1 app route are explicit
    - URL param precedence is documented (appid-first, optional webmap where supported)

- [ ] [S2] Task: Define phase-1 app set and adapter responsibilities.
  - Owner: Product owner
  - Effort: S
  - Depends-On: S1
  - Deliverable: Adapter matrix for Map Tour, Map Journal, Swipe/Spyglass
  - Acceptance criteria:
    - Each app defines accepted params and unsupported cases
    - Viewer-only policy is called out per app

#### Phase 2: Shared UX and Validation Layer
- [ ] [S3] Task: Implement shared input/validation module used by all app pages.
  - Owner: Build owner
  - Effort: M
  - Depends-On: S2
  - Deliverable: Reusable JS module for parsing and validating appid/webmap input
  - Acceptance criteria:
    - Empty, malformed, and unsupported input paths return actionable messages
    - Validation responses are consistent across app pages

- [ ] [S4] Task: Implement shared page shell (header, breadcrumb, help text, launch CTA, error panel).
  - Owner: Build owner
  - Effort: M
  - Depends-On: S1
  - Deliverable: Shared HTML/CSS/JS shell components
  - Acceptance criteria:
    - Desktop and mobile layouts are verified
    - Brand assets and thumbnails render correctly

#### Phase 3: Landing and Per-App Pages
- [ ] [S5] Task: Build landing page with app thumbnails and deep links.
  - Owner: Build owner
  - Effort: S
  - Depends-On: S4
  - Deliverable: /templates/classic-storymaps/index.html
  - Acceptance criteria:
    - Cards for all phase-1 apps display correctly
    - Card links route to the right app pages

- [ ] [S6] Task: Build Map Tour, Map Journal, and Swipe/Spyglass pages.
  - Owner: Build owner
  - Effort: M
  - Depends-On: S3, S4
  - Deliverable: Per-app pages with adapter-driven validation and launch behavior
  - Acceptance criteria:
    - Known good appid launches correctly for each app
    - Unsupported input shows app-specific guidance and org-search links

#### Phase 4: IIS Packaging and Deployment
- [ ] [S7] Task: Define deploy boundary and publish process for /templates/classic-storymaps.
  - Owner: Build owner
  - Effort: XS
  - Depends-On: S5, S6
  - Deliverable: Packaging checklist and deployment notes
  - Acceptance criteria:
    - Runtime does not require source-only files
    - Static assets resolve correctly from nested IIS path

- [ ] [S8] Task: Configure IIS for route assets, cache policy, and fallback behavior.
  - Owner: Infra owner
  - Effort: M
  - Depends-On: S7
  - Deliverable: IIS config checklist and validation transcript
  - Acceptance criteria:
    - Landing and all app pages serve from target path
    - Cache behavior matches documented policy

#### Phase 5: Release and Operations
- [ ] [S9] Task: Define release workflow (sync, build/package, test, publish, rollback).
  - Owner: Build owner
  - Effort: S
  - Depends-On: S7, S8
  - Deliverable: Release runbook
  - Acceptance criteria:
    - Release notes capture commit SHA and deployment timestamp
    - Rollback instructions are explicit and tested

- [ ] [S10] Task: Define smoke suite for every release.
  - Owner: QA owner
  - Effort: S
  - Depends-On: S6, S8
  - Deliverable: Smoke checklist covering valid, invalid, missing, and unsupported input
  - Acceptance criteria:
    - All phase-1 app pages pass positive and negative tests
    - Viewer-only constraints are validated

### Risks and Mitigations
- Risk: Map Tour-specific assumptions leak into other classic app pages.
  - Mitigation: Enforce adapter interface with app-specific validation contracts.

- Risk: IIS path handling breaks relative assets in nested routes.
  - Mitigation: Run path/asset checks on each page in pre-release smoke tests.

- Risk: Ambiguous input behavior (appid vs webmap) confuses users.
  - Mitigation: Keep appid authoritative and show explicit per-app support text.

- Risk: Upstream classic app drift introduces launch regressions.
  - Mitigation: Track upstream source baseline and keep patches minimal and documented.

### Exit Criteria
- [ ] Landing page and phase-1 app pages are deployed under /templates/classic-storymaps.
- [ ] Shared validation and error UX is consistent and documented.
- [ ] Viewer-only launch behavior is enforced for phase-1 apps.
- [ ] Release runbook and smoke tests are committed.
- [ ] Operations handoff is complete.

### Suggested Execution Order
1. Phase 1 route and adapter contract
2. Phase 2 shared UX/validation layer
3. Phase 3 landing and per-app implementation
4. Phase 4 IIS deployment setup
5. Phase 5 release and recurring smoke tests

### Suggested PR Batches and Merge Order

#### PR1: Architecture and Shared UX Foundation
- Tasks: S1, S2, S3, S4
- Intent: lock route contract and reusable validation/page-shell layer before page-specific implementation.
- Merge gate:
  - Adapter matrix approved
  - Shared validation module and shell pass local checks

#### PR2: Landing and Phase-1 App Pages
- Tasks: S5, S6
- Intent: deliver user-facing landing + per-app pages using shared foundation.
- Merge gate:
  - Positive and negative launch flows pass for all phase-1 apps
  - Mobile and desktop layouts verified

#### PR3: IIS Deployment and Operations
- Tasks: S7, S8, S9, S10
- Intent: operationalize deployment, release management, and smoke validation.
- Merge gate:
  - IIS smoke tests pass for landing and app routes
  - Runbook and smoke checklist are committed
