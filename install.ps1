# Seahorse installer for Windows. Usage, in PowerShell:
#   irm https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.ps1 | iex
# It installs uv (the Python tool manager) and Seahorse, then starts the setup. On Windows, Seahorse sets up a
# server: the setup runs here, and Seahorse runs on the server.
$ErrorActionPreference = "Stop"
$repo = "lorenzoa7/seahorse"
$bin = Join-Path $env:USERPROFILE ".local\bin"

if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $bin "uv.exe"))) {
    Write-Host "`nInstalling uv..." -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
}
$env:Path = "$bin;$env:Path"

try { $tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest").tag_name } catch { $tag = "main" }
Write-Host "`nInstalling Seahorse $tag..." -ForegroundColor Cyan
# A zip of the release: no git is needed on this computer.
uv tool install --force --python 3.12 "https://github.com/$repo/archive/$tag.zip"
uv tool update-shell | Out-Null

Write-Host "`nStarting the setup..." -ForegroundColor Cyan
& (Join-Path $bin "seahorse.exe") setup
