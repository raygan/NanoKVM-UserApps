#!/usr/bin/env python3
"""
HomeKit Switch UI for NanoKVM.

Display layout (320x240 logical, rotation=270):
  Touchable area: 320x172 (y=0..171)
  Non-touch zone: 320x68  (y=172..239) — used for PIN display

  ┌──────────────────────────────────────────┐
  │ [<]   Desktop PC HomeKit        PWR: ON  │  y=0..27
  ├────────────────────┬─────────────────────┤
  │                    │ HomeKit: Not Paired │  y=28..75
  │   QR code          ├─────────────────────┤
  │   (or Paired ✓)    │ PC Power: ON        │  y=76..123
  │                    ├─────────────────────┤
  │                    │  [ START SERVICE ]  │  y=124..171
  ├────────────────────┴─────────────────────┤
  │  Setup PIN: 879-54-321   (scan QR above) │  y=172..239
  └──────────────────────────────────────────┘
"""

import os
import json
import time
import shutil
import subprocess

from framebuffer import Framebuffer
from input import TouchScreen, GpioKeys

# ── colours ────────────────────────────────────────────────────────────────
C_BLACK      = (0,   0,   0)
C_WHITE      = (255, 255, 255)
C_GRAY       = (128, 128, 128)
C_DARK_GRAY  = (48,  48,  48)
C_MED_GRAY   = (80,  80,  80)
C_GREEN      = (80,  213, 83)
C_RED        = (213, 80,  80)
C_BLUE       = (80,  160, 213)
C_YELLOW     = (213, 200, 80)
C_DIVIDER    = (60,  60,  60)

# ── layout constants ────────────────────────────────────────────────────────
SCREEN_W = 320
SCREEN_H = 240
TOUCH_H  = 172

HEADER_H  = 28
CONTENT_Y = HEADER_H
CONTENT_H = TOUCH_H - HEADER_H   # 144px

DIVIDER_X = 155
RIGHT_X   = DIVIDER_X + 1
RIGHT_W   = SCREEN_W - RIGHT_X   # 164px

ROW1_Y = CONTENT_Y           # y=28..75
ROW2_Y = CONTENT_Y + 48      # y=76..123
ROW3_Y = CONTENT_Y + 96      # y=124..171
ROW_H  = 48

PIN_ZONE_Y = TOUCH_H          # y=172..239

STATE_FILE   = '/tmp/homekit_state.json'
SERVICE_NAME = 'homekit-switch'
SERVICE_DST  = f'/etc/systemd/system/{SERVICE_NAME}.service'
APP_DIR      = os.path.dirname(os.path.abspath(__file__))
SERVICE_SRC  = os.path.join(APP_DIR, f'{SERVICE_NAME}.service')

BACK_BTN_X = 4
BACK_BTN_Y = 4
BACK_BTN_W = 32
BACK_BTN_H = 20


# ── service helpers ─────────────────────────────────────────────────────────
def service_is_registered() -> bool:
    return os.path.exists(SERVICE_DST)


def register_service() -> bool:
    try:
        shutil.copy(SERVICE_SRC, SERVICE_DST)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=10)
        return True
    except Exception as e:
        print(f"register_service failed: {e}")
        return False


def service_is_active() -> bool:
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', SERVICE_NAME],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False


def service_start() -> bool:
    try:
        subprocess.run(['systemctl', 'enable', '--now', SERVICE_NAME],
                       check=True, timeout=10)
        return True
    except Exception as e:
        print(f"start failed: {e}")
        return False


def service_stop() -> bool:
    try:
        subprocess.run(['systemctl', 'disable', '--now', SERVICE_NAME],
                       check=True, timeout=10)
        return True
    except Exception as e:
        print(f"stop failed: {e}")
        return False


# ── state file helpers ──────────────────────────────────────────────────────
def read_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}



# ── UI ───────────────────────────────────────────────────────────────────────
class HomeKitUI:
    def __init__(self, fb: Framebuffer):
        self.fb = fb

    # ── setup screen ────────────────────────────────────────────────────────
    def draw_setup_ui(self):
        self.fb.fill_screen(C_BLACK)
        self._draw_header()
        self._draw_exit_button()

        # Info card (left side)
        self.fb.draw_rect(5, CONTENT_Y + 5, DIVIDER_X - 10, CONTENT_H - 10,
                          C_DARK_GRAY, auto_swap=False)
        lines = ["Service not", "registered.", "", "Tap Register", "to set up."]
        for i, line in enumerate(lines):
            self.fb.draw_text(14, CONTENT_Y + 14 + i * 22, line, C_GRAY, auto_swap=False)

        # Register button (right side, centred vertically)
        self._draw_action_button("REGISTER", color=C_BLUE, pressed=False)

        self.fb.swap_buffer()

    def draw_setup_working(self, message: str):
        self._draw_action_button(message, color=C_GRAY, pressed=True)
        self.fb.swap_buffer()

    def draw_setup_error(self, message: str):
        self._draw_action_button(message, color=C_RED, pressed=False)
        self.fb.swap_buffer()

    # ── control screen ───────────────────────────────────────────────────────
    def draw_control_ui(self, state: dict, running: bool):
        self.fb.fill_screen(C_BLACK)
        self._draw_header()
        self._draw_exit_button()
        self._draw_divider()
        self._draw_left_panel(state, running)
        self._draw_right_panel(state, running)
        self._draw_pin_zone(state, running)
        self.fb.swap_buffer()

    def update_control_status(self, state: dict, running: bool):
        """Redraw right panel and PIN zone without touching the QR/left panel."""
        self._draw_right_panel(state, running)
        self._draw_pin_zone(state, running)
        self.fb.swap_buffer()

    # ── shared drawing ───────────────────────────────────────────────────────
    def _draw_header(self):
        self.fb.draw_rect(0, 0, SCREEN_W, HEADER_H, C_MED_GRAY, auto_swap=False)
        title = "NanoKVM  HomeKit"
        tw, _ = self.fb.get_text_size(title)
        self.fb.draw_text((SCREEN_W - tw) // 2, 6, title, C_WHITE, auto_swap=False)

    def _draw_exit_button(self, pressed: bool = False):
        bg = (100, 40, 40) if pressed else C_DARK_GRAY
        self.fb.draw_rect(BACK_BTN_X, BACK_BTN_Y, BACK_BTN_W, BACK_BTN_H,
                          C_GRAY, auto_swap=False)
        self.fb.draw_rect(BACK_BTN_X + 1, BACK_BTN_Y + 1, BACK_BTN_W - 2, BACK_BTN_H - 2,
                          bg, auto_swap=False)
        self.fb.draw_text(BACK_BTN_X + 9, BACK_BTN_Y + 2, '<', C_WHITE, auto_swap=False)

    def _draw_divider(self):
        self.fb.draw_rect(DIVIDER_X, CONTENT_Y, 1, CONTENT_H, C_DIVIDER, auto_swap=False)

    def _draw_action_button(self, label: str, color: tuple, pressed: bool):
        """Large button on the right half of the content area."""
        pad = 8
        bx = RIGHT_X + pad
        by = CONTENT_Y + (CONTENT_H - 52) // 2
        bw = RIGHT_W - pad * 2
        bh = 52
        border = tuple(min(255, c + 40) for c in color)
        fill   = tuple(max(0, c - 40) for c in color) if pressed else color
        self.fb.draw_rect(bx, by, bw, bh, border, auto_swap=False)
        self.fb.draw_rect(bx + 2, by + 2, bw - 4, bh - 4, fill, auto_swap=False)
        lw, lh = self.fb.get_text_size(label)
        self.fb.draw_text(bx + (bw - lw) // 2, by + (bh - lh) // 2,
                          label, C_WHITE, auto_swap=False)

    def _draw_left_panel(self, state: dict, running: bool):
        area_x, area_y = 0, CONTENT_Y
        area_w, area_h = DIVIDER_X, CONTENT_H
        paired     = state.get('paired', False)
        setup_code = state.get('setup_code', '')

        if paired:
            self.fb.draw_rect(area_x, area_y, area_w, area_h, C_DARK_GRAY, auto_swap=False)
            self.fb.draw_rect(area_x + area_w//2 - 22, area_y + 20, 44, 44,
                              C_GREEN, auto_swap=False)
            ow, _ = self.fb.get_text_size("OK")
            self.fb.draw_text(area_x + area_w//2 - ow//2, area_y + 34,
                              "OK", C_BLACK, auto_swap=False)
            lw, _ = self.fb.get_text_size("Paired")
            self.fb.draw_text(area_x + (area_w - lw)//2, area_y + 78,
                              "Paired", C_GREEN, auto_swap=False)
        elif running and setup_code:
            # Pairing instructions + PIN
            code = setup_code.replace('-', '')
            if len(code) == 8:
                setup_code = f"{code[:3]}-{code[3:5]}-{code[5:]}"
            self.fb.draw_rect(area_x, area_y, area_w, area_h, C_DARK_GRAY, auto_swap=False)
            lines = ["In Home app:", "Add Accessory", "More Options,", "then enter:"]
            for i, line in enumerate(lines):
                self.fb.draw_text(area_x + 6, area_y + 8 + i * 22,
                                  line, C_GRAY, auto_swap=False)
            # PIN prominently at the bottom of the panel
            pw, _ = self.fb.get_text_size(setup_code)
            self.fb.draw_text(area_x + (area_w - pw) // 2, area_y + area_h - 26,
                              setup_code, C_WHITE, auto_swap=False)
        else:
            self.fb.draw_rect(area_x, area_y, area_w, area_h, C_DARK_GRAY, auto_swap=False)
            for i, line in enumerate(["Start service", "to show QR"]):
                lw, _ = self.fb.get_text_size(line)
                self.fb.draw_text(area_x + (area_w - lw) // 2,
                                  area_y + area_h // 2 - 16 + i * 22,
                                  line, C_GRAY, auto_swap=False)

    def _draw_right_panel(self, state: dict, running: bool):
        paired   = state.get('paired', False)
        power_on = state.get('power_on', False)

        # Row 1 — HomeKit pairing status
        self.fb.draw_rect(RIGHT_X, ROW1_Y, RIGHT_W, ROW_H - 1, C_DARK_GRAY, auto_swap=False)
        self.fb.draw_text(RIGHT_X + 6, ROW1_Y + 6, "HomeKit", C_GRAY, auto_swap=False)
        if not running:
            txt, col = "Service stopped", C_GRAY
        elif paired:
            txt, col = "Paired", C_GREEN
        else:
            txt, col = "Not paired", C_YELLOW
        self.fb.draw_text(RIGHT_X + 6, ROW1_Y + 26, txt, col, auto_swap=False)

        # Row 2 — PC power status
        self.fb.draw_rect(RIGHT_X, ROW2_Y, RIGHT_W, ROW_H - 1, C_DARK_GRAY, auto_swap=False)
        self.fb.draw_text(RIGHT_X + 6, ROW2_Y + 6, "PC Power", C_GRAY, auto_swap=False)
        if running:
            txt, col = ("ON", C_GREEN) if power_on else ("OFF", C_RED)
        else:
            txt, col = "Unknown", C_GRAY
        self.fb.draw_text(RIGHT_X + 6, ROW2_Y + 26, txt, col, auto_swap=False)

        # Row 3 — start/stop button
        self._draw_service_button(running)

    def _draw_service_button(self, running: bool, pressed: bool = False):
        if running:
            color, label = C_RED,   "STOP SERVICE"
        else:
            color, label = C_GREEN, "START SERVICE"
        if pressed:
            color = tuple(min(255, c + 60) for c in color)
        border = tuple(min(255, c + 30) for c in color)
        fill   = tuple(max(0, c - 30) for c in color)
        self.fb.draw_rect(RIGHT_X,     ROW3_Y,     RIGHT_W,     ROW_H,     border, auto_swap=False)
        self.fb.draw_rect(RIGHT_X + 2, ROW3_Y + 2, RIGHT_W - 4, ROW_H - 4, fill,   auto_swap=False)
        lw, lh = self.fb.get_text_size(label)
        self.fb.draw_text(RIGHT_X + (RIGHT_W - lw) // 2, ROW3_Y + (ROW_H - lh) // 2,
                          label, C_WHITE, auto_swap=False)

    def _draw_pin_zone(self, state: dict, running: bool):
        self.fb.draw_rect(0, PIN_ZONE_Y, SCREEN_W, SCREEN_H - PIN_ZONE_Y,
                          C_DARK_GRAY, auto_swap=False)
        paired = state.get('paired', False)

        if paired:
            msg, col = "Paired with HomeKit  —  power switch ready", C_GREEN
        elif running:
            msg, col = "Home app  >  Add Accessory  >  More options  >  enter PIN", C_GRAY
        else:
            msg, col = "Start the service to begin HomeKit pairing", C_GRAY

        mw, _ = self.fb.get_text_size(msg)
        self.fb.draw_text(max(4, (SCREEN_W - mw) // 2), PIN_ZONE_Y + 24,
                          msg, col, auto_swap=False)

    # ── hit testing ──────────────────────────────────────────────────────────
    def is_exit_button(self, x, y) -> bool:
        return (BACK_BTN_X <= x <= BACK_BTN_X + BACK_BTN_W and
                BACK_BTN_Y <= y <= BACK_BTN_Y + BACK_BTN_H)

    def is_service_button(self, x, y) -> bool:
        return RIGHT_X <= x <= SCREEN_W and ROW3_Y <= y <= ROW3_Y + ROW_H

    def is_register_button(self, x, y) -> bool:
        pad = 8
        bx = RIGHT_X + pad
        by = CONTENT_Y + (CONTENT_H - 52) // 2
        bw = RIGHT_W - pad * 2
        bh = 52
        return bx <= x <= bx + bw and by <= y <= by + bh


# ── mode runners ─────────────────────────────────────────────────────────────
def run_setup_mode(fb: Framebuffer) -> bool:
    """Show the first-run registration screen. Returns True when registered."""
    ui = HomeKitUI(fb)
    ui.draw_setup_ui()

    import threading
    lock = threading.Lock()
    done = [False]
    success = [False]

    def do_register():
        ok = register_service()
        success[0] = ok
        done[0] = True
        lock.release()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                if done[0]:
                    break

                touch_event = touch.read_event(timeout=0.02)
                if touch_event:
                    ev, raw_x, raw_y, _ = touch_event
                    sx, sy = TouchScreen.map_coords_270(raw_x, raw_y)

                    if ev == 'touch_down':
                        if ui.is_exit_button(sx, sy):
                            return False
                        if ui.is_register_button(sx, sy):
                            if lock.acquire(blocking=False):
                                ui.draw_setup_working("Registering...")
                                t = threading.Thread(target=do_register, daemon=True)
                                t.start()

                key_event = keys.read_event(timeout=0.0)
                if key_event:
                    ev, key_name, _, _, is_long = key_event
                    if ev == 'key_long_press' and key_name in ('ESC', 'ENTER'):
                        return False

    except KeyboardInterrupt:
        pass

    if not success[0]:
        ui.draw_setup_error("Failed!")
        time.sleep(2)

    return success[0]


def run_control_mode(fb: Framebuffer):
    ui      = HomeKitUI(fb)
    state   = read_state()
    running = service_is_active()
    ui.draw_control_ui(state, running)

    last_refresh = time.time()
    REFRESH_INTERVAL = 2.0

    import threading
    lock = threading.Lock()

    def toggle_in_thread(currently_running):
        nonlocal state, running
        if currently_running:
            ok = service_stop()
        else:
            ok = service_start()
        time.sleep(0.5)
        state   = read_state()
        running = service_is_active()
        ui.update_control_status(state, running)
        lock.release()

    try:
        with TouchScreen() as touch, GpioKeys() as keys:
            while True:
                now = time.time()
                if now - last_refresh >= REFRESH_INTERVAL:
                    new_state   = read_state()
                    new_running = service_is_active()
                    if new_state != state or new_running != running:
                        state   = new_state
                        running = new_running
                        ui.draw_control_ui(state, running)
                    last_refresh = now

                touch_event = touch.read_event(timeout=0.02)
                if touch_event:
                    ev, raw_x, raw_y, _ = touch_event
                    sx, sy = TouchScreen.map_coords_270(raw_x, raw_y)

                    if ev == 'touch_down':
                        if ui.is_exit_button(sx, sy):
                            break
                        if ui.is_service_button(sx, sy):
                            if lock.acquire(blocking=False):
                                ui._draw_service_button(running, pressed=True)
                                fb.swap_buffer()
                                t = threading.Thread(
                                    target=toggle_in_thread, args=(running,), daemon=True)
                                t.start()

                key_event = keys.read_event(timeout=0.0)
                if key_event:
                    ev, key_name, _, _, is_long = key_event
                    if ev == 'key_long_press' and key_name in ('ESC', 'ENTER'):
                        break
                    if ev == 'key_release' and key_name == 'ENTER' and not is_long:
                        if lock.acquire(blocking=False):
                            t = threading.Thread(
                                target=toggle_in_thread, args=(running,), daemon=True)
                            t.start()

    except KeyboardInterrupt:
        pass
    finally:
        fb.fill_screen(C_BLACK)


# ── entry point ──────────────────────────────────────────────────────────────
def main():
    fb = Framebuffer(
        '/dev/fb0',
        rotation=270,
        font_size=14,
        font_path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    )

    while True:
        if service_is_registered():
            run_control_mode(fb)
            break
        else:
            if not run_setup_mode(fb):
                break   # user cancelled


if __name__ == '__main__':
    main()
