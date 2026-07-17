#!/usr/bin/env python
# ============================================================
# LeRobot Launch Lab  —  KivyMD desktop app
# Visual language cloned from leLab (huggingface/leLab): pure black bg,
# flat gray-800/gray-700 cards, green/yellow/red semantic accents, no emoji.
# Same quest, same terminal, same backend logic as the web version
# (lerobot.robots.launch_lab): 8-level pipeline, real PTY terminal,
# XP/ranks, install/motor/port tracking. This file is just a different
# front end wired onto the same commands.py / quest_state.py / pty_session.py.
# ============================================================

import re
import sys
import uuid
from pathlib import Path
from kivy.lang import Builder
from kivy.core.window import Window

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lerobot.robots.setup_quest_utils import detect_error_suggestion
from lerobot.robots.launch_lab import commands
from lerobot.robots.launch_lab.pty_session import PtySession, strip_ansi
from lerobot.robots.launch_lab.quest_state import LEVEL_TITLES, LEVELS, MOTOR_ORDER, AppState
from kivy.uix.image import Image
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.properties import ListProperty
from kivy.utils import escape_markup
from kivymd.app import MDApp
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.toast import toast
from kivy.uix.screenmanager import Screen, ScreenManager

# Load the sibling module by path, not via `lerobot.robots.viewer_bridge` — a
# different lerobot checkout installed elsewhere on this machine can shadow
# this repo's package on sys.path and silently hide this file.
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from viewer_bridge import ViewerBridge
except Exception as e:
    ViewerBridge = None
    print(f"3D viewer bridge unavailable ({e}); 'View 3D' will be disabled.")


def _rgba(token):
    return tuple(float(x) for x in token.split(","))


# ── palette (cloned from leLab: pure black + flat gray cards + green/yellow/red accents) ──
BG = "0, 0, 0, 1"                          # black
BG1 = "0.122, 0.161, 0.216, 1"             # gray-800 (cards)
BG2 = "0.067, 0.094, 0.153, 1"             # gray-900 (dim/locked surfaces, inputs)
BORDER = "0.216, 0.255, 0.318, 1"          # gray-700 (card borders)
BORDER_FAINT = "0.122, 0.161, 0.216, 1"    # gray-800 (topbar/footer hairlines)
TEXT = "1, 1, 1, 1"
TEXT_DIM = "0.612, 0.639, 0.686, 1"        # gray-400
TEXT_FAINT = "0.420, 0.447, 0.502, 1"      # gray-500

GREEN = "0.133, 0.773, 0.369, 1"           # green-500 (primary "go" actions)
GREEN_TEXT = "0.290, 0.871, 0.502, 1"      # green-400 (ready/done status text)
GREEN_DIM = "0.035, 0.122, 0.078, 1"

AMBER = "0.984, 0.749, 0.141, 1"           # amber-400 (needs-attention status text)
AMBER_DIM = "0.227, 0.184, 0.063, 1"

YELLOW = "0.918, 0.702, 0.031, 1"          # yellow-500 (teleop / live-control actions)
YELLOW_DIM = "0.161, 0.129, 0.024, 1"

RED = "0.937, 0.267, 0.267, 1"             # red-500 (destructive / stop)
RED_TEXT = "0.973, 0.443, 0.443, 1"        # red-400 (error text)
RED_DIM = "0.227, 0.102, 0.102, 1"

GREEN_DIM_T = _rgba(GREEN_DIM)
GREEN_TEXT_T = _rgba(GREEN_TEXT)
AMBER_DIM_T = _rgba(AMBER_DIM)
AMBER_T = _rgba(AMBER)
RED_DIM_T = _rgba(RED_DIM)
RED_TEXT_T = _rgba(RED_TEXT)
BG1_T = _rgba(BG1)
BG2_T = _rgba(BG2)
TEXT_FAINT_T = _rgba(TEXT_FAINT)

LEVEL_SCREENS = {
    "install": "install_screen",
    "find_ports": "find_usb",
    "set_motor_ids": "set_ids_baudrates",
    "calibrate": "calibrate",
    "teleoperate": "teleoperate_screen",
    "record": "record_screen",
    "train": "train_screen",
    "evaluate": "evaluate_screen",
}

# back-nav target for each level screen's header arrow
BACK_TARGET = {
    "install_screen": "main_menu",
    "find_usb": "install_screen",
    "set_ids_baudrates": "find_usb",
    "calibrate": "set_ids_baudrates",
    "teleoperate_screen": "calibrate",
    "record_screen": "teleoperate_screen",
    "train_screen": "record_screen",
    "evaluate_screen": "train_screen",
}

KV = f'''
<ChatWidget>:
    orientation: "vertical"
    size_hint_y: None
    height: "260dp"
    spacing: "6dp"

    MDCard:
        orientation: "vertical"
        radius: [8, 8, 8, 8]
        elevation: 1
        line_color: {BORDER}
        md_bg_color: {BG1}
        padding: "10dp"
        spacing: "6dp"
        size_hint_y: 0.8

        MDBoxLayout:
            orientation: "horizontal"
            size_hint_y: None
            height: "26dp"
            spacing: "8dp"
            Widget:
                size_hint_x: None
                width: "14dp"
                canvas:
                    Color:
                        rgba: root.status_color
                    Ellipse:
                        pos: self.x + 1, self.y + 6
                        size: 12, 12
            MDLabel:
                text: "TERMINAL"
                font_style: "Overline"
                theme_text_color: "Custom"
                text_color: {TEXT_FAINT}
            MDIconButton:
                icon: "stop"
                theme_text_color: "Custom"
                text_color: {RED_TEXT}
                size_hint_x: None
                width: "34dp"
                on_release: app.stop_current_session()

        ScrollView:
            id: scroll_view
            MDLabel:
                id: chat_log
                markup: True
                text: "[color=#6b7280]Ready. Pick an action, or type a command below.[/color]"
                halign: "left"
                valign: "top"
                font_size: "13sp"
                size_hint_y: None
                height: self.texture_size[1]
                text_size: self.width, None

    MDBoxLayout:
        orientation: "horizontal"
        size_hint_y: 0.2
        spacing: "6dp"
        MDTextField:
            id: cmd_input
            hint_text: "Type a command or press Enter..."
            mode: "rectangle"
            size_hint_x: 0.85
            font_size: "13sp"
            multiline: False
            on_text_validate: app.send_terminal_command(self.text, root)
        MDIconButton:
            icon: "send"
            size_hint_x: 0.15
            on_release: app.send_terminal_command(root.ids.cmd_input.text, root)

ScreenManager:
    MainMenuScreen:
    InstallScreen:
    FindUSBScreen:
    SetIDsBaudratesScreen:
    CalibrateScreen:
    TeleoperateScreen:
    RecordScreen:
    TrainScreen:
    EvaluateScreen:

<MotorChip@MDBoxLayout>:
    label_text: ""
    dot_color: {TEXT_FAINT}
    orientation: "horizontal"
    size_hint_y: None
    height: "24dp"
    padding: "8dp", "2dp"
    spacing: "8dp"
    canvas.before:
        Color:
            rgba: {BG2}
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [6]
    Widget:
        size_hint_x: None
        width: "18dp"
        canvas:
            Color:
                rgba: root.dot_color
            Ellipse:
                pos: self.x + 4, self.y + 6
                size: 10, 10
    MDLabel:
        text: root.label_text
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: {TEXT}

<QuestNode@MDCard>:
    level: ""
    number: "1"
    title_text: ""
    subtitle_text: ""
    reward_text: ""
    status_text: "Locked"
    status_color: {TEXT_FAINT}
    node_color: {BG1}
    radius: [8, 8, 8, 8]
    elevation: 1
    line_color: {BORDER}
    orientation: "horizontal"
    padding: "12dp"
    spacing: "12dp"
    size_hint_y: None
    height: "72dp"
    md_bg_color: self.node_color
    on_release: app.go_to_level(self.level)
    MDCard:
        size_hint: None, None
        size: "32dp", "32dp"
        radius: [6, 6, 6, 6]
        md_bg_color: {BG2}
        MDLabel:
            text: root.number
            halign: "center"
            font_style: "Caption"
            bold: True
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}
    MDBoxLayout:
        orientation: "vertical"
        MDLabel:
            text: root.title_text
            font_style: "Subtitle1"
            bold: True
            theme_text_color: "Custom"
            text_color: {TEXT}
        MDLabel:
            text: root.subtitle_text
            font_style: "Caption"
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}
    MDBoxLayout:
        orientation: "vertical"
        size_hint_x: 0.3
        MDLabel:
            text: root.status_text
            halign: "right"
            font_style: "Caption"
            bold: True
            theme_text_color: "Custom"
            text_color: root.status_color
        MDLabel:
            text: root.reward_text
            halign: "right"
            font_style: "Caption"
            theme_text_color: "Custom"
            text_color: {TEXT_FAINT}

<ArmTab@MDCard>:
    arm: "follower"
    label_text: ""
    port_text: "no port yet"
    selected: False
    radius: [8, 8, 8, 8]
    elevation: 1
    line_color: {BORDER}
    md_bg_color: {BG1}
    padding: "10dp"
    orientation: "vertical"
    MDLabel:
        text: root.label_text
        halign: "center"
        font_style: "Subtitle2"
        bold: True
        theme_text_color: "Custom"
        text_color: ({GREEN_TEXT}) if root.arm == "follower" else ({TEXT})
    MDLabel:
        text: root.port_text
        halign: "center"
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: {TEXT_FAINT}

<InstallStep@MDCard>:
    step_name: ""
    status_text: "Checking..."
    step_color: {BG1}
    status_color: {TEXT_DIM}
    orientation: "vertical"
    padding: "12dp"
    spacing: "4dp"
    radius: [8, 8, 8, 8]
    elevation: 1
    line_color: {BORDER}
    md_bg_color: self.step_color
    size_hint_y: None
    height: "56dp"
    MDLabel:
        text: root.step_name
        halign: "center"
        font_style: "Subtitle2"
        theme_text_color: "Custom"
        text_color: {TEXT}
    MDLabel:
        text: root.status_text
        halign: "center"
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: root.status_color

<NavRow@MDBoxLayout>:
    orientation: "horizontal"
    spacing: "10dp"
    size_hint_y: 0.09

<LevelHeader@MDBoxLayout>:
    title_text: ""
    level_tag: ""
    back_target: "main_menu"
    orientation: "horizontal"
    size_hint_y: None
    height: "40dp"
    spacing: "10dp"
    padding: "0dp", "0dp", "0dp", "6dp"
    MDIconButton:
        icon: "arrow-left"
        theme_text_color: "Custom"
        text_color: {TEXT_DIM}
        on_release: app.show_screen(root.back_target)
    Image:
        source: "media/readme/lerobot-logo-thumbnail.png"
        size_hint: None, None
        size: "24dp", "24dp"
    MDLabel:
        text: root.title_text
        font_style: "H6"
        bold: True
        theme_text_color: "Custom"
        text_color: {TEXT}
        size_hint_x: None
        width: self.texture_size[0]
    MDLabel:
        text: root.level_tag
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: {TEXT_FAINT}

<FooterBar@MDBoxLayout>:
    orientation: "horizontal"
    size_hint_y: None
    height: "26dp"
    canvas.before:
        Color:
            rgba: {BORDER_FAINT}
        Line:
            points: [self.x, self.y + self.height, self.x + self.width, self.y + self.height]
            width: 1
    MDLabel:
        text: "Powered by LeRobot"
        halign: "center"
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: {TEXT_FAINT}

<MainMenuScreen>:
    name: "main_menu"
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    on_pre_enter: app.refresh_level_nodes()
    MDBoxLayout:
        orientation: "vertical"
        padding: "18dp"
        spacing: "8dp"

        MDBoxLayout:
            orientation: "horizontal"
            size_hint_y: None
            height: "40dp"
            spacing: "10dp"
            canvas.before:
                Color:
                    rgba: {BORDER_FAINT}
                Line:
                    points: [self.x, self.y, self.x + self.width, self.y]
                    width: 1
            Image:
                source: "media/readme/lerobot-logo-thumbnail.png"
                size_hint: None, None
                size: "26dp", "26dp"
            MDLabel:
                text: "LeRobot Launch Lab"
                font_style: "Subtitle1"
                bold: True
                theme_text_color: "Custom"
                text_color: {TEXT}
                size_hint_x: None
                width: self.texture_size[0]
            Widget:
            MDCard:
                orientation: "vertical"
                padding: "6dp", "4dp"
                radius: [6, 6, 6, 6]
                elevation: 0
                line_color: {BORDER}
                size_hint: None, None
                size: "150dp", "30dp"
                md_bg_color: {BG1}
                on_release: app.open_viewer()
                MDLabel:
                    id: view3d_label
                    text: "View 3D"
                    halign: "center"
                    font_style: "Caption"
                    theme_text_color: "Custom"
                    text_color: {TEXT_DIM}
            MDCard:
                orientation: "vertical"
                padding: "6dp", "4dp"
                radius: [6, 6, 6, 6]
                elevation: 0
                line_color: {BORDER}
                size_hint: None, None
                size: "150dp", "30dp"
                md_bg_color: {BG1}
                MDLabel:
                    id: rank_label
                    text: "Rookie Roboticist"
                    halign: "center"
                    font_style: "Caption"
                    bold: True
                    theme_text_color: "Custom"
                    text_color: {GREEN_TEXT}

        MDLabel:
            id: quest_summary
            text: "0 / 8 quests · XP 0"
            halign: "center"
            font_style: "Caption"
            size_hint_y: None
            height: "18dp"
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}

        MDProgressBar:
            id: xp_bar
            value: 0
            size_hint_y: None
            height: "6dp"
            color: {GREEN}

        ScrollView:
            MDBoxLayout:
                orientation: "vertical"
                spacing: "8dp"
                size_hint_y: None
                height: self.minimum_height
                padding: "2dp", "8dp"
                QuestNode:
                    id: node_install
                    level: "install"
                    number: "1"
                    title_text: "Install"
                QuestNode:
                    id: node_find_ports
                    level: "find_ports"
                    number: "2"
                    title_text: "Find Ports"
                QuestNode:
                    id: node_set_motor_ids
                    level: "set_motor_ids"
                    number: "3"
                    title_text: "Set IDs"
                QuestNode:
                    id: node_calibrate
                    level: "calibrate"
                    number: "4"
                    title_text: "Calibrate"
                QuestNode:
                    id: node_teleoperate
                    level: "teleoperate"
                    number: "5"
                    title_text: "Teleoperate"
                QuestNode:
                    id: node_record
                    level: "record"
                    number: "6"
                    title_text: "Record"
                QuestNode:
                    id: node_train
                    level: "train"
                    number: "7"
                    title_text: "Train"
                QuestNode:
                    id: node_evaluate
                    level: "evaluate"
                    number: "8"
                    title_text: "Evaluate"

        FooterBar:

<InstallScreen>:
    name: "install_screen"
    on_pre_enter: app.on_enter_install()
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "6dp"
        LevelHeader:
            title_text: "Install"
            level_tag: "Level 1 of 8"
            back_target: "main_menu"
        MDLabel:
            text: "We check what's already installed and only fetch what's missing. Sudo prompts show up right in the terminal below."
            halign: "left"
            font_style: "Caption"
            size_hint_y: None
            height: "20dp"
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}
        InstallStep:
            id: step_feetech
            step_name: "Motor SDK -- lerobot[feetech]"
        InstallStep:
            id: step_core_scripts
            step_name: "Robot workflows -- lerobot[core_scripts]"
        InstallStep:
            id: step_training
            step_name: "Training stack -- lerobot[training]"
        InstallStep:
            id: step_git_lfs
            step_name: "Git LFS assets"
        InstallStep:
            id: step_ffmpeg
            step_name: "ffmpeg"
        MDCard:
            orientation: "vertical"
            padding: "10dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            line_color: {BORDER}
            md_bg_color: {BG1}
            size_hint_y: None
            height: "40dp"
            on_release: app.run_hf_login(install_chat)
            MDLabel:
                text: "Hugging Face Login (optional -- needed to push datasets)"
                halign: "center"
                font_style: "Caption"
                theme_text_color: "Custom"
                text_color: {TEXT_DIM}
        ChatWidget:
            id: install_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 0.3
                on_release: app.on_enter_install()
                MDLabel:
                    text: "Re-check"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT_DIM}
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                md_bg_color: {GREEN}
                size_hint_x: 0.4
                on_release: app.run_install_checks()
                MDLabel:
                    text: "Run Install"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {BG}
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 0.3
                on_release: app.show_screen("find_usb")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<FindUSBScreen>:
    name: "find_usb"
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "6dp"
        LevelHeader:
            title_text: "Find Ports"
            level_tag: "Level 2 of 8"
            back_target: "install_screen"
        MDLabel:
            text: "Pick an arm, plug it in, then run Find Port -- unplug it when asked."
            halign: "left"
            font_style: "Caption"
            size_hint_y: None
            height: "20dp"
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "10dp"
            size_hint_y: 0.22
            ArmTab:
                id: follower_card
                arm: "follower"
                label_text: "FOLLOWER"
                on_release: app.select_arm("follower", follower_card, leader_card, find_chat)
            ArmTab:
                id: leader_card
                arm: "leader"
                label_text: "LEADER"
                on_release: app.select_arm("leader", follower_card, leader_card, find_chat)
        MDCard:
            orientation: "vertical"
            padding: "10dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            md_bg_color: {GREEN}
            size_hint_y: None
            height: "44dp"
            on_release: app.run_find_port(find_chat)
            MDLabel:
                text: "Run Find Port"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {BG}
        ChatWidget:
            id: find_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("set_ids_baudrates")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<SetIDsBaudratesScreen>:
    name: "set_ids_baudrates"
    on_pre_enter: app.update_setup_labels()
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "6dp"
        LevelHeader:
            title_text: "Set Motor IDs"
            level_tag: "Level 3 of 8"
            back_target: "find_usb"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "12dp"
            size_hint_y: 0.5
            MDBoxLayout:
                orientation: "vertical"
                spacing: "8dp"
                size_hint_x: 0.32
                ArmTab:
                    id: setup_follower_card
                    arm: "follower"
                    label_text: "FOLLOWER"
                    on_release: app.select_setup_arm("follower", setup_follower_card, setup_leader_card, setup_chat)
                MDCard:
                    orientation: "vertical"
                    padding: "10dp"
                    radius: [8, 8, 8, 8]
                    elevation: 1
                    md_bg_color: {GREEN}
                    size_hint_y: None
                    height: "46dp"
                    on_release: app.run_setup_arm("follower", setup_follower_card, setup_leader_card, setup_chat)
                    MDLabel:
                        text: "Setup Motors"
                        halign: "center"
                        font_style: "Button"
                        theme_text_color: "Custom"
                        text_color: {BG}
            MDCard:
                orientation: "vertical"
                padding: "8dp"
                spacing: "3dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 0.36
                MDLabel:
                    text: "MOTOR PROGRESS"
                    halign: "center"
                    font_style: "Overline"
                    size_hint_y: None
                    height: "18dp"
                    theme_text_color: "Custom"
                    text_color: {TEXT_FAINT}
                MotorChip:
                    id: chip_shoulder_pan
                    label_text: "1 · Shoulder Pan"
                MotorChip:
                    id: chip_shoulder_lift
                    label_text: "2 · Shoulder Lift"
                MotorChip:
                    id: chip_elbow_flex
                    label_text: "3 · Elbow Flex"
                MotorChip:
                    id: chip_wrist_flex
                    label_text: "4 · Wrist Flex"
                MotorChip:
                    id: chip_wrist_roll
                    label_text: "5 · Wrist Roll"
                MotorChip:
                    id: chip_gripper
                    label_text: "6 · Gripper"
            MDBoxLayout:
                orientation: "vertical"
                spacing: "8dp"
                size_hint_x: 0.32
                ArmTab:
                    id: setup_leader_card
                    arm: "leader"
                    label_text: "LEADER"
                    on_release: app.select_setup_arm("leader", setup_follower_card, setup_leader_card, setup_chat)
                MDCard:
                    orientation: "vertical"
                    padding: "10dp"
                    radius: [8, 8, 8, 8]
                    elevation: 1
                    md_bg_color: {GREEN}
                    size_hint_y: None
                    height: "46dp"
                    on_release: app.run_setup_arm("leader", setup_follower_card, setup_leader_card, setup_chat)
                    MDLabel:
                        text: "Setup Motors"
                        halign: "center"
                        font_style: "Button"
                        theme_text_color: "Custom"
                        text_color: {BG}
        ChatWidget:
            id: setup_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("calibrate")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<CalibrateScreen>:
    name: "calibrate"
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "6dp"
        LevelHeader:
            title_text: "Calibrate"
            level_tag: "Level 4 of 8"
            back_target: "set_ids_baudrates"
        MDLabel:
            text: "Center every joint, press Enter, then sweep each one through its full range."
            halign: "left"
            font_style: "Caption"
            size_hint_y: None
            height: "20dp"
            theme_text_color: "Custom"
            text_color: {TEXT_DIM}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "12dp"
            size_hint_y: 0.2
            MDBoxLayout:
                orientation: "vertical"
                spacing: "8dp"
                ArmTab:
                    arm: "follower"
                    label_text: "FOLLOWER"
                MDCard:
                    padding: "10dp"
                    radius: [8, 8, 8, 8]
                    elevation: 1
                    md_bg_color: {GREEN}
                    size_hint_y: None
                    height: "46dp"
                    on_release: app.run_calibrate("follower", cal_chat)
                    MDLabel:
                        text: "Calibrate"
                        halign: "center"
                        font_style: "Button"
                        theme_text_color: "Custom"
                        text_color: {BG}
            MDBoxLayout:
                orientation: "vertical"
                spacing: "8dp"
                ArmTab:
                    arm: "leader"
                    label_text: "LEADER"
                MDCard:
                    padding: "10dp"
                    radius: [8, 8, 8, 8]
                    elevation: 1
                    md_bg_color: {GREEN}
                    size_hint_y: None
                    height: "46dp"
                    on_release: app.run_calibrate("leader", cal_chat)
                    MDLabel:
                        text: "Calibrate"
                        halign: "center"
                        font_style: "Button"
                        theme_text_color: "Custom"
                        text_color: {BG}
        ChatWidget:
            id: cal_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("teleoperate_screen")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<TeleoperateScreen>:
    name: "teleoperate_screen"
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "6dp"
        LevelHeader:
            title_text: "Teleoperate"
            level_tag: "Level 5 of 8"
            back_target: "calibrate"
        MDTextField:
            id: teleop_cam
            hint_text: "Camera index (e.g. 0)"
            text: "0"
            input_filter: "int"
            size_hint_y: None
            height: "44dp"
        MDCard:
            orientation: "vertical"
            padding: "10dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            md_bg_color: {YELLOW}
            size_hint_y: None
            height: "48dp"
            on_release: app.run_teleoperate(teleop_chat)
            MDLabel:
                text: "Start Teleoperate (runs until Stop)"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {BG}
        ChatWidget:
            id: teleop_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("record_screen")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<RecordScreen>:
    name: "record_screen"
    on_pre_enter: app.prefill_record()
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "5dp"
        LevelHeader:
            title_text: "Record"
            level_tag: "Level 6 of 8"
            back_target: "teleoperate_screen"
        MDTextField:
            id: record_task
            hint_text: "Task description, e.g. pick up the red block"
            size_hint_y: None
            height: "40dp"
        MDTextField:
            id: record_repo
            hint_text: "Dataset repo id"
            size_hint_y: None
            height: "40dp"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: record_eps
                hint_text: "Episodes"
                text: "50"
                input_filter: "int"
            MDTextField:
                id: record_eplen
                hint_text: "Episode s"
                text: "30"
                input_filter: "int"
            MDTextField:
                id: record_reset
                hint_text: "Reset s"
                text: "10"
                input_filter: "int"
            MDTextField:
                id: record_cam
                hint_text: "Camera idx"
                text: "0"
                input_filter: "int"
        MDCard:
            orientation: "vertical"
            padding: "10dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            md_bg_color: {GREEN}
            size_hint_y: None
            height: "44dp"
            on_release: app.run_record(record_chat)
            MDLabel:
                text: "Start Recording"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {BG}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDCard:
                padding: "6dp"
                radius: [6, 6, 6, 6]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                on_release: app.send_episode_key(record_chat, "left")
                MDLabel:
                    text: "Redo"
                    halign: "center"
                    font_style: "Caption"
                    theme_text_color: "Custom"
                    text_color: {TEXT_DIM}
            MDCard:
                padding: "6dp"
                radius: [6, 6, 6, 6]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                on_release: app.send_episode_key(record_chat, "right")
                MDLabel:
                    text: "Next Episode"
                    halign: "center"
                    font_style: "Caption"
                    theme_text_color: "Custom"
                    text_color: {TEXT_DIM}
            MDCard:
                padding: "6dp"
                radius: [6, 6, 6, 6]
                elevation: 1
                md_bg_color: {RED_DIM}
                on_release: app.send_episode_key(record_chat, "esc")
                MDLabel:
                    text: "Finish & Upload"
                    halign: "center"
                    font_style: "Caption"
                    theme_text_color: "Custom"
                    text_color: {RED_TEXT}
        ChatWidget:
            id: record_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("train_screen")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<TrainScreen>:
    name: "train_screen"
    on_pre_enter: app.prefill_train()
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "5dp"
        LevelHeader:
            title_text: "Train"
            level_tag: "Level 7 of 8"
            back_target: "record_screen"
        MDTextField:
            id: train_repo
            hint_text: "Dataset repo id"
            size_hint_y: None
            height: "40dp"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: train_policy
                hint_text: "Policy (act/diffusion/smolvla/pi0/pi05/xvla/wall_x)"
                text: "act"
            MDTextField:
                id: train_device
                hint_text: "Device (cuda/mps/cpu)"
                text: "cuda"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: train_batch
                hint_text: "Batch size"
                text: "8"
                input_filter: "int"
            MDTextField:
                id: train_steps
                hint_text: "Steps"
                text: "30000"
                input_filter: "int"
        MDTextField:
            id: train_policy_repo
            hint_text: "Push trained policy to (optional)"
            size_hint_y: None
            height: "40dp"
        MDBoxLayout:
            orientation: "horizontal"
            size_hint_y: None
            height: "36dp"
            spacing: "8dp"
            MDCheckbox:
                id: train_wandb
                size_hint_x: None
                width: "36dp"
            MDLabel:
                text: "Log to Weights & Biases"
                theme_text_color: "Custom"
                text_color: {TEXT_DIM}
                font_style: "Caption"
        MDCard:
            orientation: "vertical"
            padding: "10dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            md_bg_color: {GREEN}
            size_hint_y: None
            height: "44dp"
            on_release: app.run_train(train_chat)
            MDLabel:
                text: "Start Training (runs until done, or Stop)"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {BG}
        ChatWidget:
            id: train_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                line_color: {BORDER}
                md_bg_color: {BG1}
                size_hint_x: 1
                on_release: app.show_screen("evaluate_screen")
                MDLabel:
                    text: "Next"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {TEXT}

<EvaluateScreen>:
    name: "evaluate_screen"
    on_pre_enter: app.prefill_evaluate()
    canvas.before:
        Color:
            rgba: {BG}
        Rectangle:
            pos: self.pos
            size: self.size
    MDBoxLayout:
        orientation: "vertical"
        padding: "16dp"
        spacing: "4dp"
        LevelHeader:
            title_text: "Evaluate"
            level_tag: "Level 8 of 8"
            back_target: "train_screen"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "24dp"
            MDLabel:
                text: "REAL ROBOT EVAL"
                font_style: "Overline"
                theme_text_color: "Custom"
                text_color: {TEXT_FAINT}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: eval_task
                hint_text: "Task description"
            MDTextField:
                id: eval_repo
                hint_text: "Eval dataset repo id"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: eval_eps
                hint_text: "Episodes"
                text: "10"
                input_filter: "int"
            MDTextField:
                id: eval_policy_path
                hint_text: "Policy path / repo id"
        MDCard:
            orientation: "vertical"
            padding: "8dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            md_bg_color: {GREEN}
            size_hint_y: None
            height: "40dp"
            on_release: app.run_eval_record(eval_chat)
            MDLabel:
                text: "Run Real-Robot Eval"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {BG}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "24dp"
            MDLabel:
                text: "SIM BENCHMARK EVAL"
                font_style: "Overline"
                theme_text_color: "Custom"
                text_color: {TEXT_FAINT}
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: eval_sim_policy_path
                hint_text: "Policy path / repo id"
            MDTextField:
                id: eval_sim_env
                hint_text: "Env type (e.g. pusht)"
        MDBoxLayout:
            orientation: "horizontal"
            spacing: "8dp"
            size_hint_y: None
            height: "40dp"
            MDTextField:
                id: eval_sim_neps
                hint_text: "Episodes"
                text: "50"
                input_filter: "int"
            MDTextField:
                id: eval_sim_batch
                hint_text: "Batch size"
                text: "10"
                input_filter: "int"
        MDCard:
            orientation: "vertical"
            padding: "8dp"
            radius: [8, 8, 8, 8]
            elevation: 1
            line_color: {BORDER}
            md_bg_color: {BG1}
            size_hint_y: None
            height: "40dp"
            on_release: app.run_eval_sim(eval_chat)
            MDLabel:
                text: "Run Sim Benchmark"
                halign: "center"
                font_style: "Button"
                theme_text_color: "Custom"
                text_color: {TEXT}
        ChatWidget:
            id: eval_chat
        NavRow:
            MDCard:
                orientation: "vertical"
                padding: "10dp"
                radius: [8, 8, 8, 8]
                elevation: 1
                md_bg_color: {GREEN_DIM}
                size_hint_x: 1
                on_release: app.show_screen("main_menu")
                MDLabel:
                    text: "Done"
                    halign: "center"
                    font_style: "Button"
                    theme_text_color: "Custom"
                    text_color: {GREEN_TEXT}
'''


class ChatWidget(BoxLayout):
    status_color = ListProperty([0.420, 0.447, 0.502, 1])

    STATUS_COLORS = {
        "idle": [0.420, 0.447, 0.502, 1],
        "running": [0.918, 0.702, 0.031, 1],
        "success": [0.133, 0.773, 0.369, 1],
        "error": [0.937, 0.267, 0.267, 1],
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._has_output = False

    def set_status(self, status):
        color = self.STATUS_COLORS.get(status, self.STATUS_COLORS["idle"])
        Clock.schedule_once(lambda dt: setattr(self, 'status_color', color))

    def add_message(self, text):
        """A deliberate, colored framework line ($ cmd, tips, exit markers)."""
        stripped = text.strip()
        lower = stripped.lower()
        if stripped.startswith("$"):
            color = "#9ca3af"
        elif lower.startswith("warning:") or "error" in lower:
            color = "#f87171"
        elif lower.startswith("tip:"):
            color = "#fbbf24"
        elif "done" in lower or "exit 0" in lower or "ready" in lower:
            color = "#4ade80"
        elif stripped.startswith("--"):
            color = "#6b7280"
        else:
            color = "#e5e7eb"
        markup_line = f"[color={color}]{escape_markup(text)}[/color]"
        self._append(markup_line)

    def append_raw(self, text):
        """Raw (ANSI-stripped) terminal output, appended as-is -- this is what
        makes sudo/interactive prompts (which print with no trailing newline)
        show up immediately instead of waiting for a full line."""
        if not text:
            return
        self._append(escape_markup(text), newline=False)

    def _append(self, markup_line, newline=True):
        def _update(dt):
            log = self.ids.chat_log
            if self._has_output:
                log.text = log.text + ("\n" if newline else "") + markup_line
            else:
                log.text = markup_line
                self._has_output = True
            Clock.schedule_once(lambda dt: setattr(self.ids.scroll_view, 'scroll_y', 0), 0.05)
        Clock.schedule_once(_update)


class MainMenuScreen(Screen): pass
class InstallScreen(Screen): pass
class FindUSBScreen(Screen): pass
class SetIDsBaudratesScreen(Screen): pass
class CalibrateScreen(Screen): pass
class TeleoperateScreen(Screen): pass
class RecordScreen(Screen): pass
class TrainScreen(Screen): pass
class EvaluateScreen(Screen): pass


# ── install-script marker protocol (mirrors launch_lab/server.py) ──
_INSTALL_MARKER_RE = re.compile(r"@@INSTALL:(\w+):(start|skip|exit):?(\d+)?@@")


def _build_install_script(steps):
    parts = []
    for step in steps:
        key = step["key"]
        if step["already_installed"]:
            parts.append(f"echo '@@INSTALL:{key}:skip@@'")
        else:
            parts.append(f"echo '@@INSTALL:{key}:start@@'")
            parts.append(step["cmd"])
            parts.append(f'code=$?; echo "@@INSTALL:{key}:exit:$code@@"')
    return " ; ".join(parts) if parts else "echo 'Nothing to install -- everything is ready.'"


def _slug(text):
    text = (text or "task").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "task"


class LeRobotGUI(MDApp):

    active_session = None
    find_active_arm = 'follower'   # which arm is selected on Find USB screen
    viewer_bridge = None            # initialised in build()
    _last_chat = None                # ChatWidget of the most recently started command

    def build(self):
        self.state = AppState()
        self.title = "LeRobot Launch Lab"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.primary_hue = "500"
        self.theme_cls.theme_style = "Dark"

        if ViewerBridge is not None:
            try:
                self.viewer_bridge = ViewerBridge()
                self.viewer_bridge.start()
                self.state.viewer_bridge = self.viewer_bridge
            except Exception as e:
                print(f"3D viewer bridge unavailable: {e}")
                self.viewer_bridge = None

        Window.bind(on_key_down=self._on_key_down)
        return Builder.load_string(KV)

    def on_stop(self):
        if self.viewer_bridge:
            self.viewer_bridge.stop()
        if self.active_session:
            self.active_session.stop()

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        if key not in (13, 271):  # Enter / numpad Enter
            return False
        if not self.active_session or not self.active_session.is_alive() or not self._last_chat:
            return False
        cmd_input = self._last_chat.ids.get('cmd_input')
        if cmd_input is not None and cmd_input.focus:
            return False  # the text field's own on_text_validate already handles this
        self.send_terminal_command('', self._last_chat)
        return True

    def show_screen(self, name):
        self.root.current = name

    def go_to_level(self, level):
        screen = LEVEL_SCREENS.get(level)
        if screen:
            self.show_screen(screen)
        else:
            toast(f"{LEVEL_TITLES.get(level, level)} -- coming soon")

    def open_viewer(self):
        if self.viewer_bridge:
            self.viewer_bridge.open_viewer()
        else:
            toast("3D viewer unavailable -- check the terminal for details")

    def stop_current_session(self):
        if self.active_session and self.active_session.is_alive():
            self.active_session.stop()
            if self._last_chat:
                self._last_chat.add_message("-- stopped --")
                self._last_chat.set_status("error")

    # ── quest map ──────────────────────────────────────────────

    def refresh_level_nodes(self):
        try:
            screen = self.root.get_screen('main_menu')
        except Exception:
            return

        progress = self.state.progress()
        screen.ids.xp_bar.value = progress["xp"]
        screen.ids.quest_summary.text = f"{progress['done']} / {progress['total']} quests · XP {progress['xp']}"
        screen.ids.rank_label.text = progress["rank"]

        steps_by_key = {s.key: s for s in self.state.quest_steps}
        node_ids = {level: f"node_{level}" for level in LEVELS}
        prev_done = True
        for level in LEVELS:
            node = screen.ids[node_ids[level]]
            done = progress["levels_done"][level]
            step = steps_by_key.get(level)
            node.subtitle_text = step.subtitle if step else ""
            node.reward_text = step.reward if step else ""
            if done:
                node.status_text = "Done"
                node.status_color = GREEN_TEXT_T
                node.node_color = BG1_T
            elif prev_done:
                node.status_text = "Play"
                node.status_color = (1, 1, 1, 1)
                node.node_color = BG1_T
            else:
                node.status_text = "Locked"
                node.status_color = TEXT_FAINT_T
                node.node_color = BG2_T
            prev_done = done

    def _on_level_complete(self, level, arm=None):
        Clock.schedule_once(lambda dt: self.refresh_level_nodes())

    # ── shared PTY runner ──────────────────────────────────────

    def _run_pty(self, cmd, chat, on_line=None, on_exit=None):
        """Real pseudo-terminal execution -- sudo prompts, interactive
        confirmations, and arrow-key-driven flows all work because this is a
        genuine tty, not a plain subprocess pipe."""
        if self.active_session and self.active_session.is_alive():
            chat.add_message("Warning: a command is already running. Stop it first, or wait for it to finish.")
            return None

        self._last_chat = chat
        chat.add_message(f"$ {cmd}")
        chat.set_status("running")

        def _on_output(data: bytes):
            text = strip_ansi(data)
            if text:
                chat.append_raw(text)

        def _on_line(line: str):
            lower = line.lower()
            if any(k in lower for k in ("error", "failed", "no status packet", "traceback", "exception")):
                suggestion = detect_error_suggestion(line)
                Clock.schedule_once(lambda dt: chat.add_message(f"Tip: {suggestion}"))
            if on_line:
                on_line(line)

        def _on_exit(code: int):
            Clock.schedule_once(lambda dt: chat.add_message(f"-- exit {code} --"))
            Clock.schedule_once(lambda dt: chat.set_status("success" if code == 0 else "error"))
            if on_exit:
                on_exit(code)

        session = PtySession(
            session_id=uuid.uuid4().hex[:8],
            cmd=cmd,
            cwd=commands.REPO_ROOT,
            on_output=_on_output,
            on_line=_on_line,
            on_exit=_on_exit,
        )
        self.active_session = session
        session.start()
        return session

    def send_terminal_command(self, cmd_text, chat):
        Clock.schedule_once(lambda dt: setattr(chat.ids.cmd_input, 'text', ''), 0)

        if self.active_session and self.active_session.is_alive():
            payload = (cmd_text.strip() + '\n') if cmd_text.strip() else '\n'
            self.active_session.write(payload.encode())
            chat.add_message(f"[sent: {cmd_text.strip() or 'Enter'}]")
            return

        if not cmd_text.strip():
            return
        self._run_pty(cmd_text, chat)

    def send_episode_key(self, chat, which):
        """Redo / Next episode / Finish -- forwards the same key lerobot-record
        listens for (arrow keys, ESC) straight into the pty."""
        if not (self.active_session and self.active_session.is_alive()):
            chat.add_message("Warning: nothing is recording right now.")
            return
        codes = {"left": b"\x1b[D", "right": b"\x1b[C", "esc": b"\x1b"}
        self.active_session.write(codes[which])
        chat.add_message(f"[key: {which}]")

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _pulse(active, inactive):
        (Animation(opacity=0.4, duration=0.08) +
         Animation(opacity=1.0, duration=0.18)).start(active)
        Animation(opacity=0.45, duration=0.15).start(inactive)

    def _select_arm_ui(self, arm, f_card, l_card):
        active, inactive = (f_card, l_card) if arm == 'follower' else (l_card, f_card)
        self._pulse(active, inactive)
        active.selected = True
        inactive.selected = False

    # ── Level 1: Install ─────────────────────────────────────

    def on_enter_install(self):
        steps = commands.install_steps()
        try:
            screen = self.root.get_screen('install_screen')
        except Exception:
            return
        for step in steps:
            widget = screen.ids.get(f"step_{step['key']}")
            if widget:
                widget.status_text = "Ready" if step["already_installed"] else "Pending"
                widget.status_color = GREEN_TEXT_T if step["already_installed"] else TEXT_FAINT_T
                widget.step_color = GREEN_DIM_T if step["already_installed"] else BG1_T

    def run_install_checks(self):
        try:
            screen = self.root.get_screen('install_screen')
        except Exception:
            return
        steps = commands.install_steps()
        for step in steps:
            widget = screen.ids.get(f"step_{step['key']}")
            if widget:
                widget.status_text = "Checking..."
                widget.status_color = AMBER_T
                widget.step_color = AMBER_DIM_T
        script = _build_install_script(steps)
        chat = screen.ids.install_chat

        def on_line(line):
            m = _INSTALL_MARKER_RE.search(line)
            if not m:
                return
            key, kind, code = m.group(1), m.group(2), m.group(3)
            widget = screen.ids.get(f"step_{key}")
            if not widget:
                return
            if kind == "skip":
                Clock.schedule_once(lambda dt: (setattr(widget, 'status_text', 'Already installed'), setattr(widget, 'status_color', GREEN_TEXT_T), setattr(widget, 'step_color', GREEN_DIM_T)))
            elif kind == "start":
                Clock.schedule_once(lambda dt: (setattr(widget, 'status_text', 'Installing...'), setattr(widget, 'status_color', AMBER_T), setattr(widget, 'step_color', AMBER_DIM_T)))
            elif kind == "exit":
                ok = code == "0"
                Clock.schedule_once(lambda dt: (
                    setattr(widget, 'status_text', 'Installed' if ok else 'Failed'),
                    setattr(widget, 'status_color', GREEN_TEXT_T if ok else RED_TEXT_T),
                    setattr(widget, 'step_color', GREEN_DIM_T if ok else RED_DIM_T),
                ))

        def on_exit(code):
            fresh = commands.install_steps()
            if all(s["already_installed"] for s in fresh):
                self.state.complete_level("install")
                self._on_level_complete("install")

        self._run_pty(script, chat, on_line=on_line, on_exit=on_exit)

    def run_hf_login(self, chat):
        self._run_pty(commands.hf_login_cmd(), chat)

    # ── Level 2: Find USB ────────────────────────────────────

    def select_arm(self, arm, f_card, l_card, chat):
        self.find_active_arm = arm
        self._select_arm_ui(arm, f_card, l_card)
        port = self.state.found_ports.get(arm, '')
        chat.add_message(f"Selected {arm.upper()} -- Port: {port or '(run Find Port first)'}")

    def run_find_port(self, chat):
        arm = self.find_active_arm
        seen_prompt = {"flag": False}

        def on_line(line):
            lower = line.lower()
            if not seen_prompt["flag"] and ("press enter" in lower or "remove" in lower):
                seen_prompt["flag"] = True
                Clock.schedule_once(lambda dt: chat.add_message("-- Disconnect USB now --"))
                return
            if seen_prompt["flag"] and not self.state.found_ports.get(arm):
                m = re.search(r"(/dev/tty[^\s'\"\\,\]]+)", line)
                if m:
                    port = m.group(1)
                    self.state.set_port(arm, port)
                    self.state.complete_level("find_ports", arm=arm)
                    self._on_level_complete("find_ports", arm=arm)
                    Clock.schedule_once(lambda dt: chat.add_message(f"Done: stored {arm.upper()} port {port}"))
                    Clock.schedule_once(lambda dt: self.update_setup_labels(), 0.1)

        self._run_pty(commands.find_port_cmd(), chat, on_line=on_line)

    # ── Level 3: Set IDs / Baudrates ─────────────────────────

    def update_setup_labels(self):
        try:
            s = self.root.get_screen('set_ids_baudrates')
            fp = self.state.found_ports.get('follower', '')
            lp = self.state.found_ports.get('leader', '')
            s.ids.setup_follower_card.port_text = fp or "no port yet"
            s.ids.setup_leader_card.port_text = lp or "no port yet"
        except Exception:
            pass

    def select_setup_arm(self, arm, f_card, l_card, chat):
        self._select_arm_ui(arm, f_card, l_card)
        port = self.state.found_ports.get(arm, '')
        chat.add_message(f"Selected {arm.upper()} -- Port: {port or '(not found -- run Find Port first)'}")

    def reset_motor_checklist(self):
        try:
            screen = self.root.get_screen('set_ids_baudrates')
        except Exception:
            return
        for motor in MOTOR_ORDER:
            chip = screen.ids.get(f"chip_{motor}")
            if chip:
                chip.dot_color = TEXT_FAINT_T

    def run_setup_arm(self, arm, f_card, l_card, chat):
        self.select_setup_arm(arm, f_card, l_card, chat)
        port = self.state.found_ports.get(arm, '')
        if not port:
            chat.add_message(f"Warning: no port stored for {arm.upper()}. Go back and run Find Port.")
            return

        self.reset_motor_checklist()

        def on_line(line):
            self.state.on_setup_motor_line(line)
            m = re.search(r"Connect the controller board to the '(\w+)' motor only", line)
            if m:
                self._set_motor_chip(m.group(1), AMBER_T)
                return
            m = re.search(r"'(\w+)' motor id set to (\d+)", line)
            if m:
                self._set_motor_chip(m.group(1), GREEN_TEXT_T)

        def on_exit(code):
            if code == 0:
                self.state.complete_level("set_motor_ids", arm=arm)
                self._on_level_complete("set_motor_ids", arm=arm)

        self._run_pty(commands.setup_motors_cmd(arm, port), chat, on_line=on_line, on_exit=on_exit)

    def _set_motor_chip(self, motor, color):
        def _update(dt):
            try:
                screen = self.root.get_screen('set_ids_baudrates')
            except Exception:
                return
            chip = screen.ids.get(f"chip_{motor}")
            if chip:
                chip.dot_color = color
        Clock.schedule_once(_update)

    # ── Level 4: Calibrate ───────────────────────────────────

    def run_calibrate(self, arm, chat):
        port = self.state.found_ports.get(arm, '') or ('/dev/ttyACM0' if arm == 'follower' else '/dev/ttyACM1')

        def on_exit(code):
            if code == 0:
                self.state.complete_level("calibrate", arm=arm)
                self._on_level_complete("calibrate", arm=arm)

        self._run_pty(commands.calibrate_cmd(arm, port), chat, on_exit=on_exit)

    # ── Level 5: Teleoperate ─────────────────────────────────

    def run_teleoperate(self, chat):
        try:
            screen = self.root.get_screen('teleoperate_screen')
            cam_index = int(screen.ids.teleop_cam.text or 0)
        except Exception:
            cam_index = 0
        cameras = [{"name": "front", "index_or_path": cam_index}]
        cmd = commands.teleoperate_cmd(
            self.state.found_ports.get("follower", "/dev/ttyACM0"),
            self.state.found_ports.get("leader", "/dev/ttyACM1"),
            cameras,
        )

        def on_exit(code):
            if code == 0:
                self.state.complete_level("teleoperate")
                self._on_level_complete("teleoperate")

        self._run_pty(cmd, chat, on_exit=on_exit)

    # ── Level 6: Record ──────────────────────────────────────

    def prefill_record(self):
        try:
            screen = self.root.get_screen('record_screen')
        except Exception:
            return
        user = self.state.hf_user or "your_hf_user"
        if not screen.ids.record_repo.text:
            screen.ids.record_repo.text = f"{user}/task"

        def _sync_repo(instance, value):
            screen.ids.record_repo.text = f"{user}/{_slug(value)}"
        screen.ids.record_task.bind(text=_sync_repo)

    def run_record(self, chat):
        try:
            screen = self.root.get_screen('record_screen')
            repo_id = screen.ids.record_repo.text
            task = screen.ids.record_task.text
            num_episodes = int(screen.ids.record_eps.text or 50)
            episode_time_s = int(screen.ids.record_eplen.text or 30)
            reset_time_s = int(screen.ids.record_reset.text or 10)
            cam_index = int(screen.ids.record_cam.text or 0)
        except Exception as e:
            chat.add_message(f"Warning: fix the form fields: {e}")
            return
        if not repo_id or not task:
            chat.add_message("Warning: fill in a task description and dataset repo id first.")
            return

        cmd = commands.record_cmd(
            self.state.found_ports.get("follower", "/dev/ttyACM0"),
            self.state.found_ports.get("leader", "/dev/ttyACM1"),
            [{"name": "front", "index_or_path": cam_index}],
            repo_id, task, num_episodes, episode_time_s, reset_time_s,
        )

        def on_exit(code):
            if code == 0:
                self.state.set_last_dataset(repo_id)
                self.state.complete_level("record")
                self._on_level_complete("record")

        self._run_pty(cmd, chat, on_exit=on_exit)

    # ── Level 7: Train ───────────────────────────────────────

    def prefill_train(self):
        try:
            screen = self.root.get_screen('train_screen')
        except Exception:
            return
        if not screen.ids.train_repo.text and self.state.last_dataset_repo_id:
            screen.ids.train_repo.text = self.state.last_dataset_repo_id

    def run_train(self, chat):
        try:
            screen = self.root.get_screen('train_screen')
            repo_id = screen.ids.train_repo.text
            policy_type = screen.ids.train_policy.text or "act"
            device = screen.ids.train_device.text or "cuda"
            batch_size = int(screen.ids.train_batch.text or 8)
            steps_text = screen.ids.train_steps.text
            steps = int(steps_text) if steps_text else None
            wandb_enable = screen.ids.train_wandb.active
            policy_repo_id = screen.ids.train_policy_repo.text or None
        except Exception as e:
            chat.add_message(f"Warning: fix the form fields: {e}")
            return
        if not repo_id:
            chat.add_message("Warning: fill in a dataset repo id first.")
            return

        cmd = commands.train_cmd(
            repo_id=repo_id, policy_type=policy_type, device=device,
            batch_size=batch_size, steps=steps, wandb_enable=wandb_enable,
            policy_repo_id=policy_repo_id,
        )

        def on_exit(code):
            if code == 0:
                if policy_repo_id:
                    self.state.set_last_policy(policy_repo_id)
                self.state.complete_level("train")
                self._on_level_complete("train")

        self._run_pty(cmd, chat, on_exit=on_exit)

    # ── Level 8: Evaluate ────────────────────────────────────

    def prefill_evaluate(self):
        try:
            screen = self.root.get_screen('evaluate_screen')
        except Exception:
            return
        if not screen.ids.eval_repo.text:
            user = self.state.hf_user or "your_hf_user"
            screen.ids.eval_repo.text = f"{user}/eval_task"
        if not screen.ids.eval_policy_path.text and self.state.last_policy_repo_id:
            screen.ids.eval_policy_path.text = self.state.last_policy_repo_id
        if not screen.ids.eval_sim_policy_path.text and self.state.last_policy_repo_id:
            screen.ids.eval_sim_policy_path.text = self.state.last_policy_repo_id

    def run_eval_record(self, chat):
        try:
            screen = self.root.get_screen('evaluate_screen')
            task = screen.ids.eval_task.text
            repo_id = screen.ids.eval_repo.text
            num_episodes = int(screen.ids.eval_eps.text or 10)
            policy_path = screen.ids.eval_policy_path.text
        except Exception as e:
            chat.add_message(f"Warning: fix the form fields: {e}")
            return
        if not policy_path or not repo_id:
            chat.add_message("Warning: fill in the policy path and eval dataset repo id first.")
            return

        cmd = commands.eval_record_cmd(
            self.state.found_ports.get("follower", "/dev/ttyACM0"),
            [{"name": "front", "index_or_path": 0}],
            repo_id, task, policy_path, num_episodes,
        )

        def on_exit(code):
            if code == 0:
                self.state.complete_level("evaluate")
                self._on_level_complete("evaluate")

        self._run_pty(cmd, chat, on_exit=on_exit)

    def run_eval_sim(self, chat):
        try:
            screen = self.root.get_screen('evaluate_screen')
            policy_path = screen.ids.eval_sim_policy_path.text
            env_type = screen.ids.eval_sim_env.text
            n_episodes = int(screen.ids.eval_sim_neps.text or 50)
            batch_size = int(screen.ids.eval_sim_batch.text or 10)
        except Exception as e:
            chat.add_message(f"Warning: fix the form fields: {e}")
            return
        if not policy_path or not env_type:
            chat.add_message("Warning: fill in the policy path and env type first.")
            return

        cmd = commands.eval_sim_cmd(policy_path, env_type, n_episodes, batch_size)

        def on_exit(code):
            if code == 0:
                self.state.complete_level("evaluate")
                self._on_level_complete("evaluate")

        self._run_pty(cmd, chat, on_exit=on_exit)


if __name__ == "__main__":
    LeRobotGUI().run()
