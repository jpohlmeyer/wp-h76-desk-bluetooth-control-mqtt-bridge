"""
desk-control.py

Bluetooth-to-MQTT bridge for a height-adjustable smart desk.

Exposes the desk over MQTT so it can be controlled from Home Assistant
or any other MQTT client:

* ``desk_control/<address>/desk_control`` (subscribe) control commands:
    ``up``, ``down``, ``stop``, ``set <height_cm>``
* ``desk_control/<address>/current_desk_height`` (publish) latest desk
  height in centimetres (rate limited).

All configuration can be overridden through environment variables, see
``CONFIGURATION`` below.

Requirements
------------
* Python 3.10+
* bleak
* paho-mqtt

Running
-------
    python desk-control.py AA:BB:CC:DD:EE:FF

See README.md for a full setup guide.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Optional

import paho.mqtt.client as mqtt
from bleak import BleakClient

# ---------------------------------------------------------------------------
# Bluetooth GATT characteristics (reverse-engineered from the desk)
# ---------------------------------------------------------------------------
NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CONTROL_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

# Raw control packets (7 bytes) for instant up/down/stop commands.
PACKET_UP = bytes.fromhex("a50020dfff00ff")
PACKET_DOWN = bytes.fromhex("a50040bfff00ff")
PACKET_STOP = bytes.fromhex("a50000ffff00ff")

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt.local")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "desk_control")
CONTROL_TOPIC_SUFFIX = os.getenv("MQTT_CONTROL_TOPIC_SUFFIX", "desk_control")
HEIGHT_TOPIC_SUFFIX = os.getenv("MQTT_HEIGHT_TOPIC_SUFFIX", "current_desk_height")

RATE_LIMIT_SECONDS = float(os.getenv("DESK_RATE_LIMIT_SECONDS", "0.2"))
LOWER_BOUND_CM = int(os.getenv("DESK_LOWER_BOUND_CM", "65"))
UPPER_BOUND_CM = int(os.getenv("DESK_UPPER_BOUND_CM", "130"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DeskBridgeService:
    """Bridge between MQTT commands and a BLE-controlled desk."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.is_connected = False
        self.stop_event = asyncio.Event()

        self.client: Optional[BleakClient] = None
        self.mqtt_client: Optional[mqtt.Client] = None
        self.command_queue: Optional[asyncio.Queue[str]] = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.last_publish_time = 0.0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        """Signal the service to stop gracefully (wakes all blocking tasks)."""
        logger.info("Stop signal received.")
        self.stop_event.set()

    def control_topic(self) -> str:
        """Full MQTT topic used to receive desk control commands."""
        return f"{BASE_TOPIC}/{self.address}/{CONTROL_TOPIC_SUFFIX}"

    def height_topic(self) -> str:
        """Full MQTT topic used to publish the current desk height."""
        return f"{BASE_TOPIC}/{self.address}/{HEIGHT_TOPIC_SUFFIX}"

    def setup_mqtt(self) -> None:
        """Initialize the MQTT client, connect to the broker, subscribe."""
        self.event_loop = asyncio.get_running_loop()
        self.command_queue = asyncio.Queue()

        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.mqtt_client.loop_start()
        logger.info("Connected to MQTT broker at %s:%s", MQTT_BROKER, MQTT_PORT)

    def on_mqtt_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        connect_flags: int,
        reason_code: int,
        properties: mqtt.Properties,
    ) -> None:
        """Callback when the MQTT client connects successfully."""
        if reason_code == 0:
            topic = self.control_topic()
            client.subscribe(topic)
            logger.info("Subscribed to %s", topic)
        else:
            logger.error("Failed to connect to MQTT, return code %s", reason_code)

    def on_mqtt_message(self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        """Callback when an MQTT message arrives queue it for async handling."""
        try:
            payload = msg.payload.decode("utf-8").strip()
            logger.info("Received command on %s: %s", msg.topic, payload)
            if self.event_loop and self.command_queue:
                asyncio.run_coroutine_threadsafe(
                    self.command_queue.put(payload), self.event_loop
                )
        except Exception as exc:
            logger.error("Error processing MQTT message: %s", exc)

    # ------------------------------------------------------------------ #
    # BLE / desk handling
    # ------------------------------------------------------------------ #
    def handle_disconnect(self, client: BleakClient) -> None:
        """Bleak disconnected callback: mark the desk as disconnected."""
        logger.warning("Bluetooth device disconnected.")
        self.is_connected = False

    def handle_notifications(self, sender: int, data: bytes) -> None:
        """Handle incoming desk height notifications with rate limiting."""
        current_time = time.time()
        if current_time - self.last_publish_time < RATE_LIMIT_SECONDS:
            return

        height = self.parse_height_from_ascii(data)
        if height is not None and LOWER_BOUND_CM <= height <= UPPER_BOUND_CM:
            topic = self.height_topic()
            if self.mqtt_client:
                self.mqtt_client.publish(topic, f"{height:.1f}")
            self.last_publish_time = current_time
            logger.info("Published desk height: %.1f cm to %s", height, topic)
        else:
            logger.warning("Received invalid height data: %s (=> %s)", data.hex(), height)

    @staticmethod
    def parse_height_from_ascii(data: bytes) -> Optional[float]:
        """
        Parse desk height from a notification payload.

        Payload format: ASCII digits, e.g. ``b'0820'`` meaning 820 mm.
        Returns the height in centimetres (float), or ``None`` on parse error.
        """
        try:
            mm = int(data.decode("ascii"))
            return mm / 10.0  # convert mm → cm
        except Exception:
            return None

    async def connect(self) -> None:
        """Connect to the desk over BLE and start height notifications."""
        logger.info("Connecting to desk %s ...", self.address)
        self.client = BleakClient(self.address, disconnected_callback=self.handle_disconnect)
        await self.client.connect()
        self.is_connected = True

        await self.client.start_notify(NOTIFY_UUID, self.handle_notifications)
        logger.info("Notifications started on %s", NOTIFY_UUID)

    async def disconnect(self) -> None:
        """Gracefully disconnect from the desk."""
        if self.client and self.is_connected:
            try:
                await self.client.disconnect()
            except Exception as exc:
                logger.warning("Error during BLE disconnect: %s", exc)
        self.is_connected = False
        self.client = None

    # ------------------------------------------------------------------ #
    # Command handling
    # ------------------------------------------------------------------ #
    async def process_mqtt_commands(self) -> None:
        """Consume queued MQTT commands and forward them to the desk."""
        while not self.stop_event.is_set():
            try:
                command = await asyncio.wait_for(
                    self.command_queue.get(), timeout=0.5  # type: ignore[union-attr]
                )
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle_desk_command(command)
            except Exception as exc:
                logger.error("Error processing command: %s", exc)

    async def handle_desk_command(self, command: str) -> None:
        """Parse and execute a single desk control command string."""
        parts = command.split()
        if not parts:
            return
        cmd = parts[0].lower()

        try:
            if cmd == "up":
                await self.up_desk()
                logger.info("Executed: up")
            elif cmd == "down":
                await self.down_desk()
                logger.info("Executed: down")
            elif cmd == "stop":
                await self.stop_desk()
                logger.info("Executed: stop")
            elif cmd == "set" and len(parts) > 1:
                try:
                    height = int(parts[1])
                    await self.set_desk_height(height)
                    logger.info("Executed: set to %s cm", height)
                except ValueError:
                    logger.error("Invalid height value: %s", parts[1])
            else:
                logger.warning("Unknown command: %s", command)
        except Exception as exc:
            logger.error("Error executing command '%s': %s", command, exc)

    # ------------------------------------------------------------------ #
    # BLE control packets
    # ------------------------------------------------------------------ #
    def build_height_packet_from_template(self, height_cm: int) -> bytes:
        """
        Build the 9-byte "move to exact height" command packet.

        The desk expects: A6 A8 01 <MSB> <LSB> 00 00 <checksum> FF where
        ``<MSB>/<LSB>`` is the target height in millimetres (big-endian) and
        the checksum is ``(MSB XOR LSB) + offset (mod 256)``. The offset is
        +1 when MSB is even, -1 when MSB is odd (reverse-engineered).

        Raises ValueError when the requested height is outside the safe bounds.
        """
        if not LOWER_BOUND_CM <= height_cm <= UPPER_BOUND_CM:
            raise ValueError(
                f"Height must be between {LOWER_BOUND_CM} and {UPPER_BOUND_CM} cm"
            )

        height_mm = height_cm * 10
        height_msb = (height_mm >> 8) & 0xFF
        height_lsb = height_mm & 0xFF

        offset = 1 if height_msb % 2 == 0 else -1
        checksum = ((height_msb ^ height_lsb) + offset) % 256

        return bytes(
            [
                0xA6,
                0xA8,
                0x01,
                height_msb,
                height_lsb,
                0x00,
                0x00,
                checksum,
                0xFF,
            ]
        )

    async def set_desk_height(self, cm: int) -> None:
        """Move the desk to an exact height in centimetres."""
        packet = self.build_height_packet_from_template(cm)
        await self.client.write_gatt_char(CONTROL_UUID, packet, response=False)

    async def stop_desk(self) -> None:
        """Stop any ongoing desk movement."""
        await self.client.write_gatt_char(CONTROL_UUID, PACKET_STOP, response=False)

    async def up_desk(self) -> None:
        """Start moving the desk up."""
        await self.client.write_gatt_char(CONTROL_UUID, PACKET_UP, response=False)

    async def down_desk(self) -> None:
        """Start moving the desk down."""
        await self.client.write_gatt_char(CONTROL_UUID, PACKET_DOWN, response=False)

    # ------------------------------------------------------------------ #
    # Main run loop
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Main loop: connect to MQTT, maintain BLE connection, process commands."""
        self.setup_mqtt()
        try:
            while not self.stop_event.is_set():
                if not self.is_connected:
                    try:
                        await self.connect()
                    except Exception as exc:
                        logger.warning("BLE connect failed: %s – retrying in 5s", exc)
                        try:
                            await asyncio.wait_for(self.stop_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                        continue
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("Service task cancelled.")
        finally:
            await self.disconnect()
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                logger.info("MQTT connection closed")


async def main() -> None:
    """Entry point: parse args, start the bridge, handle Ctrl+C gracefully."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Continuously monitor and control a desk over Bluetooth and MQTT."
    )
    parser.add_argument(
        "bt_address", help="Bluetooth MAC address of the desk (e.g. AA:BB:CC:DD:EE:FF)"
    )
    args = parser.parse_args()

    service = DeskBridgeService(args.bt_address)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.stop)

    run_task = asyncio.create_task(service.run())
    command_task = asyncio.create_task(service.process_mqtt_commands())
    try:
        await asyncio.gather(run_task, command_task)
    finally:
        service.stop()
        run_task.cancel()
        command_task.cancel()
        try:
            await run_task
            await command_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
