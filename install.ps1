# Seahorse installer for Windows. Usage, in PowerShell:
#   irm https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.ps1 | iex
# It installs uv (the Python tool manager) and Seahorse, then starts the setup. On Windows, Seahorse sets up a
# server: the setup runs here, and Seahorse runs on the server.
# All of it runs in a script block: its settings stay inside, and an error stops it without closing the window.
& {
    $ErrorActionPreference = "Stop"
    $repo = "lorenzoa7/seahorse"
    $bin = Join-Path $env:USERPROFILE ".local\bin"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $bin "uv.exe"))) {
        Write-Host "`nInstalling uv..." -ForegroundColor Cyan
        powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`nCould not install uv (see the error above). See https://docs.astral.sh/uv/ and run this again." -ForegroundColor Red
            return
        }
    }
    $env:Path = "$bin;$(Join-Path $env:USERPROFILE '.cargo\bin');$env:Path"

    # The newest release. The page of the latest release sends to the page of its tag, with no limit. The GitHub
    # API allows only 60 requests an hour for each IP, and many people can share one IP (a phone network, an office).
    $tag = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create("https://github.com/$repo/releases/latest")
        $request.AllowAutoRedirect = $false
        $request.UserAgent = "seahorse-install"
        $response = $request.GetResponse()
        if ($response.Headers["Location"] -match "/releases/tag/([^/?#]+)$") { $tag = $Matches[1] }
        $response.Close()
    } catch {}
    if (-not $tag) {
        try { $tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest").tag_name } catch {}
    }
    if (-not $tag) {
        $tag = "main"
        Write-Host "`nCould not find the newest release on GitHub. Installing the main version." -ForegroundColor Yellow
    }

    Write-Host "`nInstalling Seahorse $tag..." -ForegroundColor Cyan
    # A zip of the release: no git is needed on this computer.
    uv tool install --force --python 3.12 "https://github.com/$repo/archive/$tag.zip"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nCould not install Seahorse $tag (see the error above). Check the internet connection, and run this again." -ForegroundColor Red
        return
    }
    uv tool update-shell | Out-Null
    $tools = try { uv tool dir --bin } catch { $null }
    $seahorse = Join-Path $(if ($tools) { $tools } else { $bin }) "seahorse.exe"
    if (-not (Test-Path $seahorse)) {
        Write-Host "`nSeahorse is installed, but $seahorse is not there. Open a new PowerShell window, and run: seahorse setup" -ForegroundColor Red
        return
    }

    Write-Host "`nStarting the setup..." -ForegroundColor Cyan
    & $seahorse setup
}
