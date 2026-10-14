<#
  Installs, upgrades, downgrades and uninstalls Zapi with the real installers and checks what a user would meet.
  Needs no Python and no admin rights. Run it in a clean VM or Windows Sandbox for the meaningful result.

    powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1
    powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1 -CleanMachine      # VM / Sandbox only

  The upgrade and downgrade scenarios need the test installers: run tools\make_test_installers.ps1 first (they are
  skipped, and reported as SKIP, when dist\test is missing).

  Safety: the script refuses to run when Zapi is already installed (it would replace it) unless -ReplaceExisting is given.
  It never deletes %APPDATA%\Zapi unless that folder holds nothing but its own marker file (or -CleanMachine is given).
  The app under test keeps its data in a throw-away ZAPI_HOME, not in %APPDATA%.
#>
param(
    [string]$Installer = (Join-Path $PSScriptRoot "..\dist\Zapi-Setup.exe"),
    [string]$OldInstaller = (Join-Path $PSScriptRoot "..\dist\test\Zapi-Setup-0.0.9.exe"),
    [string]$NewerInstaller = (Join-Path $PSScriptRoot "..\dist\test\Zapi-Setup-9.9.exe"),
    [switch]$CleanMachine,
    [switch]$ReplaceExisting,
    [string]$Report = (Join-Path $env:TEMP "zapi-smoke-report.md")
)
$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $Installer).Path
$hasOld = Test-Path $OldInstaller
$hasNewer = Test-Path $NewerInstaller
if ($hasOld) { $OldInstaller = (Resolve-Path $OldInstaller).Path }
if ($hasNewer) { $NewerInstaller = (Resolve-Path $NewerInstaller).Path }
$root = Join-Path $env:TEMP "ZapiSmoke"
$dir = Join-Path $root "Zapi"
$home_ = Join-Path $root "data"
$guid = "{3C7E2B54-9A1D-4F0B-8E63-5D2A7C91B4E8}"
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${guid}_is1"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Zapi"
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Zapi.lnk"
$defaultDir = Join-Path $env:LOCALAPPDATA "Programs\Zapi"
$spaced = Join-Path $root ("spaced\Zo" + [char]0xEB + " " + [char]0x674E + " Zapi (test)")
$dataDir = Join-Path $env:APPDATA "Zapi"
$marker = Join-Path $dataDir "smoke-marker.txt"
$results = New-Object System.Collections.ArrayList
$originalPath = [Environment]::GetEnvironmentVariable("Path", "User")
$envKey = Get-Item "HKCU:\Environment"
$originalPathRaw = $envKey.GetValue("Path", $null, "DoNotExpandEnvironmentNames")      # unexpanded, as stored
$originalPathKind = if ($null -ne $originalPathRaw) { $envKey.GetValueKind("Path") } else { $null }
$expectedVersion = $null

function Add-Result($name, $status, $detail) {
    [void]$results.Add([pscustomobject]@{ Test = $name; Result = $status; Detail = $detail })
}
function Check([string]$name, [scriptblock]$test) {
    try {
        $r = & $test
        if ($r -is [bool] -and -not $r) { throw "the check returned false" }
        Add-Result $name "PASS" ""
        Write-Host ("PASS  " + $name) -ForegroundColor Green
    } catch {
        Add-Result $name "FAIL" $_.Exception.Message
        Write-Host ("FAIL  " + $name + "  ->  " + $_.Exception.Message) -ForegroundColor Red
    }
}
function Skip([string]$name, [string]$why) {
    Add-Result $name "SKIP" $why
    Write-Host ("SKIP  " + $name + "  (" + $why + ")") -ForegroundColor DarkYellow
}

# ---- helpers -------------------------------------------------------------------------------------------------
function Run-Setup([string]$exe, [string[]]$extra = @(), [string]$target = "", [string]$tasks = $null) {
    $a = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$(Join-Path $root ('setup-' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.log'))`"")
    if ($target) { $a += "/DIR=`"$target`"" }
    if ($null -ne $tasks) { $a += "/TASKS=`"$tasks`"" }
    return (Start-Process -FilePath $exe -ArgumentList ($a + $extra) -Wait -PassThru).ExitCode
}
function Install-Ok([string]$exe, [string[]]$extra = @(), [string]$target = "", [string]$tasks = $null) {
    $code = Run-Setup $exe $extra $target $tasks
    if ($code -ne 0) { throw "installer exit code $code ($(Split-Path $exe -Leaf))" }
}
function Installed-Version { (Get-ItemProperty $uninstKey -ErrorAction SilentlyContinue).DisplayVersion }
function Installed-Dir {
    $k = Get-ItemProperty $uninstKey -ErrorAction SilentlyContinue
    if ($k -and $k.InstallLocation) { return $k.InstallLocation.TrimEnd("\") }
    return $null
}
function Uninstall-Ok([string[]]$extra = @()) {
    $k = Get-ItemProperty $uninstKey -ErrorAction Stop
    $u = $k.UninstallString.Trim('"')
    $where = Split-Path $u -Parent
    $p = Start-Process -FilePath $u -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $extra) -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "uninstaller exit code $($p.ExitCode)" }
    for ($i = 0; $i -lt 40 -and (Test-Path $where); $i++) { Start-Sleep -Milliseconds 500 }
}
function Reset-Zapi {
    if (Test-Path $uninstKey) { Uninstall-Ok }
    foreach ($d in $dir, $defaultDir, (Join-Path $root "spaced")) { if (Test-Path $d) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } }
}
function Start-App([string]$exe) {
    $env:ZAPI_HOME = $home_
    try { $p = Start-Process -FilePath $exe -PassThru } finally { Remove-Item Env:\ZAPI_HOME -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $p.Refresh()
        if ($p.HasExited) { throw "the app exited with code $($p.ExitCode) during start-up" }
        if ($p.MainWindowHandle -ne 0) { Start-Sleep -Seconds 1; return $p }
    }
    $p.Kill()
    throw "no window appeared within 20 s"
}
function Wait-Exit($p, [int]$ms = 20000) { return $p.WaitForExit($ms) }
# PATH as a comparable string, ignoring the folders this test installs to: a stale entry left by an earlier install
# may already be there, and the uninstaller rightly removes it
function Norm-Path([string]$p) {
    $skip = @($dir, $defaultDir, $spaced)
    return (($p -split ";") | Where-Object { $skip -notcontains $_ }) -join ";"
}
function Path-Same { (Norm-Path ([Environment]::GetEnvironmentVariable("Path", "User"))) -eq (Norm-Path $originalPath) }
function Path-Count([string]$d) { @(([Environment]::GetEnvironmentVariable("Path", "User") -split ";") | Where-Object { $_ -eq $d }).Count }
function Zapi-Processes { Get-Process -Name "Zapi", "zapi-cli" -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -like "$root*" -or $_.Path -like "$defaultDir*") } }
function Selftest-Passes([string]$d) {
    $log = Join-Path $env:TEMP "zapi-selftest.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath (Join-Path $d "Zapi.exe") -ArgumentList "--selftest" -Wait -PassThru
    $text = Get-Content $log -Raw
    if ($p.ExitCode -ne 0 -or $text -notmatch "selftest passed" -or $text -match "FAIL") { throw $text }
    $true
}
function Data-SafeToWipe {
    if (-not (Test-Path $dataDir)) { return $true }
    return (@(Get-ChildItem $dataDir -Force | Where-Object { $_.Name -ne "smoke-marker.txt" }).Count -eq 0)
}

# ---- preconditions -----------------------------------------------------------------------------------------------
if ((Test-Path $uninstKey) -and -not $ReplaceExisting) {
    Write-Host "Zapi is already installed on this computer. This test would replace it. Re-run with -ReplaceExisting, or use a VM / Sandbox." -ForegroundColor Yellow
    exit 2
}
$stale = @(($originalPath -split ";") | Where-Object { @($dir, $defaultDir, $spaced) -contains $_ })
if ($stale) { Write-Host "Note: your PATH already contains $($stale -join ', ') without an install; the test will not count that as a change." -ForegroundColor DarkYellow }
New-Item -ItemType Directory -Force -Path $root, $home_ | Out-Null
$createdDataDir = -not (Test-Path $dataDir)
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Set-Content -Path $marker -Value "installer test marker: the installer must never touch this"
Write-Host "Installer : $Installer`nOlder     : $(if ($hasOld) { $OldInstaller } else { '(none: run tools\make_test_installers.ps1)' })`nNewer     : $(if ($hasNewer) { $NewerInstaller } else { '(none)' })`n"
Reset-Zapi

# ======== 1. fresh install, everything a user sees ============================================================
Check "installer is signed (informational: fails until a certificate is used)" {
    $sig = Get-AuthenticodeSignature $Installer
    if ($sig.Status -ne "Valid") { throw "signature status: $($sig.Status)" }
}
Check "1. fresh silent install (custom folder, both optional tasks)" { Install-Ok $Installer @() $dir "addtopath,desktopicon" }
$expectedVersion = Installed-Version
foreach ($f in "Zapi.exe", "zapi-cli.exe", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt", "unins000.exe", "_internal\python314.dll") {
    Check "1. installed file: $f" { Test-Path (Join-Path $dir $f) }
}
Check "1. version resources name Lavati Softwares" {
    foreach ($e in "Zapi.exe", "zapi-cli.exe") {
        $v = (Get-Item (Join-Path $dir $e)).VersionInfo
        if ($v.CompanyName -ne "Lavati Softwares") { throw "$e CompanyName='$($v.CompanyName)'" }
        if (-not $v.ProductVersion) { throw "$e has no ProductVersion" }
    }
}
Check "1. Start menu and desktop shortcuts exist" { (Test-Path (Join-Path $startMenu "Zapi.lnk")) -and (Test-Path $desktopLnk) }
Check "1. uninstall entry: publisher, icon, version and install folder" {
    $k = Get-ItemProperty $uninstKey
    if ($k.Publisher -ne "Lavati Softwares") { throw "publisher '$($k.Publisher)'" }
    if (-not $k.DisplayIcon) { throw "no icon" }
    if (-not $k.DisplayVersion) { throw "no version" }
    if ((Installed-Dir) -ne $dir) { throw "InstallLocation '$($k.InstallLocation)'" }
}
Check "1. zapi-cli directory is on the user PATH exactly once" { (Path-Count $dir) -eq 1 }
Check "1. packaged self-test passes (every component loads)" { Selftest-Passes $dir }
Check "1. zapi-cli --version prints the version" { $o = & (Join-Path $dir "zapi-cli.exe") --version; if ($o -notmatch "^Zapi \d") { throw "output: $o" } }

# --- zapi-cli against a local server (a .NET listener, so no Python is needed) ---
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
Check "1. zapi-cli runs a collection with a test script and exits 0" {
    $out = Join-Path $root "result.json"
    & (Join-Path $dir "zapi-cli.exe") run $coll --env-var "base=http://127.0.0.1:$port" --silent --reporter-json-export $out
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    $j = Get-Content $out -Raw | ConvertFrom-Json
    if ($j.stats.tests -ne 1 -or $j.stats.failedTests -ne 0) { throw "stats: $($j.stats | ConvertTo-Json -Compress)" }
}
Check "1. zapi-cli exits 1 when a test fails" {
    $badFile = Join-Path $root "bad.json"
    Set-Content -Path $badFile -Value ((Get-Content $coll -Raw).Replace("to.be.true", "to.be.false")) -Encoding UTF8
    & (Join-Path $dir "zapi-cli.exe") run $badFile --env-var "base=http://127.0.0.1:$port" --silent
    if ($LASTEXITCODE -ne 1) { throw "exit code $LASTEXITCODE" }
}
Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue

Check "1. the app window opens, stays responsive and closes cleanly without a crash report" {
    $p = Start-App (Join-Path $dir "Zapi.exe")
    $p.Refresh()
    if (-not $p.Responding) { $p.Kill(); throw "the window is not responding" }
    [void]$p.CloseMainWindow()
    if (-not (Wait-Exit $p 15000)) { $p.Kill(); throw "the app did not close within 15 s" }
    if (-not (Test-Path (Join-Path $home_ "workspace.json"))) { throw "workspace.json was not written on exit" }
    $crash = Get-ChildItem (Join-Path $home_ "logs") -Filter "crash-*" -ErrorAction SilentlyContinue
    if ($crash) { throw "crash report: $($crash[0].FullName)" }
}
Check "1. no stray Zapi processes are left running" { Start-Sleep -Seconds 1; $l = Zapi-Processes; if ($l) { throw "still running: $($l.Id -join ', ')" } }

# ======== 2. reinstall over the same version ===================================================================
Check "2. installing the same version again keeps one uninstall entry and one PATH entry" {
    Install-Ok $Installer
    if (-not (Test-Path (Join-Path $dir "Zapi.exe"))) { throw "Zapi.exe missing" }
    $entries = @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" | Where-Object { $_.PSChildName -like "{3C7E2B54*" })
    if ($entries.Count -ne 1) { throw "$($entries.Count) uninstall entries" }
    if ((Path-Count $dir) -ne 1) { throw "PATH has the folder $(Path-Count $dir) times" }
}

# ======== 3. uninstall while the app is running; data is kept by default ========================================
Check "3. uninstall while Zapi is running (silent, default = keep my data) removes everything" {
    $p = Start-App (Join-Path $dir "Zapi.exe")
    Uninstall-Ok
    if (-not $p.HasExited) { $p.Kill(); throw "the running app was not stopped" }
    if (Test-Path $dir) { throw "files left in $dir" }
    if (Test-Path (Join-Path $startMenu "Zapi.lnk")) { throw "Start menu shortcut left" }
    if (Test-Path $desktopLnk) { throw "desktop shortcut left" }
    if (Test-Path $uninstKey) { throw "uninstall registry entry left" }
    if ((Path-Count $dir) -ne 0) { throw "PATH entry left" }
}
Check "3. the uninstall kept the user's data (a silent uninstall never deletes it by default)" { Test-Path $marker }
Check "3. user PATH is as it was before the test" { Path-Same }

# ======== 4. upgrade from an older version ========================================================================
if ($hasOld) {
    $oldVer = "0.0.9"
    Check "4. install the OLDER version (PATH task on, desktop shortcut off)" {
        Install-Ok $OldInstaller @() $dir "addtopath"
        if ((Installed-Version) -ne $oldVer) { throw "version is '$(Installed-Version)'" }
        if (-not (Test-Path (Join-Path $dir "_internal\only_in_old_version.txt"))) { throw "the old payload is not what was expected" }
        if (Test-Path $desktopLnk) { throw "desktop shortcut exists but was not requested" }
    }
    $running = $null
    Check "4. upgrade to the current version WHILE the old one is running (no /TASKS: choices are remembered)" {
        $script:running = Start-App (Join-Path $dir "Zapi.exe")
        Install-Ok $Installer
        if ((Installed-Version) -ne $expectedVersion) { throw "version is '$(Installed-Version)', expected $expectedVersion" }
    }
    Check "4. the running app was closed by the installer" { if ($running -and -not (Wait-Exit $running 5000)) { $running.Kill(); throw "still running after the upgrade" } }
    Check "4. files that only the old version had are gone (inside _internal and at the top level)" {
        if (Test-Path (Join-Path $dir "_internal\only_in_old_version.txt")) { throw "_internal\only_in_old_version.txt is still there" }
        if (Test-Path (Join-Path $dir "OLD_VERSION_MARKER.txt")) { throw "OLD_VERSION_MARKER.txt is still there" }
    }
    Check "4. remembered choices: PATH entry kept (once), desktop shortcut still not created" {
        if ((Path-Count $dir) -ne 1) { throw "PATH has the folder $(Path-Count $dir) times" }
        if (Test-Path $desktopLnk) { throw "the upgrade added a desktop shortcut the user had declined" }
        if (-not (Test-Path (Join-Path $startMenu "Zapi.lnk"))) { throw "Start menu shortcut missing" }
    }
    Check "4. the user's data was not touched by the upgrade" { Test-Path $marker }
    Check "4. the upgraded app starts and passes its self-test" { Selftest-Passes $dir }
    Check "4. one uninstall entry, and uninstall after an upgrade leaves nothing behind" {
        $entries = @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" | Where-Object { $_.PSChildName -like "{3C7E2B54*" })
        if ($entries.Count -ne 1) { throw "$($entries.Count) uninstall entries" }
        Uninstall-Ok
        if (Test-Path $dir) { throw "files left in ${dir}: $((Get-ChildItem $dir -Force | Select-Object -First 3).Name -join ', ')" }
        if ((Path-Count $dir) -ne 0) { throw "PATH entry left" }
        if (-not (Path-Same)) { throw "the user PATH was not restored" }
    }
} else {
    Skip "4. upgrade from an older version" "dist\test\Zapi-Setup-0.0.9.exe is missing: run tools\make_test_installers.ps1"
}

# ======== 5. downgrade protection =================================================================================
if ($hasNewer) {
    Check "5. installing an older setup over a NEWER install is refused in silent mode" {
        Install-Ok $NewerInstaller @() $dir "addtopath"
        $code = Run-Setup $Installer
        if ($code -eq 0) { throw "the downgrade went through (exit 0)" }
        if ((Installed-Version) -ne "9.9") { throw "installed version changed to $(Installed-Version)" }
    }
    Check "5. /ALLOWDOWNGRADE lets it through, and the user's data is still there" {
        Install-Ok $Installer @("/ALLOWDOWNGRADE")
        if ((Installed-Version) -ne $expectedVersion) { throw "version is $(Installed-Version)" }
        if (-not (Test-Path $marker)) { throw "data marker lost" }
    }
    Check "5. cleanup after the downgrade test" { Uninstall-Ok; if (Test-Path $dir) { throw "files left" } }
} else {
    Skip "5. downgrade protection" "dist\test\Zapi-Setup-9.9.exe is missing: run tools\make_test_installers.ps1"
}

# ======== 6. other places to install ===============================================================================
Check "6. default location (no /DIR): per-user %LOCALAPPDATA%\Programs\Zapi, no admin prompt" {
    Install-Ok $Installer @() "" "addtopath"
    if ((Installed-Dir) -ne $defaultDir) { throw "installed to '$(Installed-Dir)'" }
    if (-not (Test-Path (Join-Path $defaultDir "Zapi.exe"))) { throw "Zapi.exe missing in the default folder" }
    Selftest-Passes $defaultDir | Out-Null
    Uninstall-Ok
    if (Test-Path $defaultDir) { throw "files left in $defaultDir" }
}
Check "6. a folder with spaces, accents, non-Latin letters and brackets installs, starts and uninstalls" {
    Install-Ok $Installer @() $spaced "addtopath"
    Selftest-Passes $spaced | Out-Null
    $out = Join-Path $root "result-unicode.json"
    & (Join-Path $spaced "zapi-cli.exe") run $coll --env-var "base=http://127.0.0.1:1" --silent --reporter-json-export $out 2>$null
    if (-not (Test-Path $out)) { throw "zapi-cli did not run from that folder" }
    if ((Path-Count $spaced) -ne 1) { throw "PATH entry missing for the folder" }
    Uninstall-Ok
    if (Test-Path $spaced) { throw "files left" }
    if (-not (Path-Same)) { throw "PATH not restored" }
}

# ======== 7. what happens to the user's data on uninstall ============================================================
if ((Data-SafeToWipe) -or $CleanMachine) {
    Check "7. /REMOVEDATA deletes the user's data (and a plain silent uninstall does not)" {
        Install-Ok $Installer @() $dir
        Uninstall-Ok @("/REMOVEDATA")
        if (Test-Path $dataDir) { throw "$dataDir still exists" }
        $script:createdDataDir = $false
    }
} else {
    Skip "7. /REMOVEDATA deletes the user's data" "$dataDir holds real Zapi data on this PC; run with -CleanMachine in a VM / Sandbox to test it"
}

# ---- tidy up ---------------------------------------------------------------------------------------------------------
Reset-Zapi
Remove-Item $marker -ErrorAction SilentlyContinue
if ($createdDataDir -and (Test-Path $dataDir) -and -not (Get-ChildItem $dataDir -Force)) { Remove-Item $dataDir -Force }
# the uninstaller rightly removes a stale entry for a test folder that was in PATH before the test: put it back
if ($null -ne $originalPathRaw -and (Get-Item "HKCU:\Environment").GetValue("Path", $null, "DoNotExpandEnvironmentNames") -ne $originalPathRaw) {
    Set-ItemProperty -Path "HKCU:\Environment" -Name Path -Value $originalPathRaw -Type $originalPathKind
    Write-Host "Restored your user PATH exactly as it was before the test." -ForegroundColor DarkYellow
}
Check "end: your user PATH is exactly as it was before the run (same text and registry type)" {
    $k = Get-Item "HKCU:\Environment"
    if ($null -eq $originalPathRaw) { return $true }
    if ($k.GetValue("Path", $null, "DoNotExpandEnvironmentNames") -ne $originalPathRaw) { throw "different text" }
    if ($k.GetValueKind("Path") -ne $originalPathKind) { throw "different registry type" }
}
Check "end: no Zapi processes or install folders left from the test" {
    if (Zapi-Processes) { throw "processes still running" }
    if (Test-Path $dir) { throw "$dir exists" }
}

# ---- report ------------------------------------------------------------------------------------------------------------
$pass = @($results | Where-Object Result -eq "PASS").Count
$skip = @($results | Where-Object Result -eq "SKIP").Count
$fail = @($results | Where-Object Result -eq "FAIL")
$lines = @("# Zapi installer lifecycle test", "", "Machine: $env:COMPUTERNAME, $([Environment]::OSVersion.VersionString), $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
           "Installer: $Installer ($([math]::Round((Get-Item $Installer).Length / 1MB, 1)) MB), version $expectedVersion", "",
           "**$pass passed, $($fail.Count) failed, $skip skipped**", "", "| Result | Test | Detail |", "|---|---|---|")
foreach ($r in $results) { $lines += "| $($r.Result) | $($r.Test) | $($r.Detail -replace '\|', '/' -replace '\r?\n', ' ') |" }
Set-Content -Path $Report -Value $lines -Encoding UTF8
Write-Host "`n$pass passed, $($fail.Count) failed, $skip skipped. Report: $Report"
$real = @($fail | Where-Object { $_.Test -notlike "installer is signed*" })   # the signature check is informational until a certificate exists
exit $real.Count
