# Bundle 0 spike (#380): built-in Coworker copy in the System repository + update repository.
#
#   powershell -ExecutionPolicy Bypass -File run_spike.ps1 -Zip <bfa_coworker-x.y.z.zip> [-Scratch <dir>] [-Exe <bforartists.exe>] [-Writable]
#
# Everything lives under -Scratch (default F:\bfa_coworker_wt\_scratch\bundle-380): the user
# profile (BLENDER_USER_RESOURCES), the System extensions folder (BLENDER_SYSTEM_EXTENSIONS),
# and USERPROFILE / APPDATA / LOCALAPPDATA for Coworker's own caches.  The real Bforartists
# config is never touched.  The System folder is made read-only for the current user (ACL
# read + execute only) during the runs, like an install under Program Files, and restored after.
#
# Two --background --offline-mode runs, 5 s apart, under the shared live-test lock.
param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [string]$Scratch = "F:\bfa_coworker_wt\_scratch\bundle-380",
    [string]$Exe = "C:\3D_Stuff\Devbuild\bforartists.exe",
    [switch]$Writable  # leave the System folder writable (a portable / zip install)
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$lock = Join-Path $env:LOCALAPPDATA "Temp\bfa_live.lock"

python -I (Join-Path $here "prepare.py") --zip $Zip --scratch $Scratch
if ($LASTEXITCODE -ne 0) { throw "prepare.py failed" }

$res = Join-Path $Scratch "res"
$home_ = Join-Path $Scratch "home"
$logs = Join-Path $Scratch "logs"
$sysRepo = Join-Path $Scratch "sysext\system"
foreach ($d in "config", "scripts", "extensions", "datafiles") { New-Item -ItemType Directory -Force (Join-Path $res $d) | Out-Null }

$env:BLENDER_USER_RESOURCES = $res
$env:BLENDER_USER_CONFIG = Join-Path $res "config"
$env:BLENDER_USER_SCRIPTS = Join-Path $res "scripts"
$env:BLENDER_USER_EXTENSIONS = Join-Path $res "extensions"
$env:BLENDER_USER_DATAFILES = Join-Path $res "datafiles"
$env:BLENDER_SYSTEM_EXTENSIONS = Join-Path $Scratch "sysext"
$env:USERPROFILE = $home_
$env:HOME = $home_
$env:APPDATA = Join-Path $home_ "AppData\Roaming"
$env:LOCALAPPDATA = Join-Path $home_ "AppData\Local"

# Read-only System folder, like Program Files.
# Inheritance cut and only read+execute granted (a deny ACE with W or D also blocks reads,
# and a deny of DC alone does not stop deleting a child the user may delete).
if (-not $Writable) { icacls $sysRepo /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)(RX)" | Out-Null }

# Shared live-test lock.
while ($true) {
    try { New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null; break }
    catch { Write-Host "waiting for $lock ..."; Start-Sleep -Seconds 20 }
}
try {
    $i = 0
    foreach ($phase in "phase1", "phase2") {
        if ($i -gt 0) { Start-Sleep -Seconds 5 }
        $i++
        $out = Join-Path $logs "$phase.out.txt"
        $err = Join-Path $logs "$phase.err.txt"
        $p = Start-Process -FilePath $Exe -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err `
            -ArgumentList @("--background", "--offline-mode", "--python", (Join-Path $here "spike_bfa.py"), "--", $phase, $logs)
        Write-Host "$phase exit code $($p.ExitCode)"
    }
}
finally {
    Remove-Item $lock -Force
    if (-not $Writable) { icacls $sysRepo /reset /T | Out-Null }
}
# Keep the results of each variant side by side.
$variant = if ($Writable) { "writable" } else { "readonly" }
$keep = Join-Path $Scratch "results\$variant"
New-Item -ItemType Directory -Force $keep | Out-Null
Copy-Item (Join-Path $logs "*") $keep -Force
Get-Content (Join-Path $logs "phase1.out.txt"), (Join-Path $logs "phase2.out.txt") | Select-String "^SPIKE\|"
