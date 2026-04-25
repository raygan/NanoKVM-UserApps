# SmartHome Switch

Control your desktop PC's power from **Apple HomeKit**, **Home Assistant (via MQTT)**, or both — using the NanoKVM's ATX header connection.

## Features

- Exposes the desktop PC as a smart switch (on/off)
- Reads live power state from the ATX power LED header
- Supports HomeKit, Home Assistant (MQTT auto-discovery), or both simultaneously
- First-run wizard guides you through mode selection and setup
- MQTT broker credentials entered via browser — no typing on the device
- Service persists across reboots when enabled

## Requirements

- NanoKVM Pro connected to the desktop PC's ATX power and LED headers
- For HomeKit: `avahi-daemon` running (already present on NanoKVM)
- For MQTT/HA: an MQTT broker accessible on your network (the Mosquitto add-on for Home Assistant is recommended)
- Internet access on first launch (to download Python packages)

## First Run

The app walks you through setup automatically:

1. **Install packages** — tap **Install** to download `HAP-python`, `Pillow`, and `paho-mqtt` via pip. Happens once.
2. **Mode selection** — choose **Apple HomeKit**, **Home Assistant**, or **Both**.
3. **MQTT config** *(MQTT/Both only)* — the screen displays a URL (e.g. `http://192.168.1.x:8080`). Open it on your phone or laptop, enter your broker details, and tap **Save**. Then tap **OK** on the NanoKVM screen.
4. **Register service** — tap **Register** to install the systemd service. Happens once.
5. **Pairing screen** — follow the on-screen instructions to pair with HomeKit and/or check HA for auto-discovery.

After setup, the app launches directly to the control screen.

## HomeKit Pairing

1. Tap **Start Service** (if not already running)
2. On your iPhone, open the **Home** app
3. Tap **+** → **Add Accessory** → **More Options**
4. Select **NanoKVM** and enter the setup PIN shown on screen

## Home Assistant (MQTT)

1. In Home Assistant, install the **Mosquitto broker** add-on (one click under Add-ons)
2. Enable the **MQTT integration** (HA detects Mosquitto automatically)
3. Start the SmartHome Switch service
4. **NanoKVM Power** appears automatically under **Settings → Devices & Services → MQTT**

No manual YAML configuration is needed — the device uses MQTT Discovery.

## MQTT Configuration

Broker credentials are stored at `/userapp/smarthome-switch/config.json`. To reconfigure, open `http://<nanokvm-ip>:8080` in your browser while the main.py app is on the MQTT config screen — or edit the file directly and restart the service.

Default topics:
| Topic | Purpose |
|-------|---------|
| `homeassistant/switch/nanokvm_power/state` | Power state (`ON`/`OFF`) |
| `homeassistant/switch/nanokvm_power/set` | Command topic |
| `homeassistant/switch/nanokvm_power/availability` | `online`/`offline` |
| `homeassistant/switch/nanokvm_power/config` | Discovery payload |

## GPIO Pins Used

| Function     | GPIO |
|--------------|------|
| Power status | 75   |
| Power button | 7    |

## Service Management

Via touchscreen:
- **Start Service** — enables and starts the daemon (survives reboot)
- **Stop Service** — stops and disables the daemon
- **Exit** (`<`) — returns to the app launcher

Via SSH:
```bash
systemctl start smarthome-switch
systemctl stop smarthome-switch
journalctl -u smarthome-switch -f
```

## Uninstallation

```bash
systemctl disable --now smarthome-switch
rm /etc/systemd/system/smarthome-switch.service
systemctl daemon-reload
rm -rf /userapp/smarthome-switch/
```

## Notes

- The HomeKit accessory name is **NanoKVM**. Rename it in the Home app after pairing.
- HomeKit pairing state is in `/userapp/smarthome-switch/accessory.state`. Delete it to unpair all clients.
- The MQTT device publishes a Last Will Testament so Home Assistant shows it as unavailable if the service stops unexpectedly.
