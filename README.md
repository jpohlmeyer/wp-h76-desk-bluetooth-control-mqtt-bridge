# WP-H76 Desk Bluetooth ↔ MQTT Bridge

A small Python service that bridges a BLE-controlled height-adjustable desk with WP-H76 handset to [MQTT](https://mqtt.org/).
It lets you read the current desk height and move the desk (up / down / stop / set height) from Home Assistant or any MQTT client.

## Features

* Publish current desk height (cm) to MQTT
* Subscribe to MQTT control commands: `up`, `down`, `stop`, `set <cm>`

## Prerequisites

* Linux host with Bluetooth (BlueZ) – e.g. a Raspberry Pi
* Python 3.10+
* A BLE-capable desk with WP-H76 or compatible handset

## Installation

```bash
# Clone the repo
git clone https://github.com/jpohlmeyer/wp-h76-desk-bluetooth-control-mqtt-bridge.git
cd desk-bluetooth-control-mqtt-bridge

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Many settings can be overridden with environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_BROKER` | `mqtt.local` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_BASE_TOPIC` | `desk_control` | Base topic prefix |
| `MQTT_CONTROL_TOPIC_SUFFIX` | `desk_control` | Control command topic suffix |
| `MQTT_HEIGHT_TOPIC_SUFFIX` | `current_desk_height` | Height publish topic suffix |
| `DESK_RATE_LIMIT_SECONDS` | `0.2` | Min seconds between height publishes |
| `DESK_LOWER_BOUND_CM` | `65` | Minimum allowed height (cm) |
| `DESK_UPPER_BOUND_CM` | `130` | Maximum allowed height (cm) |

Example:

```bash
export MQTT_BROKER=mqtt.local
export MQTT_BASE_TOPIC=home/desk
python desk-control.py AA:BB:CC:DD:EE:FF
```

## MQTT topics

All topics are prefixed with `MQTT_BASE_TOPIC/<bluetooth-address>/`.

| Topic | Direction | Payload | Description |
|---|---|---|---|
| `.../desk_control` | ← subscribe | `up` / `down` / `stop` / `set 90` | Move the desk |
| `.../current_desk_height` | → publish | `82.0` | Latest height in centimetres |

## Usage

Find your desk's Bluetooth MAC address first:

```bash
python test.py scan
```

Then run the bridge:

```bash
python desk-control.py AA:BB:CC:DD:EE:FF
```

Send commands with any MQTT client, e.g. [`mosquitto_pub`](https://mosquitto.org/man/mosquitto_pub-1.html):

```bash
# Move up
mosquitto_pub -h mqtt.local -t 'desk_control/AA:BB:CC:DD:EE:FF/desk_control' -m 'up'

# Move to 90 cm
mosquitto_pub -h mqtt.local -t 'desk_control/AA:BB:CC:DD:EE:FF/desk_control' -m 'set 90'

# Stop
mosquitto_pub -h mqtt.local -t 'desk_control/AA:BB:CC:DD:EE:FF/desk_control' -m 'stop'

# Watch height updates
mosquitto_sub -h mqtt.local -t 'desk_control/AA:BB:CC:DD:EE:FF/current_desk_height'
```

## Debugging with `test.py`

`test.py` talks to the desk directly over BLE (no MQTT) and is handy for verifying packet formats:

```bash
python test.py scan                                    # discover nearby devices
python test.py set AA:BB:CC:DD:EE:FF 90                # move to 90 cm
python test.py up AA:BB:CC:DD:EE:FF                    # move up
python test.py down AA:BB:CC:DD:EE:FF                  # move down
python test.py stop AA:BB:CC:DD:EE:FF                  # stop
python test.py read-height AA:BB:CC:DD:EE:FF           # stream height for 10 s
```

## Protocol

The GATT characteristic UUIDs, control packet layout, and checksum algorithm
are documented in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Running as a systemd service (Raspberry Pi example)

Create `/etc/systemd/system/desk-bridge.service`:

```ini
[Unit]
Description=WP-H76 Desk Bluetooth MQTT Bridge
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/wp-h76-desk-bluetooth-control-mqtt-bridge
ExecStart=/home/pi/wp-h76-desk-bluetooth-control-mqtt-bridge/venv/bin/python desk-control.py AA:BB:CC:DD:EE:FF
Environment=MQTT_BROKER=mqtt.lan
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now desk-bridge
```

## Project structure

```
desk-control.py          # Production bridge service (BLE + MQTT)
test.py                  # Debug CLI for direct BLE interaction
requirements.txt         # Python dependencies
docs/PROTOCOL.md         # Reverse-engineered BLE protocol documentation
LICENSE                  # MIT
```

## License

MIT – see [`LICENSE`](LICENSE).
