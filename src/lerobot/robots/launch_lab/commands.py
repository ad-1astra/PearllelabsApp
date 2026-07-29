"""Pure functions that build the exact shell commands run by each quest level.

Kept free of any web/UI/pty imports so the commands themselves stay easy to
read, test, and cross-check against AGENT_GUIDE.md.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_WINDOWS_FFMPEG_FALLBACK_DIR = "lerobot-launch-lab/ffmpeg"  # under %LOCALAPPDATA%, see install_steps()

# .../<repo_root>/src/lerobot/robots/launch_lab/commands.py -- derived from this file's
# own location (not hardcoded) so installs work from wherever the repo was cloned, both
# for other users and for the differently-pathed checkout Render builds from.
REPO_ROOT = str(Path(__file__).resolve().parents[4])

# Placeholder ports shown before a real one is detected via Find Ports -- Linux/macOS
# expose serial devices as /dev/ttyACM*, Windows as COM* (COM3/COM4 are the common
# first-two-devices default, not guaranteed; Find Ports always overrides these).
DEFAULT_FOLLOWER_PORT = "COM3" if IS_WINDOWS else "/dev/ttyACM0"
DEFAULT_LEADER_PORT = "COM4" if IS_WINDOWS else "/dev/ttyACM1"


def _installed(dist_name: str) -> bool:
    try:
        importlib.metadata.version(dist_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _sync_windows_ffmpeg_path() -> None:
    """Make a successful Windows ffmpeg fallback download (see install_steps) visible
    to this process, not just future ones.

    That fallback downloads to a per-user AppData folder and updates PATH via
    [Environment]::SetEnvironmentVariable -- which only affects processes started
    *after* that point. This server process already took its own PATH snapshot at
    startup, so without this, a real successful download still shows as "not
    installed" forever (both in the Install screen's own re-check, and for every
    later command this server spawns, e.g. record/teleoperate needing ffmpeg).
    """
    if not IS_WINDOWS or shutil.which("ffmpeg"):
        return
    base = Path(os.environ.get("LOCALAPPDATA", "")) / Path(_WINDOWS_FFMPEG_FALLBACK_DIR)
    if not base.is_dir():
        return
    for exe in base.glob("ffmpeg-*/bin/ffmpeg.exe"):
        bin_dir = str(exe.parent)
        path = os.environ.get("PATH", "")
        if bin_dir not in path.split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + path
        break


def install_steps() -> list[dict]:
    """One entry per Install-level check, in run order.

    Each entry: key, label, already_installed (bool), and the command to run
    when it isn't. `already_installed` is re-evaluated every time this is
    called (used for both the initial check and "Re-check").
    """
    _sync_windows_ffmpeg_path()
    py = sys.executable
    # Quoting: single-quoted on POSIX (bash), double-quoted on Windows (PowerShell) --
    # each shell's own string-literal syntax for a path that may contain spaces (a
    # Windows user profile path routinely has one, e.g. C:\Users\Pearl Labs\...).
    quote = '"{}"' if IS_WINDOWS else "'{}'"

    def pip_install(extra: str) -> str:
        # `uv pip install`, not `{py} -m pip install`: venvs created by `uv sync`/
        # `uv venv` don't include pip at all by default (uv is its own installer, not
        # a pip wrapper) -- confirmed by testing an actual uv-created venv, where
        # `python -m pip` fails with "No module named pip" even though the venv and
        # its packages are otherwise completely normal. `--python` targets the exact
        # interpreter this app is itself running under, same as the old command did.
        cmd = f"uv pip install --python {quote.format(py)} -e {quote.format(REPO_ROOT + '[' + extra + ']')}"
        if IS_WINDOWS:
            cmd += "\n$__stepOk = ($LASTEXITCODE -eq 0)"
        return cmd

    if IS_WINDOWS:
        # --disable-interactivity suppresses winget's first-run "accept Microsoft
        # Store source terms" prompt, which --accept-source-agreements alone doesn't
        # always fully suppress on every winget version -- left interactive, it hangs
        # forever waiting for a keypress that never comes from an automated PTY script.
        winget_flags = "--silent --accept-package-agreements --accept-source-agreements --disable-interactivity"
        has_winget = (
            "$__winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)\n"
            "if (-not $__winget) { Write-Host 'winget not found -- install \"App Installer\" from the Microsoft Store "
            "(https://aka.ms/getwinget), or install the tool below manually, then click Re-check.' }\n"
        )
        # Each branch sets $__stepOk explicitly at its own real point of success/
        # failure rather than relying on $LASTEXITCODE -- that variable is only set by
        # *external* processes, not cmdlets (Invoke-WebRequest, Expand-Archive, etc.),
        # so a cmdlet-only path (like the ffmpeg direct-download fallback) leaves it
        # holding whatever an unrelated *earlier* step's external command left behind,
        # silently misreporting success/failure by coincidence.
        git_lfs_cmd = (
            has_winget
            + f"if ($__winget -and -not (Get-Command git-lfs -ErrorAction SilentlyContinue)) "
            f"{{ winget install -e --id GitHub.GitLFS {winget_flags} }}\n"
            "if (Get-Command git-lfs -ErrorAction SilentlyContinue) { git lfs install; git lfs pull; $__stepOk = $true } "
            "else { $__stepOk = $false }"
        )
        ffmpeg_cmd = (
            "if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { $__stepOk = $true } else {\n"
            "  $ok = $false\n"
            + has_winget
            + f"  if ($__winget) {{ winget install -e --id Gyan.FFmpeg {winget_flags}; "
            "$ok = [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue) }\n"
            "  if (-not $ok) {\n"
            "    Write-Host 'winget unavailable or failed -- downloading a portable ffmpeg build directly instead...'\n"
            "    $dir = \"$env:LOCALAPPDATA\\lerobot-launch-lab\\ffmpeg\"\n"
            "    New-Item -ItemType Directory -Force -Path $dir | Out-Null\n"
            "    $zip = \"$dir\\ffmpeg.zip\"\n"
            "    Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip\n"
            "    Expand-Archive -Path $zip -DestinationPath $dir -Force\n"
            "    $bin = Join-Path (Get-ChildItem -Path $dir -Directory | Select-Object -First 1).FullName 'bin'\n"
            "    [Environment]::SetEnvironmentVariable('Path', \"$env:Path;$bin\", 'User')\n"
            "    $env:Path += \";$bin\"\n"
            # Verify the download actually produced a working binary rather than
            # assuming success -- this is the real, final word on whether it worked.
            "    $ok = Test-Path (Join-Path $bin 'ffmpeg.exe')\n"
            "  }\n"
            "  $__stepOk = $ok\n"
            "}"
        )
    else:
        git_lfs_cmd = "which git-lfs >/dev/null 2>&1 || sudo apt-get install -y git-lfs; git lfs install && git lfs pull"
        ffmpeg_cmd = "which ffmpeg >/dev/null 2>&1 || sudo apt-get install -y ffmpeg"

    return [
        {
            "key": "feetech",
            "label": "Motor SDK -- lerobot[feetech]",
            "already_installed": _installed("feetech-servo-sdk"),
            "cmd": pip_install("feetech"),
        },
        {
            "key": "core_scripts",
            "label": "Robot workflows -- lerobot[core_scripts] (record/replay/calibrate/teleoperate)",
            "already_installed": _installed("rerun-sdk") and _installed("datasets"),
            "cmd": pip_install("core_scripts"),
        },
        {
            "key": "training",
            "label": "Training stack -- lerobot[training] (accelerate + wandb)",
            "already_installed": _installed("accelerate") and _installed("wandb"),
            "cmd": pip_install("training"),
        },
        {
            "key": "git_lfs",
            "label": "Git LFS assets",
            "already_installed": shutil.which("git-lfs") is not None,
            "cmd": git_lfs_cmd,
        },
        {
            "key": "ffmpeg",
            "label": "ffmpeg (video decoding)",
            "already_installed": shutil.which("ffmpeg") is not None,
            "cmd": ffmpeg_cmd,
        },
    ]


def hf_whoami_cmd() -> str:
    return "hf auth whoami"


def hf_login_cmd() -> str:
    return "hf auth login"


def find_port_cmd() -> str:
    return "lerobot-find-port"


def find_cameras_cmd() -> str:
    return "lerobot-find-cameras"


def _maybe_sudo(binary: str) -> str:
    """On POSIX, resolve the full path via the invoking user's PATH and prefix with
    sudo -- `sudo <name>` alone can fail with "command not found" if the binary only
    exists in a user-local venv not on root's PATH. Windows needs neither: opening a
    COM port doesn't require elevation, and there's no portable `sudo` equivalent.

    PYTHONDONTWRITEBYTECODE=1: running as root means any .pyc bytecode cache Python
    writes while importing (__pycache__/ next to the source, inside the shared .venv)
    ends up root-owned -- the invoking user can then never clean or update that venv
    again (confirmed: `uv sync` failing with "Permission denied" removing a
    root-owned __pycache__ dir this exact command chain created on an earlier run).
    """
    if IS_WINDOWS:
        return binary
    return f"sudo env PYTHONDONTWRITEBYTECODE=1 $(which {binary})"


def setup_motors_cmd(arm: str, port: str) -> str:
    binary = _maybe_sudo("lerobot-setup-motors")
    if arm == "follower":
        return f"{binary} --robot.type=so101_follower --robot.port={port} --robot.id=picker"
    return f"{binary} --teleop.type=so101_leader --teleop.port={port} --teleop.id=commander"


def calibrate_cmd(arm: str, port: str) -> str:
    binary = _maybe_sudo("lerobot-calibrate")
    if arm == "follower":
        return f"{binary} --robot.type=so101_follower --robot.port={port} --robot.id=picker"
    return f"{binary} --teleop.type=so101_leader --teleop.port={port} --teleop.id=commander"


def camera_spec(cameras: list[dict]) -> str:
    """Build the `--robot.cameras="{...}"` value from a list of
    {name, index_or_path, width, height, fps} dicts."""
    parts = []
    for cam in cameras:
        parts.append(
            f"{cam['name']}: {{type: opencv, index_or_path: {cam['index_or_path']}, "
            f"width: {cam.get('width', 640)}, height: {cam.get('height', 480)}, fps: {cam.get('fps', 30)}}}"
        )
    return "{ " + ", ".join(parts) + "}"


def teleoperate_cmd(follower_port: str, leader_port: str, cameras: list[dict]) -> str:
    cmd = (
        "lerobot-teleoperate "
        f"--robot.type=so101_follower --robot.port={follower_port} --robot.id=picker "
        f"--teleop.type=so101_leader --teleop.port={leader_port} --teleop.id=commander"
    )
    if cameras:
        cmd += f' --robot.cameras="{camera_spec(cameras)}" --display_data=true'
    return cmd


def record_cmd(
    follower_port: str,
    leader_port: str,
    cameras: list[dict],
    repo_id: str,
    task: str,
    num_episodes: int = 50,
    episode_time_s: int = 30,
    reset_time_s: int = 10,
) -> str:
    cmd = (
        "lerobot-record "
        f"--robot.type=so101_follower --robot.port={follower_port} --robot.id=picker "
        f"--teleop.type=so101_leader --teleop.port={leader_port} --teleop.id=commander "
    )
    if cameras:
        cmd += f'--robot.cameras="{camera_spec(cameras)}" '
    cmd += (
        f'--dataset.repo_id={repo_id} --dataset.single_task="{task}" '
        f"--dataset.num_episodes={num_episodes} --dataset.episode_time_s={episode_time_s} "
        f"--dataset.reset_time_s={reset_time_s} --display_data=true"
    )
    return cmd


def replay_cmd(follower_port: str, repo_id: str, episode: int = 0) -> str:
    return (
        f"lerobot-replay --robot.type=so101_follower --robot.port={follower_port} --robot.id=picker "
        f"--dataset.repo_id={repo_id} --dataset.episode={episode}"
    )


def train_cmd(
    repo_id: str,
    policy_type: str = "act",
    device: str = "cuda",
    output_dir: str | None = None,
    job_name: str | None = None,
    batch_size: int = 8,
    steps: int | None = None,
    wandb_enable: bool = False,
    policy_repo_id: str | None = None,
) -> str:
    job_name = job_name or f"{policy_type}_{repo_id.split('/')[-1]}"
    output_dir = output_dir or f"outputs/train/{job_name}"
    cmd = (
        "lerobot-train "
        f"--dataset.repo_id={repo_id} --policy.type={policy_type} --policy.device={device} "
        f"--output_dir={output_dir} --job_name={job_name} --batch_size={batch_size}"
    )
    if steps:
        cmd += f" --steps={steps} --policy.scheduler_decay_steps={steps} --save_freq={min(steps, 5000)}"
    cmd += f" --wandb.enable={'true' if wandb_enable else 'false'}"
    if policy_repo_id:
        cmd += f" --policy.repo_id={policy_repo_id}"
    return cmd


def eval_record_cmd(
    follower_port: str,
    cameras: list[dict],
    repo_id: str,
    task: str,
    policy_path: str,
    num_episodes: int = 10,
) -> str:
    cmd = f"lerobot-record --robot.type=so101_follower --robot.port={follower_port} --robot.id=picker "
    if cameras:
        cmd += f'--robot.cameras="{camera_spec(cameras)}" '
    cmd += (
        f'--dataset.repo_id={repo_id} --dataset.single_task="{task}" '
        f"--dataset.num_episodes={num_episodes} --policy.path={policy_path}"
    )
    return cmd


def eval_sim_cmd(
    policy_path: str,
    env_type: str,
    n_episodes: int = 50,
    batch_size: int = 10,
    device: str = "cuda",
) -> str:
    return (
        f"lerobot-eval --policy.path={policy_path} --env.type={env_type} "
        f"--eval.n_episodes={n_episodes} --eval.batch_size={batch_size} --policy.device={device}"
    )
