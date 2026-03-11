## IIS Deployment Runbook: Classic Map Tour Viewer

### Purpose
Deploy a viewer-only Classic Map Tour build to /templates/maptour with public appid query support.

### Deployment Policy
- Runtime mode: viewer-only in production.
- Public appid behavior: allow URL query appid in production.
- Edit URL behavior: if URL includes edit parameters, app stays in viewer mode and does not initialize builder.

### Required Configuration
In MapTour/src/index.html, keep these values for IIS viewer deployment:
- appid: ""
- allowAnyAppIdInProd: true
- viewerOnlyInProd: true

### Build and Package Boundary
Only deploy contents of MapTour/deploy.
Do not deploy MapTour/src or repository root files.

Expected deploy package includes at minimum:
- index.html
- web.config
- app/main-app.js
- app/maptour-config.js
- app/maptour-viewer-min.js
- app/maptour-builder-min.js
- app/maptour-min.css
- resources/**

### Build Steps
1. cd MapTour
2. npm install
3. npx grunt-cli
4. Verify MapTour/deploy exists and contains expected files

### Incremental Redeploy (after code changes)
If only runtime code changed, redeploy at minimum:
- deploy/index.html
- deploy/app/main-app.js
- deploy/app/maptour-viewer-min.js
- deploy/app/maptour-builder-min.js
- deploy/web.config (if modified)

### IIS Publish Steps
1. Back up current site contents for rollback.
2. Stop or drain site traffic per operations policy.
3. Copy MapTour/deploy contents into IIS application path /templates/maptour.
4. Confirm index.html is the default document.
5. Start site and run smoke tests.

### Smoke Test Checklist
Use at least one known valid appid, including:
- 2c62acf3468c4cbbba6b82f1035bfe22

Viewer tests:
1. /templates/maptour/index.html?appid=2c62acf3468c4cbbba6b82f1035bfe22 loads.
2. /templates/maptour/index.html?appid=<another-known-valid-appid> loads.
3. /templates/maptour/index.html?appid=<invalid-id> fails gracefully.
4. /templates/maptour/index.html with no appid follows expected fallback behavior.

Viewer-only tests:
1. /templates/maptour/index.html?appid=<valid-id>&edit stays in viewer mode.
2. /templates/maptour/index.html?edit stays in viewer mode.
3. Header edit/switch-to-builder affordance is not shown in production.

Path and asset tests:
1. CSS and icon assets load from /templates/maptour path.
2. No 404s for app bundles and resources in browser network panel.

### Release Workflow
1. Sync branch with upstream/master.
2. Build deploy artifact from current commit.
3. Run smoke tests in pre-production or staging endpoint.
4. Publish to IIS.
5. Run post-deploy smoke tests on production endpoint.
6. Record release metadata.

### Release Metadata Template
- Release date/time:
- Environment:
- Git commit SHA:
- Branch:
- Builder policy: viewerOnlyInProd=true
- appid policy: allowAnyAppIdInProd=true
- Smoke test result:
- Deployed by:

### Rollback Procedure
1. If smoke test fails, restore backed-up prior site contents.
2. Recycle IIS app pool if needed.
3. Re-run baseline smoke test on restored version.
4. Record incident summary and root cause notes.

### Notes
- This runbook defines deployment and verification flow only.
- IIS server hardening, cache tuning, and monitoring remain infra-owner responsibilities.
