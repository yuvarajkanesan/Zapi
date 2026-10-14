<#
  Installs Zapi from the real installer and checks it the way a user would meet it. Needs no Python and no admin rights.
  Works on any Windows 10/11 machine; run it in a clean VM or Windows Sandbox for the meaningful result.

    powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1                       # on your own PC (safe: keeps your data)
    powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1 -CleanMachine          # VM / Sandbox only: also tests the "delete my data" uninstall

  -CleanMachine additionally runs an uninstall WITHOUT /KEEPDATA, which deletes %APPDATA%\Zapi. Never use it on a PC
  whose Zapi data you want to keep.
#>
param(
    [string]$Installer = (Join-Path $PSScriptRoot "..\dist\Zapi-Setup.exe"),
    [switch]$CleanMachine,
    [string]$Report = (Join-Path $env:TEMP "zapi-smoke-report.md")
)
$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $Installer).Path
$root = Join-Path $env:TEMP "ZapiSmoke"
$dir = Join-Path $root "Zapi"
$home_ = Join-Path $root "data"                     # the app under test keeps its data here (ZAPI_HOME), not in %APPDATA%
$appId = "{3C7E2B54-9A1D-4F0B-8E63-5D2A7C91B4E8}_is1"
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Zapi"
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Zapi.lnk"
$results = New-Object System.Collections.ArrayList
$originalPath = [Environment]::GetEnvironmentVariable("Path", "User")

function Check([string]$name, [scriptblock]$test) {
    try {
        $r = & $test
        if ($r -is [bool] -and -not $r) { throw "the check returned false" }
        [void]$results.Add([pscustomobject]@{ Test = $name; Result = "PASS"; Detail = "" })
        Write-Host ("PASS  " + $name) -ForegroundColor Green
    } catch {
        [void]$results.Add([pscustomobject]@{ Test = $name; Result = "FAIL"; Detail = $_.Exception.Message })
        Write-Host ("FAIL  " + $name + "  ->  " + $_.Exception.Message) -ForegroundColor Red
    }
}
function Run-Installer([string[]]$extra) {
    $args_ = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$dir`"", "/TASKS=`"addtopath,desktopicon`"",
               "/LOG=`"$(Join-Path $root 'install.log')`"") + $extra
    $p = Start-Process -FilePath $Installer -ArgumentList $args_ -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "installer exit code $($p.ExitCode) (see $root\install.log)" }
}
function Run-Uninstaller([string[]]$extra) {
    $u = Join-Path $dir "unins000.exe"
    $p = Start-Process -FilePath $u -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $extra) -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "uninstaller exit code $($p.ExitCode)" }
    for ($i = 0; $i -lt 30 -and (Test-Path $dir) -and (Get-ChildItem $dir -Force -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
}

New-Item -ItemType Directory -Force -Path $root, $home_ | Out-Null
if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
Write-Host "Installer: $Installer`nInstall to: $dir`n"

Check "installer is signed (informational: expected to fail until a certificate is used)" {
    $sig = Get-AuthenticodeSignature $Installer
    if ($sig.Status -ne "Valid") { throw "signature status: $($sig.Status)" }
}
Check "silent install succeeds" { Run-Installer @() }
foreach ($f in "Zapi.exe", "zapi-cli.exe", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt", "unins000.exe", "_internal\python314.dll") {
    Check "installed file: $f" { Test-Path (Join-Path $dir $f) }
}
Check "version resources name Lavati Softwares" {
    foreach ($e in "Zapi.exe", "zapi-cli.exe") {
        $v = (Get-Item (Join-Path $dir $e)).VersionInfo
        if ($v.CompanyName -ne "Lavati Softwares") { throw "$e CompanyName='$($v.CompanyName)'" }
        if (-not $v.ProductVersion) { throw "$e has no ProductVersion" }
    }
}
Check "Start menu and desktop shortcuts exist" { (Test-Path (Join-Path $startMenu "Zapi.lnk")) -and (Test-Path $desktopLnk) }
Check "uninstall entry is registered with publisher and icon" {
    $k = Get-ItemProperty $uninstKey
    if ($k.Publisher -ne "Lavati Softwares") { throw "publisher '$($k.Publisher)'" }
    if (-not $k.DisplayIcon) { throw "no icon" }
}
Check "zapi-cli directory was added to the user PATH" { ([Environment]::GetEnvironmentVariable("Path", "User") -split ";") -contains $dir }

Check "packaged self-test passes (every component loads)" {
    $log = Join-Path $env:TEMP "zapi-selftest.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath (Join-Path $dir "Zapi.exe") -ArgumentList "--selftest" -Wait -PassThru
    $text = Get-Content $log -Raw
    if ($p.ExitCode -ne 0 -or $text -notmatch "selftest passed" -or $text -match "FAIL") { throw $text }
}
Check "zapi-cli --version prints the version" { $o = & (Join-Path $dir "zapi-cli.exe") --version; if ($o -notmatch "^Zapi \d") { throw "output: $o" } }

# --- CLI against a local server (a .NET listener, so no Python is needed) ---
$port = Get-Random -Minimum 20000 -Maximum 40000
$job = Start-Job -ArgumentList $port -ScriptBlock {
    param($p)
    $l = New-Object System.Net.HttpListener
    $l.Prefixes.Add("http://127.0.0.1:$p/")
    $l.Start()
    while ($true) {
        $c = $l.GetContext()
        $b = [Text.Encoding]::UTF8.GetBytes('{"ok": true, "path": "' + $c.Request.RawUrl + '"}')
        $c.Response.ContentType = "application/json"
        $c.Response.OutputStream.Write($b, 0, $b.Length)
        $c.Response.Close()
    }
}
Start-Sleep -Seconds 2
$coll = Join-Path $root "collection.json"
@'
{"info": {"name": "smoke"}, "item": [{"name": "ping", "request": {"method": "GET", "url": "{{base}}/ping?x=1"},
 "event": [{"listen": "test", "script": {"exec": ["pm.test('ok', () => pm.expect(pm.response.json().ok).to.be.true);"]}}]}]}
'@ | Set-Content -Path $coll -Encoding UTF8
Check "zapi-cli runs a collection with a test script and exits 0" {
    $out = Join-Path $root "result.json"
    & (Join-Path $dir "zapi-cli.exe") run $coll --env-var "base=http://127.0.0.1:$port" --silent --reporter-json-export $out
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    $j = Get-Content $out -Raw | ConvertFrom-Json
    if ($j.stats.tests -ne 1 -or $j.stats.failedTests -ne 0) { throw "stats: $($j.stats | ConvertTo-Json -Compress)" }
}
Check "zapi-cli exits 1 when a test fails" {
    $bad = (Get-Content $coll -Raw).Replace("to.be.true", "to.be.false")
    $badFile = Join-Path $root "bad.json"
    Set-Content -Path $badFile -Value $bad -Encoding UTF8
    & (Join-Path $dir "zapi-cli.exe") run $badFile --env-var "base=http://127.0.0.1:$port" --silent
    if ($LASTEXITCODE -ne 1) { throw "exit code $LASTEXITCODE" }
}
Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue

# --- the GUI ---
Check "the app window opens, stays responsive and closes cleanly with no crash report" {
    $env:ZAPI_HOME = $home_
    $p = Start-Process -FilePath (Join-Path $dir "Zapi.exe") -PassThru
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $p.Refresh()
        if ($p.HasExited) { throw "the app exited with code $($p.ExitCode) during start-up" }
        if ($p.MainWindowHandle -ne 0) { $ok = $true; break }
    }
    if (-not $ok) { $p.Kill(); throw "no window appeared within 20 s" }
    Start-Sleep -Seconds 2
    $p.Refresh()
    if (-not $p.Responding) { $p.Kill(); throw "the window is not responding" }
    [void]$p.CloseMainWindow()
    if (-not $p.WaitForExit(15000)) { $p.Kill(); throw "the app did not close within 15 s" }
    Remove-Item Env:\ZAPI_HOME
    if (-not (Test-Path (Join-Path $home_ "workspace.json"))) { throw "workspace.json was not written on exit" }
    $crash = Get-ChildItem (Join-Path $home_ "logs") -Filter "crash-*" -ErrorAction SilentlyContinue
    if ($crash) { throw "crash report: $($crash[0].FullName)" }
    $true
}
Check "no stray Zapi processes are left running (script worker, mock server)" {
    Start-Sleep -Seconds 1
    $left = Get-Process -Name "Zapi", "zapi-cli" -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dir*" }
    if ($left) { throw "still running: $($left.Id -join ', ')" }
}

# --- upgrade over the existing install ---
Check "installing again over the existing version succeeds and keeps one uninstall entry" {
    Run-Installer @()
    if (-not (Test-Path (Join-Path $dir "Zapi.exe"))) { throw "Zapi.exe missing after the upgrade" }
    $entries = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" | Where-Object { $_.PSChildName -like "{3C7E2B54*" }
    if (@($entries).Count -ne 1) { throw "$(@($entries).Count) uninstall entries" }
    $paths = ([Environment]::GetEnvironmentVariable("Path", "User") -split ";") | Where-Object { $_ -eq $dir }
    if (@($paths).Count -ne 1) { throw "PATH has the install folder $(@($paths).Count) times" }
}

# --- uninstall, keeping data ---
Check "uninstall (keep data) removes program files, shortcuts, PATH entry and registry entry" {
    Run-Uninstaller @("/KEEPDATA")
    if ((Test-Path $dir) -and (Get-ChildItem $dir -Force -ErrorAction SilentlyContinue)) { throw "files left in $dir" }
    if (Test-Path (Join-Path $startMenu "Zapi.lnk")) { throw "Start menu shortcut left" }
    if (Test-Path $desktopLnk) { throw "desktop shortcut left" }
    if (Test-Path $uninstKey) { throw "uninstall registry entry left" }
    if (([Environment]::GetEnvironmentVariable("Path", "User") -split ";") -contains $dir) { throw "PATH entry left" }
}
Check "user PATH is exactly as it was before the test" { [Environment]::GetEnvironmentVariable("Path", "User") -eq $originalPath }

if ($CleanMachine) {
    Check "uninstall WITHOUT /KEEPDATA also deletes %APPDATA%\Zapi" {
        $data = Join-Path $env:APPDATA "Zapi"
        New-Item -ItemType Directory -Force -Path $data | Out-Null
        Set-Content -Path (Join-Path $data "workspace.json") -Value "{}"
        Run-Installer @()
        Run-Uninstaller @()
        if (Test-Path $data) { throw "$data still exists" }
    }
} else {
    Write-Host "(skipped: the delete-my-data uninstall; use -CleanMachine in a VM or Sandbox)" -ForegroundColor DarkGray
}

# --- report ---
$pass = @($results | Where-Object Result -eq "PASS").Count
$fail = @($results | Where-Object Result -eq "FAIL")
$lines = @("# Zapi installer smoke test", "", "Machine: $env:COMPUTERNAME, $([Environment]::OSVersion.VersionString), $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
           "Installer: $Installer ($([math]::Round((Get-Item $Installer).Length / 1MB, 1)) MB)", "",
           "**$pass passed, $($fail.Count) failed**", "", "| Result | Test | Detail |", "|---|---|---|")
foreach ($r in $results) { $lines += "| $($r.Result) | $($r.Test) | $($r.Detail -replace '\|', '/' -replace '\r?\n', ' ') |" }
Set-Content -Path $Report -Value $lines -Encoding UTF8
Write-Host "`n$pass passed, $($fail.Count) failed. Report: $Report"
# the signature check is informational until a certificate exists
$real = @($fail | Where-Object { $_.Test -notlike "installer is signed*" })
exit $real.Count
