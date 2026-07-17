"""Pure functions that build the exact shell commands run by each quest level.

Kept free of any web/UI/pty imports so the commands themselves stay easy to
read, test, and cross-check against AGENT_GUIDE.md.
"""

from __future__ import annotations

import importlib.metadata
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

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


def install_steps() -> list[dict]:
    """One entry per Install-level check, in run order.

    Each entry: key, label, already_installed (bool), and the command to run
    when it isn't. `already_installed` is re-evaluated every time this is
    called (used for both the initial check and "Re-check").
    """
    py = sys.executable
    # Quoting: single-quoted on POSIX (bash), double-quoted on Windows (PowerShell) --
    # each shell's own string-literal syntax for a path that may contain spaces.
    pip_quote = '"{}"' if IS_WINDOWS else "'{}'"

    def pip_install(extra: str) -> str:
        return f"{py} -m pip install -e {pip_quote.format(REPO_ROOT + '[' + extra + ']')}"

    if IS_WINDOWS:
        git_lfs_cmd = (
            "if (-not (Get-Command git-lfs -ErrorAction SilentlyContinue)) "
            "{ winget install -e --id GitHub.GitLFS --silent --accept-package-agreements --accept-source-agreements }\n"
            "git lfs install\n"
            "git lfs pull"
        )
        ffmpeg_cmd = (
            "if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) "
            "{ winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements }"
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
    COM port doesn't require elevation, and there's no portable `sudo` equivalent."""
    if IS_WINDOWS:
        return binary
    return f"sudo $(which {binary})"


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
