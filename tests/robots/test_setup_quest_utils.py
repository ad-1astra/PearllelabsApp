from lerobot.robots.setup_quest_utils import build_quest_steps, detect_error_suggestion


def test_build_quest_steps_contains_expected_missions():
    steps = build_quest_steps()
    assert [step.key for step in steps] == [
        "install",
        "find_ports",
        "set_motor_ids",
        "calibrate",
        "teleoperate",
    ]


def test_detect_error_suggestion_for_serial_bus_issue():
    suggestion = detect_error_suggestion("Failed to write Torque_Enable ... There is no status packet")
    assert "power" in suggestion.lower()
    assert "wiring" in suggestion.lower()
