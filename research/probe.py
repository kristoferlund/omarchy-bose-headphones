#!/usr/bin/env python3
"""Read-only probe: list SDP services on a Bose headset, then send BMAP GET requests."""

import socket
import struct
import sys

ADDR = sys.argv[1]  # headset Bluetooth address
L2CAP_UUID = 0x0100  # every record uses L2CAP; the browse group returns nothing on this headset
RFCOMM_UUID = 0x0003


# ---------- SDP ----------

def uuid16(u):
    return bytes([0x19]) + struct.pack(">H", u)


def des(payload):
    return bytes([0x35, len(payload)]) + payload


def parse_element(buf, i):
    hdr = buf[i]
    typ, size_idx = hdr >> 3, hdr & 7
    i += 1
    if size_idx < 5:
        n = [1, 2, 4, 8, 16][size_idx] if typ != 0 else 0
    else:
        width = [1, 2, 4][size_idx - 5]
        n = int.from_bytes(buf[i:i + width], "big")
        i += width
    data = buf[i:i + n]
    i += n
    if typ in (1, 2):
        val = int.from_bytes(data, "big", signed=(typ == 2))
    elif typ == 3:
        val = ("uuid", int.from_bytes(data, "big") if n <= 4 else data.hex())
    elif typ in (4, 8):
        val = data.rstrip(b"\0").decode(errors="replace")
    elif typ == 5:
        val = bool(data[0])
    elif typ in (6, 7):
        val, j = [], 0
        while j < len(data):
            v, j = parse_element(data, j)
            val.append(v)
    else:
        val = None
    return val, i


def sdp_records(addr):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.settimeout(5)
    s.connect((addr, 1))
    pattern = des(uuid16(L2CAP_UUID))
    attrs = des(bytes([0x0A]) + struct.pack(">I", 0x0000FFFF))
    cont, raw, tid = b"\x00", b"", 1
    while True:
        params = pattern + struct.pack(">H", 0xFFFF) + attrs + cont
        s.send(struct.pack(">BHH", 0x06, tid, len(params)) + params)
        resp = s.recv(4096)
        if resp[0] != 0x07:
            raise RuntimeError(f"SDP error response: {resp.hex()}")
        count = struct.unpack(">H", resp[5:7])[0]
        raw += resp[7:7 + count]
        cont = resp[7 + count:]
        tid += 1
        if cont[0] == 0:
            break
    s.close()
    lists, _ = parse_element(raw, 0)
    return [dict(zip(r[0::2], r[1::2])) for r in lists]


def rfcomm_channel(record):
    for proto in record.get(0x0004, []) or []:
        if proto and proto[0] == ("uuid", RFCOMM_UUID) and len(proto) > 1:
            return proto[1]
    return None


# ---------- BMAP ----------

OPERATORS = {0: "SET", 1: "GET", 2: "SETGET", 3: "STATUS", 4: "ERROR",
             5: "START", 6: "RESULT", 7: "PROCESSING"}

# (function block, function, label) — product-info block only, all GETs
PROBES = [(0, 1, "BMAP version"), (0, 2, "all function blocks"),
          (0, 3, "product id/variant"), (0, 5, "firmware version")]


def bmap_probe(addr, channel):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(3)
    s.connect((addr, channel))
    for block, func, label in PROBES:
        s.send(bytes([block, func, 0x01, 0x00]))
        try:
            resp = s.recv(1024)
        except TimeoutError:
            print(f"    {block}.{func} {label}: no reply")
            continue
        b, f, op, n = resp[:4]
        payload = resp[4:4 + n]
        printable = payload.decode("ascii", errors="replace") if payload.isascii() else ""
        print(f"    {block}.{func} {label}: op={OPERATORS.get(op & 0x0F, op)} "
              f"payload={payload.hex()} {printable!r}  (raw {resp.hex()})")
    s.close()


if __name__ == "__main__":
    print(f"SDP services on {ADDR}:")
    candidates = []
    for rec in sdp_records(ADDR):
        name = rec.get(0x0100, "?")
        classes = rec.get(0x0001, [])
        ch = rfcomm_channel(rec)
        print(f"  {name!r:40} rfcomm={ch} classes={classes}")
        uuids = [c[1] for c in classes if isinstance(c, tuple)]
        if ch and 0x1101 in uuids and (name, ch) not in candidates:
            candidates.append((name, ch))

    for name, ch in candidates:
        print(f"\nBMAP probe on {name!r} (RFCOMM channel {ch}):")
        try:
            bmap_probe(ADDR, ch)
        except OSError as e:
            print(f"    failed: {e}")
