"""Optional power-on mode metadata must not hide working QC Ultra controls."""
import unittest
from unittest.mock import Mock, patch

from bmap import AUDIO_MODES, BmapError, Bose, ConnectionLost


class ModeTests(unittest.TestCase):
    def setUp(self):
        with patch('bmap.Transport'):
            self.bose = Bose('00:11:22:33:44:55')
        self.bose.has_modes = Mock(return_value=True)
        self.bose.current_mode = Mock(return_value=1)
        self.bose.mode_slots = Mock(side_effect=lambda: [
            {'index': 0, 'empty': False}, {'index': 1, 'empty': False}])

    def reads(self, default):
        def get(block, func):
            self.assertEqual(block, AUDIO_MODES)
            if func == 5:
                return b'\x00'
            self.assertEqual(func, 4)
            if isinstance(default, Exception):
                raise default
            return default
        self.bose.try_get = Mock(side_effect=get)

    def test_timeout_does_not_hide_modes_or_repeat_on_refresh(self):
        self.reads(BmapError('timeout waiting for 31.4', code=9))
        for _ in range(2):
            modes = self.bose.modes()
            self.assertTrue(modes[1]['active'])
            self.assertFalse(any(m['default'] for m in modes))
        defaults = [c for c in self.bose.try_get.call_args_list if c.args[1] == 4]
        self.assertEqual(len(defaults), 1)

    def test_answered_default_still_reported(self):
        self.reads(b'\x00')
        self.assertTrue(self.bose.modes()[0]['default'])

    def test_other_errors_still_propagate(self):
        for error in (BmapError('invalid data', code=6), ConnectionLost('disconnected')):
            with self.subTest(error=error):
                self.reads(error)
                with self.assertRaises(type(error)):
                    self.bose.modes()

    def test_persistent_mode_does_not_query_default(self):
        self.bose.try_get = Mock(return_value=b'\x01')
        self.bose.modes()
        self.bose.try_get.assert_called_once_with(AUDIO_MODES, 5)
