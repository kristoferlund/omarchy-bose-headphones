#!/usr/bin/env python3
"""Read-only sweep: send BMAP GET to every (block, function) and record replies as JSON."""

import json
import os
import socket
import sys
import time

ADDR = os.environ["BOSE_ADDR"]  # headset Bluetooth address
CHANNEL = 8
OPERATORS = {0: "SET", 1: "GET", 2: "SETGET", 3: "STATUS", 4: "ERROR",
             5: "START", 6: "RESULT", 7: "PROCESSING"}


class Bmap:
    def __init__(self, addr=ADDR, channel=CHANNEL):
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.s.connect((addr, channel))
        self.buf = b""

    def packets(self, wait):
        """Yield complete packets arriving within `wait` seconds of the last one."""
        deadline = time.monotonic() + wait
        while True:
            while len(self.buf) >= 4 and len(self.buf) >= 4 + self.buf[3]:
                n = 4 + self.buf[3]
                pkt, self.buf = self.buf[:n], self.buf[n:]
                deadline = time.monotonic() + wait
                yield pkt
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.s.settimeout(remaining)
            try:
                chunk = self.s.recv(4096)
            except TimeoutError:
                return
            if not chunk:
                return
            self.buf += chunk

    def request(self, block, func, op=1, payload=b"", wait=0.4):
        self.s.send(bytes([block, func, op, len(payload)]) + payload)
        return list(self.packets(wait))


def describe(pkt):
    return {"block": pkt[0], "func": pkt[1], "op": OPERATORS.get(pkt[2] & 0x0F, pkt[2]),
            "opbyte": pkt[2], "payload": pkt[4:].hex(),
            "ascii": pkt[4:].decode("ascii", "replace") if pkt[4:].isascii() else None}


if __name__ == "__main__":
    max_func = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    bm = Bmap()
    results = {}
    for block in range(32):
        for func in range(max_func):
            replies = [describe(p) for p in bm.request(block, func)]
            ok = [r for r in replies if r["op"] != "ERROR"]
            if ok:
                results[f"{block}.{func}"] = replies
                for r in replies:
                    print(f"{r['block']:>2}.{r['func']:<2} {r['op']:<10} {r['payload']} {r['ascii'] or ''}")
            elif block not in {int(k.split('.')[0]) for k in results} and func == 0 and replies:
                print(f"{block:>2}.{func:<2} {replies[0]['op']} {replies[0]['payload']}")
    json.dump(results, open("sweep.json", "w"), indent=1)
