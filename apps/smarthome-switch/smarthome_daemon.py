#!/usr/bin/env python3
"""
SmartHome Switch daemon for NanoKVM.
Runs as a systemd service. Exposes the desktop PC power as a HomeKit
Switch and/or an MQTT device with Home Assistant auto-discovery,
depending on the mode set in config.json.
"""

import json
import logging
import os
import signal
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger(__name__)

# ── paths ─────────────────────────────────────────────────────────────────────

CONFIG_FILE  = '/userapp/smarthome-switch/config.json'
STATE_FILE   = '/tmp/smarthome_state.json'
PERSIST_FILE = '/userapp/smarthome-switch/accessory.state'
HAP_PORT     = 51826

# ── GPIO ──────────────────────────────────────────────────────────────────────

GPIO_POWER_STATUS = '/sys/class/gpio/gpio75/value'
GPIO_POWER_BUTTON = '/sys/class/gpio/gpio7/value'


def read_power_status() -> bool:
    """Returns True when the desktop PC is powered on (active-low LED)."""
    try:
        with open(GPIO_POWER_STATUS) as f:
            return f.read().strip() == '0'
    except Exception:
        return False


def pulse_power_button(duration: float = 0.2):
    """Short-press the ATX power button."""
    try:
        with open(GPIO_POWER_BUTTON, 'w') as f:
            f.write('1')
        time.sleep(duration)
        with open(GPIO_POWER_BUTTON, 'w') as f:
            f.write('0')
        logger.info('Power button pulsed')
    except Exception as e:
        logger.error('Failed to pulse power button: %s', e)


# ── shared state file ─────────────────────────────────────────────────────────

_state: dict = {}
_state_lock = threading.Lock()


def _flush():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(_state, f)
        os.chmod(STATE_FILE, 0o644)
    except Exception as e:
        logger.error('State write failed: %s', e)


def write_state(data: dict):
    global _state
    with _state_lock:
        _state = data
    _flush()


def patch_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)
    _flush()


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ── HomeKit helpers ───────────────────────────────────────────────────────────

def _setup_uri(pin: str, category: int = 8, flags: int = 4) -> str:
    code = int(pin.replace('-', ''))
    payload = code | (flags << 27) | (category << 31)
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    result, n = '', payload
    while n:
        result = chars[n % 36] + result
        n //= 36
    return 'X-HM://' + result.zfill(9)


# ── MQTT ──────────────────────────────────────────────────────────────────────

_DISCOVERY_PREFIX  = 'homeassistant'
_DEVICE_ID         = 'nanokvm_power'
MQTT_STATE_TOPIC   = f'{_DISCOVERY_PREFIX}/switch/{_DEVICE_ID}/state'
MQTT_CMD_TOPIC     = f'{_DISCOVERY_PREFIX}/switch/{_DEVICE_ID}/set'
MQTT_AVAIL_TOPIC   = f'{_DISCOVERY_PREFIX}/switch/{_DEVICE_ID}/availability'
MQTT_CONFIG_TOPIC  = f'{_DISCOVERY_PREFIX}/switch/{_DEVICE_ID}/config'

_DISCOVERY_PAYLOAD = {
    'name':                 'NanoKVM Power',
    'unique_id':            'nanokvm_power_switch',
    'state_topic':          MQTT_STATE_TOPIC,
    'command_topic':        MQTT_CMD_TOPIC,
    'availability_topic':   MQTT_AVAIL_TOPIC,
    'payload_on':           'ON',
    'payload_off':          'OFF',
    'state_on':             'ON',
    'state_off':            'OFF',
    'payload_available':    'online',
    'payload_not_available':'offline',
    'device': {
        'identifiers':  ['nanokvm_power'],
        'name':         'NanoKVM',
        'model':        'NanoKVM Pro',
        'manufacturer': 'Sipeed',
    },
}


class MQTTHandler:
    """Wraps a paho-mqtt client, running its network loop in a daemon thread."""

    def __init__(self, mqtt_cfg: dict):
        import paho.mqtt.client as mqtt

        self.broker   = mqtt_cfg.get('broker', 'homeassistant.local')
        self.port     = int(mqtt_cfg.get('port', 1883))
        self.username = mqtt_cfg.get('username', '')
        self.password = mqtt_cfg.get('password', '')

        self._connected = threading.Event()
        self._client = mqtt.Client(client_id='nanokvm_smarthome', clean_session=True)

        if self.username:
            self._client.username_pw_set(self.username, self.password or None)

        # Last-will: mark offline if we disconnect ungracefully
        self._client.will_set(MQTT_AVAIL_TOPIC, 'offline', retain=True)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info('MQTT connected to %s:%d', self.broker, self.port)
            self._connected.set()
            client.subscribe(MQTT_CMD_TOPIC)
            client.publish(MQTT_CONFIG_TOPIC, json.dumps(_DISCOVERY_PAYLOAD), retain=True)
            client.publish(MQTT_AVAIL_TOPIC, 'online', retain=True)
            client.publish(MQTT_STATE_TOPIC,
                           'ON' if read_power_status() else 'OFF', retain=True)
            patch_state(mqtt={'enabled': True, 'connected': True, 'broker': self.broker})
        else:
            logger.error('MQTT connection refused (rc=%d)', rc)
            patch_state(mqtt={'enabled': True, 'connected': False, 'broker': self.broker})

    def _on_disconnect(self, client, userdata, rc):
        logger.warning('MQTT disconnected (rc=%d)', rc)
        self._connected.clear()
        patch_state(mqtt={'enabled': True, 'connected': False, 'broker': self.broker})

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode().strip()
        logger.info('MQTT command: %s', payload)
        current = read_power_status()
        if payload == 'ON' and not current:
            pulse_power_button()
        elif payload == 'OFF' and current:
            pulse_power_button()

    # ── public API ────────────────────────────────────────────────────────────

    def start(self):
        try:
            self._client.connect_async(self.broker, self.port, keepalive=60)
        except Exception as e:
            logger.error('MQTT connect_async failed: %s', e)
            patch_state(mqtt={'enabled': True, 'connected': False, 'broker': self.broker})
        self._client.loop_start()

    def publish_state(self, power_on: bool):
        if self._connected.is_set():
            self._client.publish(MQTT_STATE_TOPIC,
                                 'ON' if power_on else 'OFF', retain=True)

    def stop(self):
        try:
            self._client.publish(MQTT_AVAIL_TOPIC, 'offline', retain=True)
            time.sleep(0.3)
            self._client.disconnect()
        except Exception:
            pass
        self._client.loop_stop()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()


# ── HomeKit accessory ─────────────────────────────────────────────────────────

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_SWITCH


class DesktopPowerSwitch(Accessory):
    category = CATEGORY_SWITCH

    def __init__(self, driver, display_name, mqtt: MQTTHandler | None = None):
        super().__init__(driver, display_name)
        self._mqtt = mqtt
        svc = self.add_preload_service('Switch')
        self.char_on = svc.configure_char('On', setter_callback=self._set_switch)

    def _set_switch(self, value: bool):
        current = read_power_status()
        if bool(value) != current:
            logger.info('HomeKit toggling power: %s', 'on' if value else 'off')
            pulse_power_button()

    @Accessory.run_at_interval(2)
    async def run(self):
        status = read_power_status()
        self.char_on.set_value(status)
        if self._mqtt:
            self._mqtt.publish_state(status)
        _write_homekit_state(self.driver, status, self._mqtt)

    def stop(self):
        super().stop()


def _write_homekit_state(driver, power_on: bool, mqtt: MQTTHandler | None):
    data: dict = {'power_on': power_on}

    try:
        pincode    = driver.state.pincode
        setup_code = pincode.decode() if isinstance(pincode, bytes) else str(pincode)
        paired     = len(driver.state.paired_clients) > 0
        data['homekit'] = {
            'enabled':    True,
            'paired':     paired,
            'setup_code': setup_code,
            'setup_uri':  _setup_uri(setup_code),
        }
    except Exception as e:
        logger.error('HomeKit state read failed: %s', e)
        data['homekit'] = {
            'enabled': True, 'paired': False,
            'setup_code': '000-00-000', 'setup_uri': '',
        }

    if mqtt:
        data['mqtt'] = {
            'enabled':   True,
            'connected': mqtt.connected,
            'broker':    mqtt.broker,
        }

    write_state(data)


# ── MQTT-only polling loop ────────────────────────────────────────────────────

def _run_mqtt_only(mqtt: MQTTHandler):
    write_state({
        'power_on': read_power_status(),
        'mqtt': {'enabled': True, 'connected': False, 'broker': mqtt.broker},
    })
    last_status = None
    while True:
        status = read_power_status()
        if status != last_status:
            mqtt.publish_state(status)
            last_status = status
        with _state_lock:
            _state['power_on'] = status
            _state['mqtt'] = {
                'enabled':   True,
                'connected': mqtt.connected,
                'broker':    mqtt.broker,
            }
        _flush()
        time.sleep(2)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    config      = load_config()
    mode        = config.get('mode', 'homekit')
    use_homekit = mode in ('homekit', 'both')
    use_mqtt    = mode in ('mqtt', 'both')

    os.makedirs(os.path.dirname(PERSIST_FILE), exist_ok=True)

    mqtt = None
    if use_mqtt:
        mqtt = MQTTHandler(config.get('mqtt', {}))
        patch_state(mqtt={'enabled': True, 'connected': False, 'broker': mqtt.broker})
        mqtt.start()

    if use_homekit:
        driver = AccessoryDriver(port=HAP_PORT, persist_file=PERSIST_FILE)
        acc    = DesktopPowerSwitch(driver, 'NanoKVM', mqtt=mqtt)
        driver.add_accessory(accessory=acc)
        _write_homekit_state(driver, read_power_status(), mqtt)

        def _stop(signum, frame):
            if mqtt:
                mqtt.stop()
            driver.stop()

        signal.signal(signal.SIGTERM, _stop)
        logger.info('Starting SmartHome daemon (HomeKit, MQTT=%s)', use_mqtt)
        driver.start()

    else:
        # MQTT-only — run blocking poll loop in main thread
        def _stop(signum, frame):
            mqtt.stop()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _stop)
        logger.info('Starting SmartHome daemon (MQTT only, broker=%s)', mqtt.broker)
        _run_mqtt_only(mqtt)


if __name__ == '__main__':
    main()
