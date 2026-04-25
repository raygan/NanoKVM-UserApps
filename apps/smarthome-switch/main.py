#!/usr/bin/env python3
"""
SmartHome Switch UI for NanoKVM.

Setup flow (first run):
  1. Install packages  (HAP-python, Pillow, paho-mqtt)
  2. Mode selection    (HomeKit / Home Assistant / Both)
  3. MQTT config       (if MQTT selected — show web config URL)
  4. Register service  (copy .service file, daemon-reload)
  5. Pairing screen    (HomeKit code and/or HA instructions)
  6. Control screen    (live status + start/stop)

Subsequent runs skip straight to step 6.

Display layout (320×240, rotation=270):
  Touchable area : 320×172  (y=0..171)
  Non-touch zone : 320×68   (y=172..239)
"""

import json
import os
import shutil
import socket
import subprocess
import time
import threading

from framebuffer import Framebuffer
from input import TouchScreen, GpioKeys

# ── colours ───────────────────────────────────────────────────────────────────
C_BLACK     = (0,   0,   0)
C_WHITE     = (255, 255, 255)
C_GRAY      = (128, 128, 128)
C_DARK_GRAY = (48,  48,  48)
C_MED_GRAY  = (80,  80,  80)
C_GREEN     = (80,  213, 83)
C_RED       = (213, 80,  80)
C_BLUE      = (80,  160, 213)
C_AMBER     = (213, 150, 50)
C_PURPLE    = (150, 100, 210)
C_YELLOW    = (213, 200, 80)
C_DIVIDER   = (60,  60,  60)

# ── layout ────────────────────────────────────────────────────────────────────
SCREEN_W = 320
SCREEN_H = 240
TOUCH_H  = 172

HEADER_H  = 28
CONTENT_Y = HEADER_H
CONTENT_H = TOUCH_H - HEADER_H   # 144 px

DIVIDER_X = 155
RIGHT_X   = DIVIDER_X + 1
RIGHT_W   = SCREEN_W - RIGHT_X   # 164 px

PIN_ZONE_Y = TOUCH_H

BACK_BTN_X = 4
BACK_BTN_Y = 4
BACK_BTN_W = 32
BACK_BTN_H = 20

# ── paths / names ─────────────────────────────────────────────────────────────
SERVICE_NAME = 'smarthome-switch'
SERVICE_DST  = f'/etc/systemd/system/{SERVICE_NAME}.service'
APP_DIR      = os.path.dirname(os.path.abspath(__file__))
SERVICE_SRC  = os.path.join(APP_DIR, f'{SERVICE_NAME}.service')
CONFIG_FILE  = '/userapp/smarthome-switch/config.json'
STATE_FILE   = '/tmp/smarthome_state.json'

REQUIRED_PACKAGES = ['HAP-python', 'Pillow', 'paho-mqtt']


# ── config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def read_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '?.?.?.?'


# ── package helpers ───────────────────────────────────────────────────────────

def packages_installed() -> bool:
    try:
        import pyhap   # noqa: F401
        import PIL     # noqa: F401
        import paho    # noqa: F401
        return True
    except ImportError:
        return False


def install_packages(progress_cb=None) -> bool:
    try:
        if progress_cb:
            progress_cb(0, 'Starting...')
        proc = subprocess.Popen(
            ['pip3', 'install', '--quiet'] + REQUIRED_PACKAGES,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        pct = 5
        while proc.poll() is None:
            time.sleep(0.3)
            pct = min(pct + 3, 90)
            if progress_cb:
                progress_cb(pct, 'Installing...')
        success = proc.returncode == 0
        if progress_cb:
            progress_cb(100, 'Done!' if success else 'Failed!')
        return success
    except Exception as e:
        print(f'install_packages: {e}')
        if progress_cb:
            progress_cb(100, 'Error!')
        return False


# ── service helpers ───────────────────────────────────────────────────────────

def service_registered() -> bool:
    return os.path.exists(SERVICE_DST)


def register_service() -> bool:
    try:
        shutil.copy(SERVICE_SRC, SERVICE_DST)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=10)
        return True
    except Exception as e:
        print(f'register_service: {e}')
        return False


def service_active() -> bool:
    try:
        r = subprocess.run(
            ['systemctl', 'is-active', SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() == 'active'
    except Exception:
        return False


def service_start() -> bool:
    try:
        subprocess.run(['systemctl', 'enable', '--now', SERVICE_NAME],
                       check=True, timeout=10)
        return True
    except Exception as e:
        print(f'service_start: {e}')
        return False


def service_stop() -> bool:
    try:
        subprocess.run(['systemctl', 'disable', '--now', SERVICE_NAME],
                       check=True, timeout=10)
        return True
    except Exception as e:
        print(f'service_stop: {e}')
        return False


def wait_for_state(timeout: float = 10.0) -> dict:
    """Block until the daemon writes the state file, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_state()
        if state:
            return state
        time.sleep(0.4)
    return {}


# ── UI class ──────────────────────────────────────────────────────────────────

class UI:
    def __init__(self, fb: Framebuffer):
        self.fb = fb

    # ── shared chrome ─────────────────────────────────────────────────────────

    def _header(self, title: str = 'NanoKVM  SmartHome'):
        self.fb.draw_rect(0, 0, SCREEN_W, HEADER_H, C_MED_GRAY, auto_swap=False)
        tw, _ = self.fb.get_text_size(title)
        self.fb.draw_text((SCREEN_W - tw) // 2, 6, title, C_WHITE, auto_swap=False)

    def _back_btn(self, pressed: bool = False):
        bg = (100, 40, 40) if pressed else C_DARK_GRAY
        self.fb.draw_rect(BACK_BTN_X, BACK_BTN_Y, BACK_BTN_W, BACK_BTN_H,
                          C_GRAY, auto_swap=False)
        self.fb.draw_rect(BACK_BTN_X + 1, BACK_BTN_Y + 1,
                          BACK_BTN_W - 2, BACK_BTN_H - 2, bg, auto_swap=False)
        self.fb.draw_text(BACK_BTN_X + 9, BACK_BTN_Y + 2, '<', C_WHITE, auto_swap=False)

    def _button(self, x, y, w, h, label, color, pressed=False):
        border = tuple(min(255, c + 40) for c in color)
        fill   = tuple(max(0, c - 40) for c in color) if pressed else color
        self.fb.draw_rect(x, y, w, h, border, auto_swap=False)
        self.fb.draw_rect(x + 2, y + 2, w - 4, h - 4, fill, auto_swap=False)
        lw, lh = self.fb.get_text_size(label)
        self.fb.draw_text(x + (w - lw) // 2, y + (h - lh) // 2,
                          label, C_WHITE, auto_swap=False)

    def _pin_zone(self, text: str, color=None):
        color = color or C_GRAY
        self.fb.draw_rect(0, PIN_ZONE_Y, SCREEN_W, SCREEN_H - PIN_ZONE_Y,
                          C_DARK_GRAY, auto_swap=False)
        tw, _ = self.fb.get_text_size(text)
        self.fb.draw_text(max(4, (SCREEN_W - tw) // 2),
                          PIN_ZONE_Y + 24, text, color, auto_swap=False)

    def is_back(self, x, y) -> bool:
        return (BACK_BTN_X <= x <= BACK_BTN_X + BACK_BTN_W and
                BACK_BTN_Y <= y <= BACK_BTN_Y + BACK_BTN_H)

    # ── install screen ────────────────────────────────────────────────────────

    INSTALL_BTN = (RIGHT_X + 8, CONTENT_Y + (CONTENT_H - 52) // 2,
                   RIGHT_W - 16, 52)

    def draw_install(self):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()
        self.fb.draw_rect(5, CONTENT_Y + 5, DIVIDER_X - 10, CONTENT_H - 10,
                          C_DARK_GRAY, auto_swap=False)
        for i, line in enumerate(['Packages not', 'installed.', '', 'Tap Install', 'to continue.']):
            self.fb.draw_text(14, CONTENT_Y + 14 + i * 22, line, C_GRAY, auto_swap=False)
        bx, by, bw, bh = self.INSTALL_BTN
        self._button(bx, by, bw, bh, 'INSTALL', C_BLUE)
        self._pin_zone('Required packages: HAP-python  Pillow  paho-mqtt')
        self.fb.swap_buffer()

    def draw_install_progress(self, pct: int, msg: str):
        rx, ry = RIGHT_X + 8, CONTENT_Y + 20
        rw = RIGHT_W - 16
        self.fb.draw_rect(RIGHT_X, CONTENT_Y, RIGHT_W, CONTENT_H, C_BLACK, auto_swap=False)
        mw, _ = self.fb.get_text_size(msg)
        self.fb.draw_text(RIGHT_X + (RIGHT_W - mw) // 2, ry, msg, C_WHITE, auto_swap=False)
        pt, _ = self.fb.get_text_size(f'{pct}%')
        self.fb.draw_text(RIGHT_X + (RIGHT_W - pt) // 2, ry + 30,
                          f'{pct}%', C_GRAY, auto_swap=False)
        bar_y, bar_h = ry + 60, 18
        self.fb.draw_rect(rx, bar_y, rw, bar_h, C_MED_GRAY, auto_swap=False)
        fill_w = max(2, int(rw * pct / 100))
        self.fb.draw_rect(rx, bar_y, fill_w, bar_h,
                          C_GREEN if pct >= 100 else C_BLUE, auto_swap=False)
        self.fb.swap_buffer()

    def is_install_btn(self, x, y) -> bool:
        bx, by, bw, bh = self.INSTALL_BTN
        return bx <= x <= bx + bw and by <= y <= by + bh

    # ── mode select screen ────────────────────────────────────────────────────
    # Three full-width buttons stacked in the content area.

    _MODE_BTNS = [
        # (label, mode_value, color, y, h)
        ('Apple HomeKit',     'homekit', C_BLUE,   CONTENT_Y + 6,  40),
        ('Home Assistant (MQTT)', 'mqtt',  C_AMBER,  CONTENT_Y + 52, 40),
        ('Both',              'both',    C_PURPLE, CONTENT_Y + 98, 40),
    ]
    _MODE_BTN_X  = 10
    _MODE_BTN_W  = SCREEN_W - 20

    def draw_mode_select(self):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()
        for label, _, color, by, bh in self._MODE_BTNS:
            self._button(self._MODE_BTN_X, by, self._MODE_BTN_W, bh, label, color)
        self._pin_zone('How will you control this PC?')
        self.fb.swap_buffer()

    def mode_btn_hit(self, x, y) -> str | None:
        """Returns mode string if a mode button was tapped, else None."""
        if not (self._MODE_BTN_X <= x <= self._MODE_BTN_X + self._MODE_BTN_W):
            return None
        for _, mode, _, by, bh in self._MODE_BTNS:
            if by <= y <= by + bh:
                return mode
        return None

    # ── MQTT config screen ────────────────────────────────────────────────────

    _OK_BTN = (RIGHT_X + 8, CONTENT_Y + (CONTENT_H - 52) // 2, RIGHT_W - 16, 52)

    def draw_mqtt_config(self, ip: str, done: bool = False):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()

        # Left panel — URL instructions
        self.fb.draw_rect(5, CONTENT_Y + 5, DIVIDER_X - 10, CONTENT_H - 10,
                          C_DARK_GRAY, auto_swap=False)
        # Split URL across lines so it fits the 155px-wide panel
        url_lines = [
            ('Open in browser:', C_GRAY),
            ('http://',          C_WHITE),
            (ip,                 C_WHITE),
            (':8080',            C_WHITE),
            ('',                 C_GRAY),
            ('Enter your MQTT',  C_GRAY),
            ('broker details,',  C_GRAY),
            ('then tap OK.',     C_GRAY),
        ]
        for i, (line, col) in enumerate(url_lines):
            self.fb.draw_text(10, CONTENT_Y + 8 + i * 18, line, col, auto_swap=False)

        # Right panel
        bx, by, bw, bh = self._OK_BTN
        if done:
            self._button(bx, by, bw, bh, 'OK  Done!', C_GREEN)
        else:
            self.fb.draw_rect(RIGHT_X, CONTENT_Y, RIGHT_W, CONTENT_H,
                              C_DARK_GRAY, auto_swap=False)
            msg = 'Waiting...'
            mw, mh = self.fb.get_text_size(msg)
            self.fb.draw_text(RIGHT_X + (RIGHT_W - mw) // 2,
                              CONTENT_Y + (CONTENT_H - mh) // 2,
                              msg, C_GRAY, auto_swap=False)

        self._pin_zone('Configure MQTT broker, then tap OK on this screen')
        self.fb.swap_buffer()

    def update_mqtt_config_done(self):
        """Flip the right panel to the saved state without a full-screen redraw.
        Avoids the fill_screen black-flash on slow hardware."""
        self.fb.draw_rect(RIGHT_X, CONTENT_Y, RIGHT_W, CONTENT_H, C_BLACK, auto_swap=False)
        msg = 'Saved!'
        mw, mh = self.fb.get_text_size(msg)
        self.fb.draw_text(RIGHT_X + (RIGHT_W - mw) // 2,
                          CONTENT_Y + (CONTENT_H - mh) // 2,
                          msg, C_GREEN, auto_swap=False)
        self._pin_zone('Config saved — continuing automatically...')
        self.fb.swap_buffer()

    def is_mqtt_ok_btn(self, x, y) -> bool:
        bx, by, bw, bh = self._OK_BTN
        return bx <= x <= bx + bw and by <= y <= by + bh

    # ── register screen ───────────────────────────────────────────────────────

    _REG_BTN = (RIGHT_X + 8, CONTENT_Y + (CONTENT_H - 52) // 2, RIGHT_W - 16, 52)

    def draw_register(self):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()
        self.fb.draw_rect(5, CONTENT_Y + 5, DIVIDER_X - 10, CONTENT_H - 10,
                          C_DARK_GRAY, auto_swap=False)
        for i, line in enumerate(['Service not', 'registered.', '', 'Tap Register', 'to set up.']):
            self.fb.draw_text(14, CONTENT_Y + 14 + i * 22, line, C_GRAY, auto_swap=False)
        bx, by, bw, bh = self._REG_BTN
        self._button(bx, by, bw, bh, 'REGISTER', C_BLUE)
        self._pin_zone('Register the background service to enable smart home control')
        self.fb.swap_buffer()

    def draw_register_working(self, msg: str):
        bx, by, bw, bh = self._REG_BTN
        self._button(bx, by, bw, bh, msg, C_GRAY, pressed=True)
        self.fb.swap_buffer()

    def draw_register_error(self, msg: str):
        bx, by, bw, bh = self._REG_BTN
        self._button(bx, by, bw, bh, msg, C_RED)
        self.fb.swap_buffer()

    def is_register_btn(self, x, y) -> bool:
        bx, by, bw, bh = self._REG_BTN
        return bx <= x <= bx + bw and by <= y <= by + bh

    # ── pairing screen ────────────────────────────────────────────────────────

    # Small OK button in the top-right of the header, mirroring the back button
    _PAIR_OK_BTN = (SCREEN_W - BACK_BTN_X - 44, BACK_BTN_Y, 44, BACK_BTN_H)

    def draw_pairing(self, mode: str, state: dict):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()

        # Small OK button top-right
        bx, by, bw, bh = self._PAIR_OK_BTN
        self._button(bx, by, bw, bh, 'OK', C_GREEN)

        hk   = state.get('homekit', {})
        y    = CONTENT_Y + 8
        LS   = 15  # line spacing — tight enough to fit both sections in 144px

        if mode in ('homekit', 'both'):
            self.fb.draw_text(8, y, 'HomeKit', C_BLUE, auto_swap=False)
            y += LS
            code = hk.get('setup_code', '---')
            self.fb.draw_text(8, y, f'Code: {code}', C_WHITE, auto_swap=False)
            y += LS
            for line in ['Home app > Add Accessory', 'More Options > enter code']:
                self.fb.draw_text(8, y, line, C_GRAY, auto_swap=False)
                y += LS
            y += 4

        if mode == 'both':
            self.fb.draw_rect(8, y, SCREEN_W - 16, 1, C_DIVIDER, auto_swap=False)
            y += 6

        if mode in ('mqtt', 'both'):
            self.fb.draw_text(8, y, 'Home Assistant', C_AMBER, auto_swap=False)
            y += LS
            for line in ['NanoKVM appears in HA automatically', 'Settings > Devices & Services']:
                self.fb.draw_text(8, y, line, C_GRAY, auto_swap=False)
                y += LS
            self.fb.draw_text(8, y, 'Enable MQTT integration in HA first.', C_GRAY, auto_swap=False)

        self._pin_zone('Tap OK when done — status shown on next screen')
        self.fb.swap_buffer()

    def is_pair_ok(self, x, y) -> bool:
        bx, by, bw, bh = self._PAIR_OK_BTN
        return bx <= x <= bx + bw and by <= y <= by + bh

    # ── control screen ────────────────────────────────────────────────────────

    def draw_control(self, mode: str, state: dict, running: bool):
        self.fb.fill_screen(C_BLACK)
        self._header()
        self._back_btn()
        self.fb.draw_rect(DIVIDER_X, CONTENT_Y, 1, CONTENT_H, C_DIVIDER, auto_swap=False)
        self._draw_control_left(mode, state, running)
        self._draw_control_right(mode, state, running)
        self._draw_control_pin(mode, state, running)
        self.fb.swap_buffer()

    def update_control(self, mode: str, state: dict, running: bool):
        """Redraw right panel and pin zone without touching left panel."""
        self._draw_control_right(mode, state, running)
        self._draw_control_pin(mode, state, running)
        self.fb.swap_buffer()

    def _draw_control_left(self, mode: str, state: dict, running: bool):
        hk      = state.get('homekit', {})
        paired  = hk.get('paired', False)
        code    = hk.get('setup_code', '')
        power   = state.get('power_on', False)

        ax, ay, aw, ah = 0, CONTENT_Y, DIVIDER_X, CONTENT_H
        self.fb.draw_rect(ax, ay, aw, ah, C_DARK_GRAY, auto_swap=False)

        if mode == 'mqtt':
            # Show MQTT broker info instead of HomeKit
            mq = state.get('mqtt', {})
            broker = mq.get('broker', '')
            lines = ['Home Assistant', '', broker or 'no broker set']
            for i, line in enumerate(lines):
                col = C_AMBER if i == 0 else C_GRAY
                lw, _ = self.fb.get_text_size(line)
                self.fb.draw_text(ax + (aw - lw) // 2, ay + 20 + i * 24,
                                  line, col, auto_swap=False)

        elif mode == 'both':
            # Show PC power status large in left panel
            self.fb.draw_text(ax + 8, ay + 12, 'PC Power', C_GRAY, auto_swap=False)
            txt, col = ('ON', C_GREEN) if (running and power) else ('OFF', C_RED) if running else ('?', C_GRAY)
            tw, th = self.fb.get_text_size(txt)
            # Draw big text by rendering it twice offset (poor man's bold)
            bx = ax + (aw - tw) // 2
            by2 = ay + (ah - th) // 2 + 8
            self.fb.draw_text(bx, by2, txt, col, auto_swap=False)

        else:
            # HomeKit only — show pairing code or paired checkmark
            if paired:
                self.fb.draw_rect(ax + aw//2 - 22, ay + 20, 44, 44, C_GREEN, auto_swap=False)
                ow, _ = self.fb.get_text_size('OK')
                self.fb.draw_text(ax + aw//2 - ow//2, ay + 34, 'OK', C_BLACK, auto_swap=False)
                lw, _ = self.fb.get_text_size('Paired')
                self.fb.draw_text(ax + (aw - lw)//2, ay + 78, 'Paired', C_GREEN, auto_swap=False)
            elif running and code:
                # Show pairing code
                c = code.replace('-', '')
                if len(c) == 8:
                    code = f'{c[:3]}-{c[3:5]}-{c[5:]}'
                for i, line in enumerate(['In Home app:', 'Add Accessory', 'More Options,', 'then enter:']):
                    self.fb.draw_text(ax + 6, ay + 8 + i * 22, line, C_GRAY, auto_swap=False)
                pw, _ = self.fb.get_text_size(code)
                self.fb.draw_text(ax + (aw - pw)//2, ay + ah - 26, code, C_WHITE, auto_swap=False)
            else:
                for i, line in enumerate(['Start service', 'to show code']):
                    lw, _ = self.fb.get_text_size(line)
                    self.fb.draw_text(ax + (aw - lw)//2,
                                      ay + ah//2 - 16 + i * 22, line, C_GRAY, auto_swap=False)

    def _draw_control_right(self, mode: str, state: dict, running: bool):
        hk      = state.get('homekit', {})
        mq      = state.get('mqtt', {})
        paired  = hk.get('paired', False)
        power   = state.get('power_on', False)
        mq_conn = mq.get('connected', False)

        if mode == 'both':
            # 3 rows: HomeKit (40px) + MQTT (40px) + button (64px) = 144px
            ROW_H_SM = 40
            BTN_H    = CONTENT_H - ROW_H_SM * 2   # 64px

            r1y = CONTENT_Y
            r2y = r1y + ROW_H_SM
            bny = r2y + ROW_H_SM

            # Row 1 — HomeKit
            self.fb.draw_rect(RIGHT_X, r1y, RIGHT_W, ROW_H_SM - 1, C_DARK_GRAY, auto_swap=False)
            self.fb.draw_text(RIGHT_X + 6, r1y + 4, 'HomeKit', C_GRAY, auto_swap=False)
            if not running:
                txt, col = 'Stopped', C_GRAY
            elif paired:
                txt, col = 'Paired', C_GREEN
            else:
                txt, col = 'Not paired', C_YELLOW
            self.fb.draw_text(RIGHT_X + 6, r1y + 20, txt, col, auto_swap=False)

            # Row 2 — MQTT
            self.fb.draw_rect(RIGHT_X, r2y, RIGHT_W, ROW_H_SM - 1, C_DARK_GRAY, auto_swap=False)
            self.fb.draw_text(RIGHT_X + 6, r2y + 4, 'Home Assistant', C_GRAY, auto_swap=False)
            if not running:
                txt, col = 'Stopped', C_GRAY
            elif mq_conn:
                txt, col = 'Connected', C_GREEN
            else:
                txt, col = 'Connecting...', C_AMBER
            self.fb.draw_text(RIGHT_X + 6, r2y + 20, txt, col, auto_swap=False)

            # Start/Stop button
            self._draw_svc_button(RIGHT_X, bny, RIGHT_W, BTN_H, running)

        else:
            # Single-protocol: 3 equal 48px rows
            ROW_H = 48
            r1y   = CONTENT_Y
            r2y   = r1y + ROW_H
            r3y   = r2y + ROW_H

            # Row 1 — protocol status
            self.fb.draw_rect(RIGHT_X, r1y, RIGHT_W, ROW_H - 1, C_DARK_GRAY, auto_swap=False)
            if mode == 'homekit':
                self.fb.draw_text(RIGHT_X + 6, r1y + 6, 'HomeKit', C_GRAY, auto_swap=False)
                if not running:
                    txt, col = 'Service stopped', C_GRAY
                elif paired:
                    txt, col = 'Paired', C_GREEN
                else:
                    txt, col = 'Not paired', C_YELLOW
            else:  # mqtt
                self.fb.draw_text(RIGHT_X + 6, r1y + 6, 'Home Assistant', C_GRAY, auto_swap=False)
                if not running:
                    txt, col = 'Service stopped', C_GRAY
                elif mq_conn:
                    txt, col = 'Connected', C_GREEN
                else:
                    txt, col = 'Connecting...', C_AMBER
            self.fb.draw_text(RIGHT_X + 6, r1y + 26, txt, col, auto_swap=False)

            # Row 2 — PC Power
            self.fb.draw_rect(RIGHT_X, r2y, RIGHT_W, ROW_H - 1, C_DARK_GRAY, auto_swap=False)
            self.fb.draw_text(RIGHT_X + 6, r2y + 6, 'PC Power', C_GRAY, auto_swap=False)
            if running:
                txt, col = ('ON', C_GREEN) if power else ('OFF', C_RED)
            else:
                txt, col = 'Unknown', C_GRAY
            self.fb.draw_text(RIGHT_X + 6, r2y + 26, txt, col, auto_swap=False)

            # Row 3 — Start/Stop button
            self._draw_svc_button(RIGHT_X, r3y, RIGHT_W, ROW_H, running)

    def _draw_svc_button(self, x, y, w, h, running: bool, pressed: bool = False):
        color = C_RED if running else C_GREEN
        label = 'STOP SERVICE' if running else 'START SERVICE'
        if pressed:
            color = tuple(min(255, c + 60) for c in color)
        self._button(x, y, w, h, label, color, pressed)

    def _draw_control_pin(self, mode: str, state: dict, running: bool):
        self.fb.draw_rect(0, PIN_ZONE_Y, SCREEN_W, SCREEN_H - PIN_ZONE_Y,
                          C_DARK_GRAY, auto_swap=False)
        hk     = state.get('homekit', {})
        mq     = state.get('mqtt', {})
        paired = hk.get('paired', False)
        mq_ok  = mq.get('connected', False)

        if not running:
            msg, col = 'Start the service to enable smart home control', C_GRAY
        elif mode == 'both':
            if paired and mq_ok:
                msg, col = 'HomeKit paired  |  Home Assistant connected', C_GREEN
            elif paired:
                msg, col = 'HomeKit paired  —  waiting for MQTT...', C_YELLOW
            elif mq_ok:
                msg, col = 'HA connected  —  open Home app to pair HomeKit', C_YELLOW
            else:
                msg, col = 'Waiting for HomeKit pairing and MQTT connection', C_GRAY
        elif mode == 'homekit':
            if paired:
                msg, col = 'Paired with HomeKit  —  power switch ready', C_GREEN
            else:
                msg, col = 'Home app  >  Add Accessory  >  More options  >  enter PIN', C_GRAY
        else:  # mqtt
            if mq_ok:
                msg, col = 'Connected to Home Assistant  —  power switch ready', C_GREEN
            else:
                msg, col = 'Connecting to MQTT broker...', C_GRAY

        mw, _ = self.fb.get_text_size(msg)
        self.fb.draw_text(max(4, (SCREEN_W - mw) // 2),
                          PIN_ZONE_Y + 24, msg, col, auto_swap=False)

    # ── control hit tests ─────────────────────────────────────────────────────

    def is_svc_button(self, x, y, mode: str) -> bool:
        if mode == 'both':
            # Button occupies bottom 64px of right panel
            btn_y = CONTENT_Y + 40 + 40
            return RIGHT_X <= x <= SCREEN_W and btn_y <= y <= TOUCH_H
        else:
            btn_y = CONTENT_Y + 48 + 48
            return RIGHT_X <= x <= SCREEN_W and btn_y <= y <= TOUCH_H


# ── mode runners ──────────────────────────────────────────────────────────────

def run_install_mode(fb: Framebuffer) -> bool:
    ui   = UI(fb)
    lock = threading.Lock()
    done    = [False]
    success = [False]

    def do_install():
        success[0] = install_packages(ui.draw_install_progress)
        done[0] = True
        lock.release()

    ui.draw_install()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while not done[0]:
                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            return False
                        if ui.is_install_btn(sx, sy) and lock.acquire(blocking=False):
                            ui.draw_install_progress(0, 'Starting...')
                            threading.Thread(target=do_install, daemon=True).start()
                kev = keys.read_event(timeout=0.0)
                if kev and kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                    return False
    except KeyboardInterrupt:
        pass

    if not success[0]:
        ui.draw_install_progress(100, 'Failed!')
        time.sleep(2)
    return success[0]


def run_mode_select(fb: Framebuffer) -> str | None:
    """Show HomeKit / Home Assistant / Both buttons. Returns chosen mode or None."""
    ui = UI(fb)
    ui.draw_mode_select()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            return None
                        mode = ui.mode_btn_hit(sx, sy)
                        if mode:
                            return mode
                kev = keys.read_event(timeout=0.0)
                if kev and kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                    return None
    except KeyboardInterrupt:
        pass
    return None


def run_mqtt_config_mode(fb: Framebuffer) -> bool:
    """Show web config URL and wait for the user to submit the broker form."""
    from config_server import ConfigServer

    ui     = UI(fb)
    ip     = get_local_ip()
    server = ConfigServer()
    server.start()
    ui.draw_mqtt_config(ip, done=False)
    drawn_done = False
    done_at: float | None = None

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                if server.is_done and not drawn_done:
                    ui.update_mqtt_config_done()
                    drawn_done = True
                    done_at = time.time()

                # Auto-advance 2 seconds after form submission — no tap needed
                if done_at is not None and time.time() - done_at >= 2.0:
                    server.stop()
                    return True

                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            server.stop()
                            return False
                kev = keys.read_event(timeout=0.0)
                if kev and kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                    server.stop()
                    return False

                time.sleep(0.02)
    except KeyboardInterrupt:
        server.stop()
    return False


def run_register_mode(fb: Framebuffer) -> bool:
    """Register + auto-start the service. Returns True on success."""
    ui   = UI(fb)
    lock = threading.Lock()
    done    = [False]
    success = [False]

    def do_register():
        if register_service():
            success[0] = service_start()
        done[0] = True
        lock.release()

    ui.draw_register()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while not done[0]:
                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            return False
                        if ui.is_register_btn(sx, sy) and lock.acquire(blocking=False):
                            ui.draw_register_working('Registering...')
                            threading.Thread(target=do_register, daemon=True).start()
                kev = keys.read_event(timeout=0.0)
                if kev and kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                    return False
    except KeyboardInterrupt:
        pass

    if not success[0]:
        ui.draw_register_error('Failed!')
        time.sleep(2)
    return success[0]


def run_pairing_mode(fb: Framebuffer, mode: str) -> bool:
    """Show the one-time pairing/info screen. Returns True when user taps OK."""
    ui    = UI(fb)
    state = wait_for_state(timeout=10.0)
    ui.draw_pairing(mode, state)

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            return False
                        if ui.is_pair_ok(sx, sy):
                            return True
                kev = keys.read_event(timeout=0.0)
                if kev:
                    if kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                        return False
                    if kev[0] == 'key_release' and kev[1] == 'ENTER' and not kev[4]:
                        return True
    except KeyboardInterrupt:
        pass
    return False


def run_control_mode(fb: Framebuffer, mode: str):
    """Main control screen — runs until the user exits."""
    ui      = UI(fb)
    state   = read_state()
    running = service_active()
    ui.draw_control(mode, state, running)

    last_refresh = time.time()
    REFRESH      = 2.0
    lock         = threading.Lock()

    def toggle(currently_running):
        nonlocal state, running
        if currently_running:
            service_stop()
        else:
            service_start()
        time.sleep(0.5)
        state   = read_state()
        running = service_active()
        ui.update_control(mode, state, running)
        lock.release()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                now = time.time()
                if now - last_refresh >= REFRESH:
                    ns = read_state()
                    nr = service_active()
                    if ns != state or nr != running:
                        state, running = ns, nr
                        ui.draw_control(mode, state, running)
                    last_refresh = now

                ev = touch.read_event(timeout=0.02)
                if ev:
                    kind, rx, ry, _ = ev
                    sx, sy = TouchScreen.map_coords_270(rx, ry)
                    if kind == 'touch_down':
                        if ui.is_back(sx, sy):
                            break
                        if ui.is_svc_button(sx, sy, mode) and lock.acquire(blocking=False):
                            ui._draw_svc_button(RIGHT_X,
                                                CONTENT_Y + (40 + 40 if mode == 'both' else 96),
                                                RIGHT_W,
                                                CONTENT_H - (80 if mode == 'both' else 96),
                                                running, pressed=True)
                            fb.swap_buffer()
                            threading.Thread(target=toggle, args=(running,), daemon=True).start()

                kev = keys.read_event(timeout=0.0)
                if kev:
                    if kev[0] == 'key_long_press' and kev[1] in ('ESC', 'ENTER'):
                        break
                    if kev[0] == 'key_release' and kev[1] == 'ENTER' and not kev[4]:
                        if lock.acquire(blocking=False):
                            threading.Thread(target=toggle, args=(running,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        fb.fill_screen(C_BLACK)


# ── helpers for needs_mqtt_config ─────────────────────────────────────────────

def needs_mqtt_config(config: dict) -> bool:
    """True if mode includes MQTT but no broker address is saved yet."""
    mode = config.get('mode', '')
    if mode not in ('mqtt', 'both'):
        return False
    mqtt = config.get('mqtt', {})
    return not mqtt.get('broker', '').strip()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    fb = Framebuffer(
        '/dev/fb0',
        rotation=270,
        font_size=14,
        font_path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    )

    while True:
        config = load_config()
        mode   = config.get('mode', '')

        if not packages_installed():
            if not run_install_mode(fb):
                break

        elif not mode:
            # First run — pick mode
            chosen = run_mode_select(fb)
            if not chosen:
                break
            config['mode'] = chosen
            save_config(config)

        elif needs_mqtt_config(config):
            # MQTT mode but no broker yet — show web config screen
            if not run_mqtt_config_mode(fb):
                break

        elif not service_registered():
            # Register service and auto-start it
            if not run_register_mode(fb):
                break
            # Show one-time pairing screen
            run_pairing_mode(fb, config.get('mode', 'homekit'))

        else:
            run_control_mode(fb, config.get('mode', 'homekit'))
            break


if __name__ == '__main__':
    main()
