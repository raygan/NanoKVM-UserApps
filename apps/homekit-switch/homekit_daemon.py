#!/usr/bin/env python3
"""
HomeKit accessory daemon for NanoKVM desktop power switch.
Runs as a systemd service. Exposes the desktop PC as a HomeKit Switch.
"""

import os
import json
import time
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

STATE_FILE = '/tmp/homekit_state.json'
PERSIST_FILE = '/userapp/homekit-switch/accessory.state'
PORT = 51826

GPIO_POWER_STATUS = "/sys/class/gpio/gpio75/value"
GPIO_POWER_BUTTON = "/sys/class/gpio/gpio7/value"


def compute_setup_uri(pin: str, category: int = 8, flags: int = 4) -> str:
    """Compute HomeKit X-HM:// pairing URI from setup PIN.

    category=8 is Switch. flags=4 means supports IP.
    """
    code = int(pin.replace("-", ""))
    payload = code | (flags << 27) | (category << 31)
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = ""
    n = payload
    while n:
        result = chars[n % 36] + result
        n //= 36
    return "X-HM://" + result.zfill(9)


def read_power_status() -> bool:
    """Returns True if desktop PC is powered on."""
    try:
        with open(GPIO_POWER_STATUS, 'r') as f:
            return f.read().strip() == '0'
    except Exception:
        return False


def pulse_power_button(duration: float = 0.2):
    """Briefly press the power button (short press = soft power toggle)."""
    try:
        with open(GPIO_POWER_BUTTON, 'w') as f:
            f.write('1')
        time.sleep(duration)
        with open(GPIO_POWER_BUTTON, 'w') as f:
            f.write('0')
        logger.info("Power button pulsed")
    except Exception as e:
        logger.error(f"Failed to pulse power button: {e}")


def write_state(data: dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f)
        os.chmod(STATE_FILE, 0o644)
    except Exception as e:
        logger.error(f"Failed to write state file: {e}")


from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_SWITCH


class DesktopPowerSwitch(Accessory):
    category = CATEGORY_SWITCH

    def __init__(self, driver, display_name):
        super().__init__(driver, display_name)
        svc = self.add_preload_service('Switch')
        self.char_on = svc.configure_char('On', setter_callback=self._set_switch)

    def _set_switch(self, value: bool):
        """Called by HomeKit when the switch is toggled."""
        current = read_power_status()
        if bool(value) != current:
            logger.info(f"HomeKit toggling power: {'on' if value else 'off'} (was {'on' if current else 'off'})")
            pulse_power_button()
        else:
            logger.info(f"Power already {'on' if value else 'off'}, ignoring")

    @Accessory.run_at_interval(2)
    async def run(self):
        """Poll GPIO and sync state to HomeKit and the state file."""
        status = read_power_status()
        self.char_on.set_value(status)
        _write_current_state(self.driver, status)

    def stop(self):
        super().stop()


def _write_current_state(driver, power_on: bool):
    try:
        pincode = driver.state.pincode
        # pincode is bytes in HAP-python 5.x (e.g. b'870-57-893')
        setup_code = pincode.decode() if isinstance(pincode, bytes) else str(pincode)
        paired = len(driver.state.paired_clients) > 0
    except Exception as e:
        logger.error(f"Failed to read state: {e}")
        setup_code = "000-00-000"
        paired = False

    write_state({
        'power_on': power_on,
        'paired': paired,
        'setup_code': setup_code,
        'setup_uri': compute_setup_uri(setup_code),
    })


def main():
    os.makedirs(os.path.dirname(PERSIST_FILE), exist_ok=True)

    driver = AccessoryDriver(port=PORT, persist_file=PERSIST_FILE)
    acc = DesktopPowerSwitch(driver, 'NanoKVM')
    driver.add_accessory(accessory=acc)

    # Write initial state so the UI has something to display immediately
    _write_current_state(driver, read_power_status())

    try:
        pincode = driver.state.pincode
        setup_code = pincode.decode() if isinstance(pincode, bytes) else str(pincode)
        logger.info(f"Setup code: {setup_code}")
        logger.info(f"Setup URI:  {compute_setup_uri(setup_code)}")
    except Exception:
        pass

    signal.signal(signal.SIGTERM, driver.stop)
    logger.info(f"Starting HomeKit accessory on port {PORT}")
    driver.start()


if __name__ == '__main__':
    main()
