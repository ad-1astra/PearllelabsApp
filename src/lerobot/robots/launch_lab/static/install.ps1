# LeRobot Launch Lab -- Windows installer.
#
# Run in PowerShell with:
#   irm https://pearllelab.web.app/install.ps1 | iex
#
# Why WSL: the local helper runs real PTY sessions (fcntl/pty/termios), which are
# POSIX-only and don't exist on native Windows Python. WSL (Windows Subsystem for
# Linux) gives a real Linux environment, so the exact same, already-working Linux
# install path runs unchanged inside it -- rewriting the PTY/shell layer for native
# Windows would be a much larger, currently-unverified undertaking.
#
# WSL2 forwards ports bound inside it to Windows' own localhost automatically, so
# once this finishes, http://127.0.0.1:8090 on your Windows browser reaches the app
# exactly like a native install would.
#
# Everything lives inside a function and uses `return`, not top-level `exit` --
# this script runs via `iex` in the user's own PowerShell session, so a bare `exit`
# would close their whole terminal window instead of just stopping the install.

function Test-WslReady {
    try {
        $distros = wsl -l -q 2>$null
        return ($LASTEXITCODE -eq 0) -and ($distros | Where-Object { $_.Trim() -ne "" })
    } catch {
        return $false
    }
}

function Install-LaunchLab {
    Write-Host "== LeRobot Launch Lab installer (Windows / WSL) =="

    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        Write-Host "WSL isn't available on this system. It needs Windows 10 version 2004+ or Windows 11."
        Write-Host "Update Windows, then re-run this command."
        return
    }

    if (-not (Test-WslReady)) {
        Write-Host "-- WSL isn't set up yet. Installing it now (this needs Administrator rights)..."
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            Write-Host "Please re-run this from an Administrator PowerShell window (right-click PowerShell -> Run as administrator), then paste the same command again."
            return
        }
        wsl --install -d Ubuntu
        Write-Host ""
        Write-Host "WSL is installed, but Windows needs a RESTART before it can be used for the first time."
        Write-Host "Restart your computer, then run this same command again -- it'll pick up from here and finish in one step."
        return
    }

    Write-Host "-- WSL is ready. Installing and starting Launch Lab inside it (first run downloads a few GB -- torch, opencv, etc. Grab a coffee.)..."
    wsl bash -c "curl -fsSL https://pearllelab.web.app/install.sh | bash"
}

Install-LaunchLab
