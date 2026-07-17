# LeRobot Launch Lab -- native Windows installer (no WSL).
#
# Run in PowerShell with:
#   irm https://pearllelab.web.app/install.ps1 | iex
#
# What this does:
#   1. Installs `uv` (Python package/version manager) if it's not already present.
#   2. Clones (or updates) the app into $HOME\lerobot-launch-lab.
#   3. Installs dependencies (pulling in pywinpty, the Windows PTY backend, instead
#      of the POSIX-only ptyprocess) and starts the local helper.
#
# Runs everything as your normal user -- no elevation needed for install/run.
# Individual quest steps (git-lfs, ffmpeg) use winget, which may show its own
# UAC prompt if a machine-wide install needs it; that's winget's prompt, not this
# script's.
#
# Uses a function + `return` instead of top-level `exit`: this script runs via `iex`
# inside your own PowerShell session, so a bare `exit` would close that whole window
# instead of just stopping the install.

function Install-LaunchLab {
    $ErrorActionPreference = "Stop"
    $repoUrl = "https://github.com/ad-1astra/PearllelabsApp.git"
    $installDir = if ($env:LAUNCH_LAB_INSTALL_DIR) { $env:LAUNCH_LAB_INSTALL_DIR } else { "$HOME\lerobot-launch-lab" }

    Write-Host "== LeRobot Launch Lab installer (Windows) =="

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "git is required but wasn't found. Install it from https://git-scm.com/download/win, then re-run this command."
        return
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "-- Installing uv (Python package manager)..."
        irm https://astral.sh/uv/install.ps1 | iex
        # The installer updates PATH for new sessions, but this one needs it now too.
        $uvBin = "$HOME\.local\bin"
        if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
    }

    if (Test-Path "$installDir\.git") {
        Write-Host "-- Updating existing install at $installDir..."
        git -C $installDir pull --ff-only
    } else {
        Write-Host "-- Cloning into $installDir..."
        git clone --depth 1 $repoUrl $installDir
    }

    Set-Location $installDir

    Write-Host "-- Installing dependencies (first run downloads a few GB -- torch, opencv, etc. Grab a coffee.)"
    uv sync --quiet

    Write-Host "-- Starting Launch Lab..."
    $env:LAUNCH_LAB_OPEN_BROWSER = "1"
    uv run lerobot-launch-lab
}

Install-LaunchLab
