"""BMAP (Bose Messaging And Protocol) client over Bluetooth RFCOMM. Python stdlib only.

Packet: [function block, function, (device<<6 | port<<4 | operator), length, payload...].
Message layouts are documented in PROTOCOL.md.
"""

import errno
import socket
import struct
import time

SET, GET, SETGET, STATUS, ERROR, START, RESULT, PROCESSING = range(8)

PRODUCT_INFO, SETTINGS, STATUS_BLOCK, DEVICE_MGMT, AUDIO_MGMT, CONTROL, NOTIFICATION = 0, 1, 2, 4, 5, 7, 9
AUDIO_MODES = 31

ERRORS = {0: "unknown", 1: "length", 2: "checksum", 3: "function block not supported",
          4: "function not supported", 5: "operator not supported", 6: "invalid data",
          7: "data unavailable", 8: "runtime", 9: "timeout", 10: "invalid state",
          11: "device not found", 12: "busy", 13: "no connection (timeout)", 14: "no connection (key)",
          15: "OTA update in progress", 16: "OTA low battery", 17: "OTA no charger",
          18: "OTA update not allowed", 19: "unknown port", 20: "insecure transport",
          21: "invalid OTP key"}

PROMPTS = ["none", "quiet", "aware", "transparent", "transparency", "masking", "comfort",
           "commute", "outdoor", "workout", "home", "work", "music", "focus", "relax", "flight",
           "airport", "driving", "training", "gym", "run", "walk", "hike", "talk", "call",
           "whisper", "hearing", "learn", "podcast", "audiobook", "calm", "sleep", "meditate",
           "yoga", "immersion", "stereo", "cinema"]
SPATIAL = ["off", "still", "motion"]  # spatial audio off / fixed to room / fixed to head
# Mode types offered for custom modes, matching the choices in the Bose app
APP_ICONS = ["commute", "focus", "home", "music", "outdoor", "relax", "run", "walk", "work", "workout"]
SIDETONE = ["off", "high", "medium", "low"]
EQ_BANDS = ["bass", "mid", "treble", "low_mid", "high_mid"]
LANGUAGES = ["en-GB", "en-US", "fr", "it", "de", "es-ES", "es-MX", "pt-BR", "zh-CN", "ko", "ru",
             "pl", "he", "tr", "nl", "ja", "zh-HK", "ar", "sv", "da", "no", "fi", "hi"]
BUTTON_ACTIONS = ["not_configured", "voice_assistant", "anr", "battery_level", "play_pause",
                  "cnc_up", "cnc_down", "toggle_wake_word", "switch_source", "conversation_mode",
                  "track_forward", "track_back", "fetch_notifications", "wind_mode", "disabled",
                  "client_interaction", "spotify_tap", "modes_carousel", "volume_preset_carousel",
                  "spatial_audio_mode", "line_in_switch", "linking", "panic", "panic", "panic",
                  "third_party", "smart_action", "alexa"]
BUTTON_IDS = {0: "distal_cnc", 2: "voice_assistant", 3: "right_shortcut", 4: "left_shortcut",
              128: "shortcut"}

# Function blocks to receive pushed updates for
NOTIFY_BLOCKS = {0, 1, 2, 4, 5, 9, 16, 18, 31}


class BmapError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class ConnectionLost(BmapError):
    """The RFCOMM link to the headset is gone; reconnect before retrying."""


def need(payload, length, what):
    if len(payload) < length:
        raise BmapError(f"short reply for {what}: {len(payload)} of {length} bytes")
    return payload


def bits(value, width):
    return [i for i in range(width) if value >> i & 1]


def mac(b):
    return ":".join(f"{x:02X}" for x in b)


def mac_bytes(s):
    return bytes.fromhex(s.replace(":", ""))


def cstr(b):
    return b.split(b"\0")[0].decode(errors="replace")


# ---------------------------------------------------------------- transport

# Service discovery should be small; bound both memory and peer-driven round trips.
SDP_MAX_BYTES = 64 * 1024
SDP_MAX_RESPONSES = 32


def find_rfcomm_channel(addr, uuid16=0x1101):
    """SDP ServiceSearchAttribute for a 16-bit UUID; return its RFCOMM channel."""
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.settimeout(5)
    try:
        s.connect((addr, 1))
        pattern = bytes([0x35, 3, 0x19]) + struct.pack(">H", uuid16)
        attrs = bytes([0x35, 5, 0x0A]) + struct.pack(">I", 0x0000FFFF)
        raw, cont, seen = bytearray(), b"\0", set()
        for tid in range(1, SDP_MAX_RESPONSES + 1):
            params = pattern + struct.pack(">H", 0xFFFF) + attrs + cont
            s.send(struct.pack(">BHH", 0x06, tid, len(params)) + params)
            # One byte beyond the largest SDP PDU makes oversized packets fail
            # length validation even if SOCK_SEQPACKET truncates the datagram.
            resp = s.recv(5 + 0xFFFF + 1)
            if len(resp) < 8 or resp[0] != 0x07:
                raise BmapError(f"unexpected SDP response from {addr}")
            response_tid, param_len = struct.unpack(">HH", resp[1:5])
            if response_tid != tid or param_len != len(resp) - 5:
                raise BmapError(f"invalid SDP response header from {addr}")
            count = struct.unpack(">H", resp[5:7])[0]
            end = 7 + count
            if end >= len(resp):
                raise BmapError(f"invalid SDP attribute byte count from {addr}")
            cont = resp[end:]
            if cont[0] > 16 or len(cont) != 1 + cont[0]:
                raise BmapError(f"invalid SDP continuation state from {addr}")
            if count == 0:
                raise BmapError(f"SDP response made no progress from {addr}")
            if len(raw) + count > SDP_MAX_BYTES:
                raise BmapError(f"SDP byte limit exceeded from {addr}")
            if cont[0]:
                if cont in seen:
                    raise BmapError(f"repeated SDP continuation state from {addr}")
                if tid == SDP_MAX_RESPONSES:
                    raise BmapError(f"SDP response limit exceeded from {addr}")
                seen.add(cont)
            raw.extend(resp[7:end])
            if cont[0] == 0:
                break
    finally:
        s.close()
    # RFCOMM protocol descriptor: DES(UUID16 0x0003, UINT8 channel)
    marker = bytes([0x19, 0x00, 0x03, 0x08])
    i = raw.find(marker)
    if i < 0 or i + len(marker) >= len(raw):
        raise BmapError(f"no RFCOMM service for UUID 0x{uuid16:04x} on {addr}")
    return raw[i + len(marker)]


class Transport:
    def __init__(self, addr, channel=None):
        self.addr = addr
        self.channel = channel or find_rfcomm_channel(addr)
        self.buf = b""
        for attempt in range(15):
            self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            try:
                self.sock.settimeout(8)
                self.sock.connect((addr, self.channel))
                return
            except OSError as e:
                self.sock.close()
                # a previous RFCOMM session on this channel may still be tearing down
                if e.errno != errno.EBUSY or attempt == 14:
                    raise
                time.sleep(0.3)

    def close(self):
        self.sock.close()

    def send(self, block, func, op, payload=b""):
        if len(payload) > 255:
            raise BmapError(f"payload for {block}.{func} is too long ({len(payload)} bytes)")
        self.sock.sendall(bytes([block, func, op, len(payload)]) + payload)

    def recv(self, timeout):
        """Return the next complete packet, or None on timeout."""
        deadline = time.monotonic() + timeout
        while len(self.buf) < 4 or len(self.buf) < 4 + self.buf[3]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                return None
            if not chunk:
                raise ConnectionLost("connection closed by headset")
            self.buf += chunk
        n = 4 + self.buf[3]
        pkt, self.buf = self.buf[:n], self.buf[n:]
        return pkt


# ---------------------------------------------------------------- client

class Bose:
    def __init__(self, addr, channel=None):
        self.t = Transport(addr, channel)
        self.addr = addr
        self.on_unsolicited = None
        self._blocks = None

    def close(self):
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- request/response

    def request(self, block, func, op=GET, payload=b"", timeout=4, collect=False):
        """Send a request and return the reply payload.

        collect=True returns every STATUS payload until RESULT (multi-part START replies).
        A PROCESSING reply is returned if nothing else follows within a second.
        """
        self.t.send(block, func, op, payload)
        deadline = time.monotonic() + timeout
        processing, parts = None, []
        while True:
            pkt = self.t.recv(max(deadline - time.monotonic(), 0))
            if pkt is None:
                if processing is not None:
                    return parts if collect else processing
                raise BmapError(f"timeout waiting for {block}.{func}", code=9)
            if pkt[0] != block or pkt[1] != func:
                if self.on_unsolicited:
                    self.on_unsolicited(pkt)
                continue
            code, body = pkt[2] & 0x0F, pkt[4:]
            if code == ERROR:
                err = body[0] if body else 0
                raise BmapError(f"{block}.{func}: {ERRORS.get(err, err)}", code=err)
            if code == PROCESSING:
                processing = body
                deadline = min(deadline, time.monotonic() + (timeout if collect else 1.0))
                continue
            if collect:
                if code == RESULT:
                    return parts
                parts.append(body)
                continue
            if code in (STATUS, RESULT):
                return body

    def get(self, block, func, payload=b""):
        return self.request(block, func, GET, payload)

    def setget(self, block, func, payload):
        return self.request(block, func, SETGET, payload)

    def start(self, block, func, payload=b"", **kw):
        return self.request(block, func, START, payload, **kw)

    def try_get(self, block, func, payload=b""):
        """GET that returns None when the headset doesn't support or can't answer the function."""
        if block not in self.blocks():
            return None
        try:
            return self.get(block, func, payload)
        except BmapError as e:
            if e.code in (3, 4, 5, 7):
                return None
            raise

    # -- capabilities

    def blocks(self):
        if self._blocks is None:
            mask = int.from_bytes(self.get(PRODUCT_INFO, 2), "big")
            self._blocks = set(bits(mask, 32)) | {PRODUCT_INFO}
        return self._blocks

    def has_modes(self):
        return AUDIO_MODES in self.blocks()

    # -- product info / status

    def info(self):
        def text(block, func, skip=0):
            p = self.try_get(block, func)
            return p[skip:].decode(errors="replace") if p else None

        pid = self.try_get(PRODUCT_INFO, 3) or b""
        return {
            "address": self.addr,
            "name": text(SETTINGS, 2, skip=1),
            "product_id": f"0x{pid[0] << 8 | pid[1]:04x}" if len(pid) >= 2 else None,
            "variant": pid[2] if len(pid) >= 3 else None,
            "firmware": text(PRODUCT_INFO, 5),
            "serial": text(PRODUCT_INFO, 7),
            "bmap_version": text(PRODUCT_INFO, 1),
            "function_blocks": sorted(self.blocks()),
        }

    def battery(self):
        p = self.try_get(STATUS_BLOCK, 2) or b""
        out = []
        for i in range(0, len(p) - len(p) % 4, 4):
            minutes = p[i + 1] << 8 | p[i + 2]
            out.append({"percent": p[i], "minutes": None if minutes == 0xFFFF else minutes,
                        "component": p[i + 3]})
        return out

    # -- audio modes

    def modes_capabilities(self):
        p = need(self.get(AUDIO_MODES, 2), 2, "mode capabilities")
        flags = p[5] if len(p) > 5 else 0
        return {"bose_modes": p[0], "user_modes": p[1],
                "cnc": bool(flags & 1), "auto_cnc": bool(flags & 2), "spatial": bool(flags & 4),
                "wind_block": bool(flags & 8), "favorites": bool(flags & 16),
                "anc_toggle": bool(flags & 32), "min_favorites": p[6] if len(p) > 6 else 0}

    def _cnc_steps(self):
        p = self.try_get(SETTINGS, 5)
        return p[0] if p and p[0] > 1 else 11

    @staticmethod
    def parse_mode(p, steps=11):
        need(p, 44, "mode config")
        flags = p[41]
        prompt = (p[1], p[2])
        cnc = min(p[42], steps - 1)
        return {
            "index": p[0],
            "name": cstr(p[6:38]),
            "icon": PROMPTS[p[2]] if p[1] == 0 and p[2] < len(PROMPTS) else f"{p[1]:02x}{p[2]:02x}",
            "empty": prompt == (0, 0),
            "custom": p[3] == 1,
            "configured": p[4] == 1,
            "favorite": p[5] == 1,
            "noise_cancel": steps - 1 - cnc,  # app scale: 0 = aware … max = full cancelling
            "noise_cancel_max": steps - 1,
            "cnc_level": p[42],  # raw BMAP value
            "auto_cnc": p[43] == 1,
            "spatial": SPATIAL[p[44]] if len(p) >= 45 and p[44] < len(SPATIAL) else None,
            "wind_block": p[46] == 1 if len(p) >= 47 else None,
            "anc_toggle": p[47] == 1 if len(p) >= 48 else None,
            "editable": {"noise_cancel": bool(flags & 1), "auto_cnc": bool(flags & 2),
                         "spatial": bool(flags & 4), "wind_block": bool(flags & 8),
                         "anc_toggle": bool(flags & 16)},
            "_prompt": prompt,
        }

    def mode_slots(self):
        """Every mode slot, including empty ones (icon 'none')."""
        caps = self.modes_capabilities()
        steps = self._cnc_steps()
        slots = []
        for i in range(caps["bose_modes"] + caps["user_modes"]):
            try:
                slots.append(self.parse_mode(self.get(AUDIO_MODES, 6, bytes([i])), steps))
            except BmapError as e:
                if e.code != 6:
                    raise
        return slots

    def modes(self):
        if not self.has_modes():
            return []
        current = self.current_mode()
        # with persistence on, the headset powers on in the last used mode and
        # DefaultMode just mirrors the active one; the default only matters when it's off
        persistence = self.try_get(AUDIO_MODES, 5)
        default = None if persistence and persistence[0] else self.try_get(AUDIO_MODES, 4)
        out = []
        for m in self.mode_slots():
            if m["empty"]:
                continue
            m["active"] = m["index"] == current
            m["default"] = bool(default) and m["index"] == default[0]
            out.append(m)
        return out

    def current_mode(self):
        return need(self.get(AUDIO_MODES, 3), 1, "current mode")[0]

    def find_mode(self, ref, modes=None):
        modes = modes if modes is not None else self.modes()
        for m in modes:
            if (str(ref).isdigit() and m["index"] == int(ref)) or m["name"].lower() == str(ref).lower():
                return m
        raise BmapError(f"no mode {ref!r} (have: {', '.join(m['name'] for m in modes) or 'none'})")

    def set_current_mode(self, index, prompt=False):
        self.start(AUDIO_MODES, 3, bytes([index, int(prompt)]))
        for _ in range(10):
            if self.current_mode() == index:
                return
            time.sleep(0.1)
        raise BmapError("headset did not switch mode")

    @staticmethod
    def _mode_payload(index, prompt, name, cnc_level, auto_cnc, spatial, wind_block, anc_toggle):
        """ModeConfig SETGET payload; prompt is a (byte1, byte2) pair or an icon name."""
        if isinstance(prompt, str):
            if prompt not in PROMPTS:
                raise BmapError(f"unknown icon {prompt!r}")
            prompt = (0, PROMPTS.index(prompt))
        encoded = name.encode()
        if len(encoded) > 31:
            raise BmapError("mode name must be at most 31 bytes")
        p = bytes([index, *prompt]) + encoded.ljust(32, b"\0") + bytes([cnc_level, int(bool(auto_cnc))])
        # optional tail: spatial and wind block travel together; ANC toggle only on 48-byte records
        if spatial is not None or wind_block is not None:
            p += bytes([SPATIAL.index(spatial) if spatial is not None else 0])
            p += bytes([int(bool(wind_block))])
        if anc_toggle is not None:
            p += bytes([int(anc_toggle)])
        return p

    def edit_mode(self, index, name=None, icon=None, noise_cancel=None, auto_cnc=None,
                  spatial=None, wind_block=None, anc_toggle=None):
        steps = self._cnc_steps()
        m = self.parse_mode(self.get(AUDIO_MODES, 6, bytes([index])), steps)
        if m["empty"]:
            raise BmapError(f"mode slot {index} is empty")
        changes = {"noise_cancel": noise_cancel, "auto_cnc": auto_cnc, "spatial": spatial,
                   "wind_block": wind_block, "anc_toggle": anc_toggle}
        for key, value in changes.items():
            if value is not None and not m["editable"][key]:
                raise BmapError(f"{key} is not editable on mode {m['name']!r}")
        if (name is not None or icon is not None) and not m["custom"]:
            raise BmapError(f"mode {m['name']!r} is a Bose preset; name and icon are fixed")
        if name is not None and not name.strip():
            raise BmapError("mode name cannot be empty")
        if noise_cancel is not None and not 0 <= noise_cancel < steps:
            raise BmapError(f"noise_cancel must be 0..{steps - 1}")
        payload = self._mode_payload(
            index,
            icon if icon is not None else m["_prompt"],
            name.strip() if name is not None else m["name"],
            steps - 1 - noise_cancel if noise_cancel is not None else m["cnc_level"],
            auto_cnc if auto_cnc is not None else m["auto_cnc"],
            spatial if spatial is not None else m["spatial"],
            wind_block if wind_block is not None else m["wind_block"],
            anc_toggle if anc_toggle is not None else m["anc_toggle"],
        )
        return self.parse_mode(self.setget(AUDIO_MODES, 6, payload), steps)

    def create_mode(self, name, icon, noise_cancel=None, wind_block=None, spatial=None,
                    auto_cnc=None, anc_toggle=None):
        if icon not in PROMPTS or icon == "none":
            raise BmapError(f"icon must be one of: {', '.join(PROMPTS[1:])}")
        if not name.strip():
            raise BmapError("mode name cannot be empty")
        free = next((s for s in self.mode_slots() if s["empty"]), None)
        if free is None:
            raise BmapError("no free mode slot; delete a custom mode first")
        steps = self._cnc_steps()
        if noise_cancel is not None and not 0 <= noise_cancel < steps:
            raise BmapError(f"noise_cancel must be 0..{steps - 1}")
        # new modes start at the middle level, keeping the slot's other defaults
        payload = self._mode_payload(
            free["index"], icon, name.strip(),
            steps - 1 - noise_cancel if noise_cancel is not None else (steps - 1) // 2,
            auto_cnc if auto_cnc is not None else free["auto_cnc"],
            spatial if spatial is not None else free["spatial"],
            wind_block if wind_block is not None else free["wind_block"],
            anc_toggle if anc_toggle is not None else free["anc_toggle"],
        )
        return self.parse_mode(self.setget(AUDIO_MODES, 6, payload), steps)

    def delete_mode(self, index):
        steps = self._cnc_steps()
        m = self.parse_mode(self.get(AUDIO_MODES, 6, bytes([index])), steps)
        if not m["custom"]:
            raise BmapError(f"mode {m['name']!r} is a Bose preset and cannot be deleted")
        was_active = self.current_mode() == index
        try:
            self.start(AUDIO_MODES, 9, bytes([index]))
        except BmapError as e:
            if isinstance(e, ConnectionLost):
                raise
            # fallback: overwrite the slot with an empty mode
            self.setget(AUDIO_MODES, 6, self._mode_payload(
                index, (0, 0), "", (steps - 1) // 2, m["auto_cnc"], m["spatial"], m["wind_block"],
                m["anc_toggle"]))
        if was_active:
            self.set_current_mode(0)

    def set_default_mode(self, index):
        return self.setget(AUDIO_MODES, 4, bytes([index]))

    # -- settings

    def settings(self):
        """Every setting this headset answers, decoded. Settings with malformed replies are skipped."""
        out = {}

        def field(key, block, func, decode):
            p = self.try_get(block, func)
            if p is None:
                return
            try:
                out[key] = decode(p)
            except (BmapError, IndexError, struct.error, ValueError):
                pass  # decoders don't do I/O, so this only skips a malformed setting

        def voice_prompts(p):
            supported = int.from_bytes(p[1:5], "big") if len(p) >= 5 else 0
            lang = p[0] & 0x1F
            return {"enabled": bool(p[0] & 0x20),
                    "language": LANGUAGES[lang] if lang < len(LANGUAGES) else lang,
                    "languages": [LANGUAGES[i] for i in bits(supported, len(LANGUAGES))]}

        def eq(p):
            bands = {}
            for i in range(0, len(p) - len(p) % 4, 4):
                lo, hi, cur, band = struct.unpack("bbbB", p[i:i + 4])
                bands[EQ_BANDS[band] if band < len(EQ_BANDS) else str(band)] = {"value": cur, "min": lo, "max": hi}
            if not bands:
                raise ValueError("no bands")
            return bands

        def shortcut(p):
            supported = int.from_bytes(need(p, 7, "shortcut")[3:7], "big")
            unavailable = int.from_bytes(p[7:11], "big") if len(p) >= 11 else 0
            return {"button": BUTTON_IDS.get(p[0], p[0]), "event": p[1],
                    "action": BUTTON_ACTIONS[p[2]] if p[2] < len(BUTTON_ACTIONS) else p[2],
                    "actions": [BUTTON_ACTIONS[i] for i in bits(supported, len(BUTTON_ACTIONS))
                                if not unavailable >> i & 1]}

        def sidetone(p):
            supported = p[2] if len(p) > 2 else 0
            return {"mode": SIDETONE[p[1]] if p[1] < len(SIDETONE) else p[1], "persistent": bool(p[0] & 1),
                    "modes": [SIDETONE[i] for i in bits(supported, len(SIDETONE))]}

        field("voice_prompts", SETTINGS, 3, voice_prompts)
        # auto-off layout: [minutes], or [lo, ?, hi] when 3+ bytes
        field("auto_off_minutes", SETTINGS, 4, lambda p: p[0] if len(p) < 3 else p[2] << 8 | p[0])
        field("noise_cancel", SETTINGS, 5, lambda p: {
            "level": p[0] - 1 - p[1], "max": p[0] - 1, "cnc_level": p[1],
            "enabled": bool(p[2] & 1), "toggleable": not bool(p[2] & 2)})
        field("eq", SETTINGS, 7, eq)
        field("shortcut", SETTINGS, 9, shortcut)
        field("multipoint", SETTINGS, 10, lambda p: {
            "enabled": bool(p[0] & 1), "supported": bool(p[0] & 2), "can_disable": bool(p[0] & 4)})
        field("sidetone", SETTINGS, 11, sidetone)
        field("cnc_persistence", SETTINGS, 14, lambda p: bool(p[0] & 1))
        field("on_head_detection", SETTINGS, 16, lambda p: {
            "enabled": bool(p[0] & 1),
            "auto_play": bool(p[1] & 1) if p[0] & 2 else None,
            "auto_answer": bool(p[1] & 2) if p[0] & 4 else None,
            "auto_transparency": bool(p[1] & 4) if p[0] & 8 else None})
        for func, key in ((20, "motion_auto_off"), (22, "flip_to_off"), (24, "auto_play_pause"),
                          (27, "auto_answer"), (29, "auto_aware"), (32, "auto_volume"),
                          (34, "disable_touch"), (37, "le_audio")):
            field(key, SETTINGS, func, lambda p: bool(p[0] & 1))
        field("mode_persistence", AUDIO_MODES, 5, lambda p: bool(p[0]))
        field("volume", AUDIO_MGMT, 5, lambda p: {"value": p[1], "max": p[0]})
        return out

    def set_name(self, name):
        encoded = name.strip().encode()
        if not 0 < len(encoded) <= 64:
            raise BmapError("headset name must be 1..64 bytes")
        return cstr(self.setget(SETTINGS, 2, encoded)[1:])

    def set_voice_prompts(self, enabled=None, language=None):
        cur = need(self.get(SETTINGS, 3), 1, "voice prompts")[0]
        lang = LANGUAGES.index(language) if language is not None else cur & 0x1F
        en = enabled if enabled is not None else bool(cur & 0x20)
        value = int(en) << 5 | lang
        try:
            self.request(SETTINGS, 3, SETGET, bytes([value]), timeout=1.5)
        except BmapError as e:
            # the headset applies a changed value but sends no reply; verify instead
            if e.code != 9 or self.get(SETTINGS, 3)[0] & 0x3F != value:
                raise

    def set_auto_off(self, minutes):
        if not 0 <= minutes <= 0xFFFF:
            raise BmapError("auto-off must be 0..65535 minutes")
        # one byte, or low byte then high byte
        payload = bytes([minutes]) if minutes <= 255 else bytes([minutes & 0xFF, minutes >> 8])
        self.setget(SETTINGS, 4, payload)

    def set_noise_cancel(self, level):
        steps = self._cnc_steps()
        if not 0 <= level < steps:
            raise BmapError(f"noise cancel level must be 0..{steps - 1}")
        try:
            p = need(self.setget(SETTINGS, 5, bytes([steps - 1 - level, 1])), 2, "noise cancel")
            return p[0] - 1 - p[1]
        except BmapError as e:
            if e.code != 5 or not self.has_modes():
                raise
        # mode-based headsets: noise cancelling belongs to the active mode
        return self.edit_mode(self.current_mode(), noise_cancel=level)["noise_cancel"]

    def set_eq(self, band, value):
        bands = self.settings().get("eq", {})
        if band not in bands:
            raise BmapError(f"this headset has no {band} EQ band")
        lo, hi = bands[band]["min"], bands[band]["max"]
        if not lo <= value <= hi:
            raise BmapError(f"{band} must be {lo}..{hi}")
        self.setget(SETTINGS, 7, struct.pack("bB", value, EQ_BANDS.index(band)))

    def set_shortcut(self, action):
        p = need(self.get(SETTINGS, 9), 2, "shortcut")
        self.setget(SETTINGS, 9, bytes([p[0], p[1], BUTTON_ACTIONS.index(action)]))

    def set_bool(self, func, value):
        self.setget(SETTINGS, func, bytes([int(value)]))

    def set_sidetone(self, mode, persistent=True):
        self.setget(SETTINGS, 11, bytes([int(persistent), SIDETONE.index(mode)]))

    def set_volume(self, value):
        p = need(self.get(AUDIO_MGMT, 5), 2, "volume")
        if not 0 <= value <= p[0]:
            raise BmapError(f"volume must be 0..{p[0]}")
        return self.setget(AUDIO_MGMT, 5, bytes([value]))

    def set_mode_persistence(self, value):
        self.setget(AUDIO_MODES, 5, bytes([int(value)]))

    # -- paired devices

    def devices(self):
        p = self.get(DEVICE_MGMT, 4)
        if len(p) <= 1:
            return []
        connected = p[0]
        out = []
        for n, i in enumerate(range(1, len(p) - (len(p) - 1) % 6, 6)):
            addr = p[i:i + 6]
            dev = {"address": mac(addr), "connected": bool(connected >> n & 1), "name": None}
            try:
                info = need(self.get(DEVICE_MGMT, 5, addr), 7, "device info")
                flags = info[6]
                dev["this_app"] = bool(flags & 2)
                dev["bose"] = bool(flags & 4)
                dev["name"] = cstr(info[10:] if flags & 4 else info[9:])
            except BmapError as e:
                if isinstance(e, ConnectionLost):
                    raise
            out.append(dev)
        return out

    def connect_device(self, address):
        self.start(DEVICE_MGMT, 1, b"\x00" + mac_bytes(address), timeout=15)

    def disconnect_device(self, address):
        self.start(DEVICE_MGMT, 2, mac_bytes(address), timeout=15)

    def remove_device(self, address):
        self.start(DEVICE_MGMT, 3, mac_bytes(address), timeout=15)

    def set_pairing_mode(self, enabled):
        self.start(DEVICE_MGMT, 8, bytes([int(enabled)]), timeout=10)

    # -- notifications

    def subscribe(self):
        """Ask the headset to push changes. Returns False if it has nothing to subscribe to."""
        wanted = sorted(NOTIFY_BLOCKS & self.blocks())
        if NOTIFICATION not in self.blocks() or not wanted:
            return False
        mask = sum(1 << b for b in wanted)
        width = 4 if max(wanted) >= 16 else 2
        self.setget(NOTIFICATION, 2, bytes([1]) + mask.to_bytes(width, "big"))
        return True

    def events(self):
        """Yield unsolicited (block, func, operator, payload) tuples forever."""
        while True:
            pkt = self.t.recv(3600)
            if pkt is not None:
                yield pkt[0], pkt[1], pkt[2] & 0x0F, pkt[4:]
