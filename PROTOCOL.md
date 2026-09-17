# BMAP notes

Notes on BMAP, the control protocol of Bose headphones, as used by `bmap.py`. Everything below
was observed on a QuietComfort Headphones (2023) headset (product 0x4075, firmware
1.0.6-80+f5f219b) with the probing scripts in `research/`.

## Transport

- BMAP runs over an RFCOMM channel advertised under the Serial Port UUID 0x1101 ("SPP Dev",
  channel 8 on this headset). Find it with an SDP search on the L2CAP UUID 0x0100; a search on the
  public browse group returns nothing.
- Another RFCOMM service (UUID `00000000-deca-fade-deca-deafdecacaff`, channel 14) answers with
  `ff55` frames and is not BMAP.
- Only one RFCOMM connection at a time; a second connect gets EBUSY, and reconnecting right after
  closing can briefly get EBUSY too (retry). Hence `bosectl daemon`.

## Packets

`[function block, function, operator, length, payload...]`

- Operator (low nibble of byte 2): 0 SET, 1 GET, 2 SETGET, 3 STATUS, 4 ERROR, 5 START, 6 RESULT,
  7 PROCESSING.
- ERROR payload is one code: 1 length, 3 function block not supported, 4 function not supported,
  5 operator not supported, 6 invalid data / index out of range, 7 data unavailable, 9 timeout,
  12 busy, 20 insecure transport.
- 0.2 returns the supported function blocks as a 32-bit big-endian bitmask (bit n = block n).
  This headset: `86cc03ff`.

Function blocks: 0 product info, 1 settings, 2 status, 3 firmware update, 4 device management,
5 audio management, 7 control, 9 notification, 18 authentication, 31 audio modes.

## Block 0: product info

0.1 BMAP version (`1.1.0`), 0.3 product id and variant (`40 75 01`), 0.5 firmware version,
0.6 Bluetooth address, 0.7 serial number.

## Block 1: settings

Writes are SETGET and answer with STATUS in the GET layout.

| fn | setting | GET layout | SETGET payload |
|---|---|---|---|
| 2 | name | `[flags, utf8…]` | `utf8…` |
| 3 | voice prompts | `[b5 enabled, b0–4 language, …][u32 supported languages]` | `[enabled<<5 \| language]` |
| 4 | auto-off minutes | `[minutes]`, or `[lo, ?, hi]` when 3+ bytes | `[minutes]`, or `[lo, hi]` above 255 |
| 5 | noise cancelling | `[steps, level, flags]` (`0b 0a 03`) | `[level, enabled]` (rejected on this headset) |
| 7 | EQ | repeated `[min, max, value, band]`, signed; band 0 bass, 1 mid, 2 treble | `[value, band]` |
| 9 | shortcut button | `[button, event, action, u32 supported, u32 unavailable]` | `[button, event, action]` |
| 10 | multipoint | `b0 enabled, b1 supported, b2 can disable` | `[0/1]` |
| 11 | self voice (sidetone) | `[persistent, mode, supported bits]`; 0 off, 1 high, 2 medium, 3 low | `[persistent, mode]` |
| 14 | noise cancelling persistence | `[0/1]` | `[0/1]` |

## Block 2: status

2.2 battery: 4-byte records `[percent, minutes u16 (0xFFFF unknown), component]`.

## Block 5: audio management

5.5 volume: `[max, current]`; SETGET `[level]`.

## Block 9: notification

9.2 SETGET `[1, function block bitmask]` subscribes to pushed updates. `01 80040237` (blocks 0, 1,
2, 4, 5, 9, 18, 31) answers `00 80040237`; afterwards the headset pushes STATUS packets, e.g.
`1f 04 03 01 02` when the mode changes.

## Block 31: audio modes (profiles)

| fn | name | ops |
|---|---|---|
| 2 | capabilities | GET → `[bose modes, user modes, …, flags]`; this headset `02 02 00 00 00 09` |
| 3 | current mode | GET → `[index]`; START `[index, play prompt 0/1]` switches (replies PROCESSING only) |
| 4 | default mode | GET, SETGET `[index]` |
| 5 | persistence | GET, SETGET `[0/1]` |
| 6 | mode config | GET `[index]` → record below; SETGET writes a mode |
| 7 | user indices | GET → mode indices |
| 8 | favorites | GET → `[count, bitmask]` |
| 9 | reset | START `[index]` deletes a custom mode |

### Mode config record (45, 47 or 48 bytes)

| offset | field |
|---|---|
| 0 | mode index |
| 1–2 | icon / voice prompt id (`00 00` empty slot, `00 01` Quiet, `00 02` Aware, `00 08` Outdoor, `00 0b` Work, …) |
| 3 | custom (user configurable) |
| 4 | user configured |
| 5 | favorite |
| 6–37 | name, 32 bytes UTF-8, NUL-terminated |
| 41 | editable bits: 0 noise cancelling, 1 ActiveSense, 2 spatial audio, 3 wind block, 4 ANC toggle |
| 42 | noise cancelling level: 0 = full cancelling … steps−1 = fully aware |
| 43 | ActiveSense (auto noise cancelling) |
| 44 | spatial audio: 0 off, 1 still (fixed to room), 2 motion (fixed to head) |
| 46 | wind block (records of 47+ bytes) |
| 47 | ANC toggle (records of 48 bytes) |

`bosectl` shows noise cancelling as `steps − 1 − level`, so 0 is aware and 10 is full cancelling.

### Writing a mode

SETGET 31.6 with `[index, icon1, icon2, name(32), level, ActiveSense, spatial, wind block, ANC toggle]`,
where spatial and wind block are sent when the record has them and ANC toggle only for 48-byte
records.

- Create: write into an empty slot (icon `00 00`). New modes start at the middle level.
- Delete: START 31.9 `[index]`; the slot becomes icon `00 00` with an empty name, other bytes kept.
  Deleting Outdoor and recreating it gave a byte-identical record.

## Behaviour on this headset

- Writes verified with read-back and restored: 31.3, 31.4, 31.5, 31.6 (level, wind block, name,
  icon), 31.9 + recreate, 1.2, 1.3, 1.4, 1.7, 1.9, 1.10, 1.11, 1.14, 5.5.
- 1.5 SETGET → error 5: noise cancelling is only adjustable through the active mode's config.
- 1.3 SETGET sends no reply when the value changes (only when unchanged); verify with GET.
- With 31.5 persistence on, the headset powers on in the last used mode and 31.4 mirrors the active
  mode (31.4 is pushed on every switch). With persistence off, 31.4 is a fixed default.
- 31.11 (supported mode names) answers error 4.
- Turning multipoint off then on made another previously paired device reconnect.
- 2 Bose modes and 2 custom slots.
