<#
  Builds two throw-away installers for the upgrade and downgrade tests, from the already-built dist\Zapi:

    dist\test\Zapi-Setup-0.0.9.exe   an OLDER version whose payload also holds files the current version does not ship
                                     (a stale file inside _internal and a stale top-level file): after upgrading from it,
                                     both must be gone
    dist\test\Zapi-Setup-9.9.exe     a NEWER version, to test that installing the current one over it is refused

  Run build.bat first. Needs Inno Setup 6. tools\smoke_installed.ps1 picks the results up automatically.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 was not found." }
if (-not (Test-Path "dist\Zapi\Zapi.exe")) { throw "dist\Zapi is missing: run build.bat first." }

$old = Join-Path $root "build\payload_old"
if (Test-Path $old) { Remove-Item $old -Recurse -Force }
robocopy "dist\Zapi" $old /E /NFL /NDL /NJH /NJS /NP | Out-Null
Set-Content -Path (Join-Path $old "_internal\only_in_old_version.txt") -Value "this file belongs to the old version"
Set-Content -Path (Join-Path $old "OLD_VERSION_MARKER.txt") -Value "so does this one"

$out = Join-Path $root "dist\test"
New-Item -ItemType Directory -Force -Path $out | Out-Null
& $iscc "/O$out" "/DOutName=Zapi-Setup-0.0.9" "/DAppVersion=0.0.9" "/DPayloadDir=$old" installer.iss | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) { throw "compiling the old installer failed" }
& $iscc "/O$out" "/DOutName=Zapi-Setup-9.9" "/DAppVersion=9.9" installer.iss | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) { throw "compiling the new installer failed" }
Get-ChildItem $out | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
