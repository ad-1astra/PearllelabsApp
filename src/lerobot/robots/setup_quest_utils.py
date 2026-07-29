from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestStep:
    key: str
    title: str
    subtitle: str
    reward: str
    tip: str


def build_quest_steps() -> list[QuestStep]:
    return [
        QuestStep(
            key="install",
            title="Install essentials",
            subtitle="Install LeRobot, motor SDK, dataset/training extras, and Git LFS.",
            reward="+15 XP",
            tip="If something fails, the app will suggest the exact recovery step.",
        ),
        QuestStep(
            key="find_ports",
            title="Find your USB ports",
            subtitle="Identify the leader and follower serial ports.",
            reward="+15 XP",
            tip="Keep the robot plugged in and reconnect it if the port is missed.",
        ),
        QuestStep(
            key="set_motor_ids",
            title="Assign motor IDs",
            subtitle="Set each joint and gripper ID one by one.",
            reward="+15 XP",
            tip="Complete this slowly so every motor is recognized by the bus.",
        ),
        QuestStep(
            key="calibrate",
            title="Calibrate the arm",
            subtitle="Center and sweep each servo through its range.",
            reward="+15 XP",
            tip="Calibrate only after the motors are detected successfully.",
        ),
        QuestStep(
            key="teleoperate",
            title="Launch teleop",
            subtitle="Make the leader move the follower in real time.",
            reward="+12 XP",
            tip="If teleop stalls, check power, wiring, and servo response.",
        ),
        QuestStep(
            key="record",
            title="Record a dataset",
            subtitle="Demonstrate the task and capture episodes for training.",
            reward="+12 XP",
            tip="Do a few dry runs before recording -- consistent demos train better.",
        ),
        QuestStep(
            key="train",
            title="Train a policy",
            subtitle="Turn your dataset into a trained control policy.",
            reward="+12 XP",
            tip="Start with ACT and 5-10 epochs before trying anything bigger.",
        ),
        QuestStep(
            key="evaluate",
            title="Evaluate",
            subtitle="Run the trained policy on the robot and check success rate.",
            reward="+12 XP",
            tip="Compare against a teleoperated baseline, not just vibes.",
        ),
    ]


def detect_error_suggestion(output: str) -> str:
    text = (output or "").lower()

    if "permission denied" in text or "dialout" in text:
        return "Permission issue detected. Add your user to dialout, then retry the command."
    if "no status packet" in text or "overload" in text or "txrxresult" in text:
        return "The motor bus is not replying. Check power, wiring, and the servo cable for the affected motor."
    if "git-lfs" in text or "lfs" in text and "not found" in text:
        return "Install git-lfs and rerun the install step."
    if "module not found" in text or "no module named" in text:
        return "Install the missing Python package and rerun the setup flow."
    if "not found" in text and "/dev/tty" in text:
        return "The USB port was not detected. Reconnect the arm and rerun Find Ports."
    if "failed to write" in text and "torque" in text:
        return "Torque enable failed. Reconnect the arm and verify the servo wiring."
    if "hf auth login" in text or "401" in text and "huggingface" in text:
        return "Complete Hugging Face login (hf auth login) so dataset/model uploads can work."
    if "could not find camera" in text or ("camera" in text and "index" in text and "error" in text):
        return "Camera not found at that index. Run Find Cameras again and double-check it's plugged in."
    if "out of memory" in text or "cuda out of memory" in text or "oom" in text:
        return "GPU ran out of memory. Retry with a smaller --batch_size (e.g. half the current value)."
    if "wandb" in text and ("login" in text or "api key" in text or "api_key" in text):
        return "Weights & Biases needs an API key, or disable it with --wandb.enable=false."
    if "repository already exists" in text or "already exists" in text and "repo" in text:
        return "That dataset/model repo already exists on the Hub. Pick a new repo_id or delete the old one."
    if "no space left on device" in text:
        return "Disk is full. Free up space (old datasets/checkpoints in outputs/) and retry."
    if "policy" in text and ("not found" in text or "404" in text):
        return "That policy path/repo_id couldn't be found. Double-check --policy.path or --policy.repo_id."
    if "scservo_sdk" in text and "attribute" in text:
        return (
            "The motor SDK (scservo_sdk) is missing an expected function -- likely a second, "
            "incompatible copy shadowing the real one. In the terminal below, run: "
            "python3 -c \"import scservo_sdk; print(scservo_sdk.__file__)\" to see which file is "
            "actually being loaded."
        )
    return "The app is still following the setup flow. Try the next recommended step from the log above."
