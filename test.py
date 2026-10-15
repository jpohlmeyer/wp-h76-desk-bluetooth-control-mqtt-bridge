"""
test.py

Debug helper for interacting with the desk directly over BLE (no MQTT).

Useful for verifying packet formats and bounds while developing.
This is *not* part of the production service, see desk-control.py for that.

Examples::

    # Scan for nearby BLE devices and print their names/addresses
    python test.py scan

    # Move desk to 90 cm (address required)
    python test.py set AA:BB:CC:DD:EE:FF 90

    # Move up / down / stop
    python test.py up AA:BB:CC:DD:EE:FF
    python test.py down AA:BB:CC:DD:EE:FF
    python test.py stop AA:BB:CC:DD:EE:FF

    # Subscribe to height notifications for 10 seconds
    python test.py read-height AA:BB:CC:DD:EE:FF
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from bleak import BleakClient, BleakScanner

NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CONTROL_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

LOWER_BOUND_CM = 65
UPPER_BOUND_CM = 130

PACKET_UP = bytes.fromhex("a50020dfff00ff")
PACKET_DOWN = bytes.fromhex("a50040bfff00ff")
PACKET_STOP = bytes.fromhex("a50000ffff00ff")


def parse_height_from_ascii(data: bytes) -> Optional[float]:
    """Parse desk height from a notification payload (ASCII mm, e.g. b'0820')."""
    try:
        mm = int(data.decode("ascii"))
        return mm / 10.0
    except Exception:
        return None


def build_height_packet_from_template(height_cm: int) -> bytes:
    """Build the 9-byte 'move to exact height' command packet."""
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
        [0xA6, 0xA8, 0x01, height_msb, height_lsb, 0x00, 0x00, checksum, 0xFF]
    )


async def scan() -> None:
    """Scan for nearby BLE devices and print their address and name."""
    print("Scanning for BLE devices...")
    devices = await BleakScanner.discover(timeout=5.0)
    for device in devices:
        print(f"  {device.address}  {(device.name or 'unknown')!r}")


async def set_height(client: BleakClient, cm: float) -> None:
    packet = build_height_packet_from_template(int(cm))
    await client.write_gatt_char(CONTROL_UUID, packet, response=True)
    print(f"Set height to {cm} cm (packet: {packet.hex()})")


async def stop_desk(client: BleakClient) -> None:
    await client.write_gatt_char(CONTROL_UUID, PACKET_STOP, response=True)
    print("Stop command sent")


async def up_desk(client: BleakClient) -> None:
    await client.write_gatt_char(CONTROL_UUID, PACKET_UP, response=True)
    print("Up command sent")


async def down_desk(client: BleakClient) -> None:
    await client.write_gatt_char(CONTROL_UUID, PACKET_DOWN, response=True)
    print("Down command sent")


async def read_height(client: BleakClient, duration: float = 10.0) -> None:
    """Subscribe to height notifications and print them for ``duration`` seconds."""

    def handler(sender: int, data: bytes) -> None:
        height = parse_height_from_ascii(data)
        print(f"Desk height: {height} cm" if height is not None else f"raw: {data.hex()}")

    await client.start_notify(NOTIFY_UUID, handler)
    print(f"Reading height for {duration:.0f}s … (Ctrl+C to stop early)")
    await asyncio.sleep(duration)


async def run_with_client(address: str, coro) -> None:
    async with BleakClient(address) as client:
        print(f"Connected to {address}")
        await coro(client)


def usage() -> None:
    print(__doc__)
    sys.exit(1)


async def async_main() -> None:
    if len(sys.argv) < 2:
        usage()

    command = sys.argv[1].lower()

    if command == "scan":
        await scan()
        return

    if len(sys.argv) < 3:
        print(f"Error: '{command}' requires a Bluetooth address", file=sys.stderr)
        usage()

    address = sys.argv[2]

    if command == "set":
        if len(sys.argv) < 4:
            print("Error: 'set' requires a height in cm", file=sys.stderr)
            usage()
        height = float(sys.argv[3])
        await run_with_client(address, lambda client: set_height(client, height))

    elif command == "up":
        await run_with_client(address, up_desk)

    elif command == "down":
        await run_with_client(address, down_desk)

    elif command == "stop":
        await run_with_client(address, stop_desk)

    elif command == "read-height":
        duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        await run_with_client(address, lambda client: read_height(client, duration))

    else:
        print(f"Error: unknown command '{command}'", file=sys.stderr)
        usage()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
