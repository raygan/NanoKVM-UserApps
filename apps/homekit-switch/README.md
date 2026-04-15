# HomeKit Switch

Control your desktop PC's power from Apple HomeKit using the NanoKVM's ATX header connection.

## Features

- Exposes the desktop PC as a HomeKit switch (on/off)
- Shows live power status from the ATX power LED header
- Pairing code displayed on screen — no SSH required
- Start/stop the HomeKit service from the touchscreen
- Service persists across reboots when enabled

## Requirements

- NanoKVM Pro connected to the desktop PC's ATX power and LED headers
- Python packages: `HAP-python`, `Pillow` (install with `pip3 install HAP-python Pillow`)
- `avahi-daemon` running (already present on NanoKVM)

## First Run

On first launch the app will show a **Register Service** button. Tap it once to install the systemd service. After that, tap **Start Service** to begin advertising to HomeKit.

## Pairing with HomeKit

1. Tap **Start Service** in the app
2. On your iPhone, open the **Home** app
3. Tap **+** → **Add Accessory** → **More Options**
4. Select **NanoKVM** from the list
5. Enter the setup PIN shown on the NanoKVM screen

The switch will appear in your home and reflect the PC's real power state. Toggling it sends a short press to the power button (same as tapping the physical button).

## Service Management

Via touchscreen:
- **Start Service**: Enables and starts the HomeKit daemon (survives reboot)
- **Stop Service**: Stops and disables the daemon
- **Exit button** (`<`): Returns to the app launcher

## GPIO Pins Used

| Function       | GPIO |
|----------------|------|
| Power status   | 75   |
| Power button   | 7    |

These are the same pins used by the PWR-BTN app.

## Uninstallation

```bash
systemctl disable --now homekit-switch
rm /etc/systemd/system/homekit-switch.service
systemctl daemon-reload
rm /userapp/homekit-switch/accessory.state
```

## Notes

- The accessory name visible in HomeKit is **NanoKVM**. You can rename it in the Home app after pairing.
- Pairing state is stored in `/userapp/homekit-switch/accessory.state`. Delete this file to unpair all clients and start fresh.
- Logs are available via `journalctl -u homekit-switch -f`.
