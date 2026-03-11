# =========================
# IIS Validation Script (WS 2008 R2 / PS2-friendly)
# =========================

# ---- Configuration (edit these) ----
$SiteName     = "Default Web Site"  # IIS site name (can be found in IIS Manager)
$AppPath      = "/templates/maptour"   # IIS app path
$BaseUrl      = "https://storymaps.esri.com/templates/maptour"  # public URL
$KnownAppId   = "2c62acf3468c4cbbba6b82f1035bfe22"
$FallbackPath = "D:\story maps\templates\maptour"  # used if IIS lookup fails
$OutFile      = "C:\temp\maptour-iis-validation.txt"
# ------------------------------------

# Ensure output folder exists
$OutDir = Split-Path $OutFile -Parent
if (!(Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

Start-Transcript -Path $OutFile -Force | Out-Null

Write-Host "=== Environment ==="
Write-Host ("Date: " + (Get-Date))
Write-Host ("Computer: " + $env:COMPUTERNAME)
Write-Host ("PSVersion: " + $PSVersionTable.PSVersion.ToString())
Write-Host ("OS: " + (Get-WmiObject Win32_OperatingSystem).Caption)

$appcmd = Join-Path $env:windir "System32\inetsrv\appcmd.exe"
Write-Host ""
Write-Host "=== IIS Tooling ==="
if (Test-Path $appcmd) {
  Write-Host ("appcmd found: " + $appcmd)
} else {
  Write-Host "ERROR: appcmd not found. IIS may not be installed."
}

# Resolve physical path from IIS
$PhysicalPath = $null
if (Test-Path $appcmd) {
  try {
    $vdirRef = "$SiteName$AppPath/"
    $PhysicalPath = & $appcmd list vdir "$vdirRef" /text:physicalPath 2>$null
    if ([string]::IsNullOrEmpty($PhysicalPath)) {
      # Try app root as fallback
      $PhysicalPath = & $appcmd list vdir "$SiteName/" /text:physicalPath 2>$null
    }
  } catch {}
}
if ([string]::IsNullOrEmpty($PhysicalPath)) { $PhysicalPath = $FallbackPath }

Write-Host ""
Write-Host "=== IIS App Mapping ==="
Write-Host ("SiteName: " + $SiteName)
Write-Host ("AppPath: " + $AppPath)
Write-Host ("Resolved PhysicalPath: " + $PhysicalPath)

# If IIS returns a parent folder (for example ...\templates) and the app path leaf exists
# as a child folder (for example ...\templates\maptour), validate against the child.
$AppLeaf = $AppPath.Trim('/').Split('/')[-1]
$PhysicalPathResolved = $PhysicalPath
$NestedCandidate = Join-Path $PhysicalPath $AppLeaf
if (Test-Path $NestedCandidate) {
  $PhysicalPathResolved = $NestedCandidate
}

Write-Host ("Validation PhysicalPath: " + $PhysicalPathResolved)

Write-Host ""
Write-Host "=== IIS Config Checks ==="
if (Test-Path $appcmd) {
  Write-Host "--- app list ---"
  & $appcmd list app
  Write-Host "--- vdir list ---"
  & $appcmd list vdir
  Write-Host "--- defaultDocument config (site/app) ---"
  & $appcmd list config "$SiteName$AppPath" /section:defaultDocument
  Write-Host "--- staticContent config (site/app) ---"
  & $appcmd list config "$SiteName$AppPath" /section:staticContent
}

Write-Host ""
Write-Host "=== File Presence Checks ==="
$required = @(
  "index.html",
  "web.config",
  "app\main-app.js",
  "app\maptour-config.js",
  "app\maptour-viewer-min.js",
  "app\maptour-builder-min.js",
  "app\maptour-min.css",
  "resources"
)
foreach ($rel in $required) {
  $p = Join-Path $PhysicalPathResolved $rel
  $ok = Test-Path $p
  Write-Host ("{0,-45} {1}" -f $rel, $(if ($ok) {"OK"} else {"MISSING"}))
}

Write-Host ""
Write-Host "=== Config Flag Checks (deployed files) ==="
$indexFile = Join-Path $PhysicalPathResolved "index.html"
$mainApp   = Join-Path $PhysicalPathResolved "app\main-app.js"

if (Test-Path $indexFile) {
  $c = Get-Content $indexFile -ErrorAction SilentlyContinue
  $flag1 = ($c | Select-String -Pattern "allowAnyAppIdInProd\s*:\s*true" -Quiet)
  $flag2 = ($c | Select-String -Pattern "viewerOnlyInProd\s*:\s*true" -Quiet)
  $flag3 = ($c | Select-String -Pattern 'appid\s*:\s*""' -Quiet)
  Write-Host ("allowAnyAppIdInProd:true  => " + $(if($flag1){"OK"}else{"NOT FOUND"}))
  Write-Host ("viewerOnlyInProd:true     => " + $(if($flag2){"OK"}else{"NOT FOUND"}))
  Write-Host ("appid empty string       => " + $(if($flag3){"OK"}else{"NOT FOUND"}))
} else {
  Write-Host "index.html not found; skipping config flag checks."
}

if (Test-Path $mainApp) {
  $m = Get-Content $mainApp -ErrorAction SilentlyContinue
  $guard = ($m | Select-String -Pattern "viewerOnlyInProd|isViewerOnlyProd|isInBuilderMode\s*=\s*false" -Quiet)
  Write-Host ("viewer-only guard in main-app.js => " + $(if($guard){"OK"}else{"NOT FOUND"}))
} else {
  Write-Host "main-app.js not found; skipping viewer-only guard check."
}

function Get-HttpStatus {
  param([string]$url)
  try {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.Method = "GET"
    $req.AllowAutoRedirect = $true
    $req.Timeout = 30000
    $resp = $req.GetResponse()
    $code = [int]([System.Net.HttpWebResponse]$resp).StatusCode
    $resp.Close()
    return "HTTP " + $code
  } catch {
    if ($_.Exception.Response -ne $null) {
      try {
        $r = [System.Net.HttpWebResponse]$_.Exception.Response
        $code = [int]$r.StatusCode
        $r.Close()
        return "HTTP " + $code
      } catch {}
    }
    return "ERROR: " + $_.Exception.Message
  }
}

Write-Host ""
Write-Host "=== URL Response Checks ==="
$urls = @(
  ($BaseUrl + "/index.html"),
  ($BaseUrl + "/index.html?appid=" + $KnownAppId),
  ($BaseUrl + "/index.html?appid=" + $KnownAppId + "&edit"),
  ($BaseUrl + "/index.html?appid=INVALID_TEST_APPID")
)
foreach ($u in $urls) {
  $s = Get-HttpStatus $u
  Write-Host ($u + " => " + $s)
}

Write-Host ""
Write-Host "=== Manual Browser Checks (report pass/fail) ==="
Write-Host "1) Open: $BaseUrl/index.html?appid=$KnownAppId"
Write-Host "   Expected: Viewer loads map tour."
Write-Host "2) Open: $BaseUrl/index.html?appid=$KnownAppId&edit"
Write-Host "   Expected: Still viewer mode; no builder UI."
Write-Host "3) Open: $BaseUrl/index.html?edit"
Write-Host "   Expected: Still viewer mode; no builder UI."
Write-Host "4) Open DevTools network"
Write-Host "   Expected: No 404 for app/*.js, app/*.css, resources/*."

Write-Host ""
Write-Host ("Transcript saved to: " + $OutFile)
Stop-Transcript | Out-Null