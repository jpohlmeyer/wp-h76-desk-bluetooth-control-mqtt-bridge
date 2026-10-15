# WP-H76 Desk Bluetooth Control Protocol

This document summarizes the reverse-engineered BLE GATT protocol used by the desk.

## Services and characteristics

| Service UUID | Characteristic UUID | Handle | Purpose |
|---|---|---|---|
| `0000ff00-…` | `0000ff01-…` | 0x0014 (20) | **Height notifications** – ASCII mm, e.g. `b'0820'` = 820 mm = 82.0 cm |
| `0000ff00-…` | `0000ff02-…` | 0x0017 (23) | **Control commands** – write without response |

## Control packets

### Instant commands (7 bytes, `0xA5` prefix)

Sent to `0000ff02-…` when pressing the physical up/down buttons.

| Packet (hex) | Action |
|---|---|
| `a5 00 20 df ff 00 ff` | Move up (start) |
| `a5 00 40 bf ff 00 ff` | Move down (start) |
| `a5 00 00 ff ff 00 ff` | Stop / neutral |

The third byte is the action code: `0x20` = up, `0x40` = down, `0x00` = stop.

### Move-to-height command (9 bytes, `0xA6` prefix)

Used for presets / explicit target heights.

```
a6 a8 01 <MSB> <LSB> 00 00 <checksum> ff
```

* `<MSB>/<LSB>` – target height in **millimetres**, big-endian 16-bit.
  * Example: `04 e2` = 0x04E2 = 1250 mm = 125.0 cm
  * Example: `03 34` = 0x0334 = 820 mm = 82.0 cm
* `checksum` (byte 7) = `(MSB XOR LSB) + offset (mod 256)`
  * `offset = +1` when MSB is even, `offset = -1` when MSB is odd

Height limits enforced in code (and the desk): **65–130 cm**.

## Height notifications

The desk pushes `b'NNNN'` (ASCII millimetres) on the notify characteristic.
The bridge rate-limits republishing to MQTT (default one publish per 0.2 s)
and validates the decoded value against the 65–130 cm bounds before
publishing.
