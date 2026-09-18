"""SDP regression tests using a simulated, potentially malicious Bluetooth peer."""

import struct
import unittest
from unittest.mock import Mock, patch

import bmap


ADDR = "00:11:22:33:44:55"
RFCOMM = b"\x19\x00\x03\x08\x07"


def response(data=RFCOMM, token=b"", tid=1):
    params = struct.pack(">H", len(data)) + data + bytes([len(token)]) + token
    return struct.pack(">BHH", 0x07, tid, len(params)) + params


class SdpTests(unittest.TestCase):
    def setUp(self):
        self.sock = Mock()
        factory = patch("bmap.socket.socket", return_value=self.sock)
        factory.start()
        self.addCleanup(factory.stop)

    def discover(self, responses):
        self.sock.recv.side_effect = responses
        try:
            return bmap.find_rfcomm_channel(ADDR)
        finally:
            self.sock.close.assert_called_once_with()

    def test_single_response(self):
        self.assertEqual(self.discover([response()]), 7)
        self.sock.connect.assert_called_once_with((ADDR, 1))

    def test_fragmented_response_and_request_continuation(self):
        token = bytes(range(16))
        self.assertEqual(self.discover([
            response(RFCOMM[:2], token), response(RFCOMM[2:], tid=2),
        ]), 7)
        first, second = [call.args[0] for call in self.sock.send.call_args_list]
        self.assertEqual(struct.unpack(">BHH", first[:5]), (6, 1, len(first) - 5))
        self.assertEqual(struct.unpack(">BHH", second[:5]), (6, 2, len(second) - 5))
        self.assertEqual(first[-1:], b"\0")
        self.assertEqual(second[5:], first[5:-1] + bytes([len(token)]) + token)

    def test_malformed_responses(self):
        valid = response()
        cases = {
            "empty": b"",
            "short header": valid[:4],
            "short parameters": valid[:7],
            "wrong PDU": b"\x01" + valid[1:],
            "wrong transaction": response(tid=2),
            "short declared length": valid[:3] + struct.pack(">H", len(valid) - 6) + valid[5:],
            "long declared length": valid[:3] + struct.pack(">H", len(valid) - 4) + valid[5:],
            "oversized count": valid[:5] + b"\xff\xff" + valid[7:],
            "count includes continuation": valid[:5] + struct.pack(">H", len(RFCOMM) + 1) + valid[7:],
            "missing continuation": valid[:3] + struct.pack(">H", len(valid) - 6) + valid[5:-1],
            "short token": valid[:-1] + b"\x01",
            "long token": response(token=b"ab")[:-3] + b"\x01ab",
            "token over 16 bytes": response(token=b"a" * 17),
            "trailing bytes after terminator": response(token=b"a")[:-2] + b"\0a",
        }
        for label, packet in cases.items():
            with self.subTest(label=label):
                self.sock.reset_mock()
                with self.assertRaises(bmap.BmapError):
                    self.discover([packet])
                self.assertEqual(self.sock.recv.call_count, 1)

    def test_repeated_and_cycling_tokens(self):
        for tokens in ((b"a", b"a"), (b"a", b"b", b"a")):
            with self.subTest(tokens=tokens):
                self.sock.reset_mock()
                packets = [response(b"x", token, tid) for tid, token in enumerate(tokens, 1)]
                with self.assertRaisesRegex(bmap.BmapError, "repeated SDP continuation"):
                    self.discover(packets)
                self.assertEqual(self.sock.recv.call_count, len(tokens))

    def test_zero_byte_progress(self):
        for token in (b"", b"a"):
            with self.subTest(token=token):
                self.sock.reset_mock()
                with self.assertRaisesRegex(bmap.BmapError, "no progress"):
                    self.discover([response(b"", token)])

    def test_changing_token_without_progress(self):
        with self.assertRaisesRegex(bmap.BmapError, "no progress"):
            self.discover([response(b"x", b"a"), response(b"", b"b", tid=2)])
        self.assertEqual(self.sock.recv.call_count, 2)

    def test_response_limit(self):
        packets = [response(b"x", bytes([tid]), tid)
                   for tid in range(1, bmap.SDP_MAX_RESPONSES + 2)]
        with self.assertRaisesRegex(bmap.BmapError, "response limit"):
            self.discover(packets)
        self.assertEqual(self.sock.recv.call_count, bmap.SDP_MAX_RESPONSES)
        self.assertEqual(self.sock.send.call_count, bmap.SDP_MAX_RESPONSES)

    def test_completion_at_response_limit(self):
        packets = [response(b"x", bytes([tid]), tid)
                   for tid in range(1, bmap.SDP_MAX_RESPONSES)]
        packets.append(response(tid=bmap.SDP_MAX_RESPONSES))
        self.assertEqual(self.discover(packets), 7)

    def test_byte_limit(self):
        # Stay below the response limit so the independent memory cap is exercised.
        for extra in (0, 1):
            with self.subTest(extra=extra):
                self.sock.reset_mock()
                data = RFCOMM + b"x" * (bmap.SDP_MAX_BYTES + extra - len(RFCOMM))
                chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
                packets = [response(chunk, bytes([tid]) if tid < len(chunks) else b"", tid)
                           for tid, chunk in enumerate(chunks, 1)]
                if extra:
                    with self.assertRaisesRegex(bmap.BmapError, "byte limit"):
                        self.discover(packets)
                else:
                    self.assertEqual(self.discover(packets), 7)
                self.assertEqual(self.sock.recv.call_count, len(chunks))

    def test_truncated_oversized_datagram(self):
        # Simulate recv's truncation of a datagram larger than any SDP PDU.
        packet = struct.pack(">BHH", 7, 1, 0xFFFF) + b"x" * 0x10000
        self.sock.recv.side_effect = lambda size: packet[:size]
        with self.assertRaisesRegex(bmap.BmapError, "invalid SDP response header"):
            bmap.find_rfcomm_channel(ADDR)
        self.sock.close.assert_called_once_with()

    def test_missing_or_incomplete_rfcomm_descriptor(self):
        for data in (b"\x35\0", RFCOMM[:-1]):
            with self.subTest(data=data):
                self.sock.reset_mock()
                with self.assertRaisesRegex(bmap.BmapError, "no RFCOMM service"):
                    self.discover([response(data)])

    def test_timeout_closes_socket(self):
        with self.assertRaises(TimeoutError):
            self.discover([TimeoutError("peer timed out")])


if __name__ == "__main__":
    unittest.main()
