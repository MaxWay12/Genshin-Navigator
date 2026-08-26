param(
    [string]$Version = "v0.1.0-alpha"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
$Dist = Join-Path $ProjectRoot "dist"
$Work = Join-Path $ProjectRoot "build\pyinstaller"
$PackageName = "GenshinNavigator-$Version-windows-x64"
$Stage = Join-Path $Dist $PackageName
$Zip = Join-Path $Dist "$PackageName.zip"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing .venv. Create the verified Python environment first."
}
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller is missing. Install requirements-build.txt in .venv."
}

& $PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name GenshinNavigator `
    --paths (Join-Path $ProjectRoot "src") `
    --collect-all webview `
    --distpath $Dist `
    --workpath $Work `
    --specpath (Join-Path $ProjectRoot "build") `
    (Join-Path $PSScriptRoot "entrypoint.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
Move-Item -LiteralPath (Join-Path $Dist "GenshinNavigator") -Destination $Stage

function Copy-ReleaseFile([string]$Relative, [string]$Destination = $Relative) {
    $Source = Join-Path $ProjectRoot $Relative
    $Target = Join-Path $Stage $Destination
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Force
}

function Copy-ReleaseDirectory([string]$Relative) {
    $Source = Join-Path $ProjectRoot $Relative
    $Target = Join-Path $Stage $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
}

Copy-ReleaseFile "config.example.json"
Copy-ReleaseFile "config.sumeru.example.json"
Copy-ReleaseFile "release/regions.portable.json" "regions.json"
Copy-ReleaseFile "README.md"
Copy-ReleaseFile "CHANGELOG.md"
Copy-ReleaseFile "RELEASE_NOTES_v0.1.0-alpha.md"
Copy-ReleaseFile "LICENSE"
Copy-ReleaseFile "THIRD_PARTY_NOTICES.md"
Copy-ReleaseFile "release/Start-Fontaine.cmd" "Start-Fontaine.cmd"
Copy-ReleaseFile "release/Start-Sumeru-Experimental.cmd" "Start-Sumeru-Experimental.cmd"

& $Python (Join-Path $ProjectRoot "scripts/collect_licenses.py") --output (Join-Path $Stage "licenses")
if ($LASTEXITCODE -ne 0) { throw "Third-party license collection failed" }

# Official map tiles, POI data, point images and user state are intentionally
# absent. The user downloads regional content into datasets/local on first run.
# Editable-install provenance contains the developer checkout path and is not
# required at runtime. Package metadata itself remains available for versioning.
Get-ChildItem -LiteralPath $Stage -File -Recurse -Filter direct_url.json | Remove-Item -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "datasets/local") | Out-Null

& $Python (Join-Path $ProjectRoot "scripts/audit_release.py") --artifact $Stage
if ($LASTEXITCODE -ne 0) { throw "Release privacy audit failed" }

if (Test-Path -LiteralPath $Zip) {
    Remove-Item -LiteralPath $Zip -Force
}
Compress-Archive -LiteralPath $Stage -DestinationPath $Zip -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$Zip.sha256" -Encoding ascii -Value "$Hash  $PackageName.zip"
Write-Output $Zip
Write-Output "SHA256 $Hash"
