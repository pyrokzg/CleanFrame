"""Offline tests for the startup watcher: framing and power-on decisions. No TV contact."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanframe_watcher as w  # noqa: E402


def reader(data):
    stream = bytearray(data)

    def read(n):
        if len(stream) < n:
            raise ConnectionError('short read')
        out = bytes(stream[:n])
        del stream[:n]
        return out
    return read


class FakeTV:
    def __init__(self, states, visible=None, launch_error=None):
        self.states, self.visible, self.launch_error, self.launches, self.routes = list(states), visible, launch_error, 0, []

    def power_state(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def app_visible(self):
        return self.visible(self) if callable(self.visible) else self.visible

    def launch(self, route=None):
        self.launches += 1
        self.routes.append(route)
        if self.launch_error:
            raise self.launch_error
        return 'websocket'


class Framing(unittest.TestCase):
    def test_masked_round_trip_all_lengths(self):
        for size in (0, 5, 125, 126, 300, 65535, 65536):
            payload = bytes(i % 251 for i in range(size))
            opcode, data = w.decode_frame(reader(w.encode_frame(payload, opcode=0x2)))
            self.assertEqual((opcode, data), (0x2, payload), size)

    def test_client_frames_are_masked(self):
        frame = w.encode_frame('hello', mask=b'\x01\x02\x03\x04')
        self.assertEqual(frame[0], 0x81)
        self.assertTrue(frame[1] & 0x80)
        self.assertNotIn(b'hello', frame)

    def test_unmasked_server_frame(self):
        self.assertEqual(w.decode_frame(reader(b'\x81\x02hi')), (0x1, b'hi'))


class Decisions(unittest.TestCase):
    def watcher(self, tv):
        return w.Watcher(tv, settle=0, retry_gap=0, sleep=lambda s: None)

    def test_startup_state_is_left_alone(self):
        tv = FakeTV(['on'], visible=False)
        self.watcher(tv).step()
        self.assertEqual(tv.launches, 0)

    def test_standby_to_on_launches_until_visible(self):
        seen = {'n': 0}

        def visible(tv):
            seen['n'] += 1
            return tv.launches >= 1
        tv = FakeTV(['standby'], visible=visible)
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        self.assertEqual(tv.launches, 1)

    def test_off_to_on_retries_a_bounded_number_of_times(self):
        tv = FakeTV(['off'], visible=False)
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        self.assertEqual(tv.launches, watcher.attempts)
        self.assertEqual(tv.routes, [None] * (watcher.attempts - 1) + ['websocket'])

    def test_already_visible_sends_nothing(self):
        tv = FakeTV(['standby'], visible=True)
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        self.assertEqual(tv.launches, 0)

    def test_unknown_visibility_sends_one_request(self):
        tv = FakeTV(['standby'], visible=None)
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        self.assertEqual(tv.launches, 1)

    def test_denied_pairing_stops_retries(self):
        tv = FakeTV(['standby'], visible=False, launch_error=PermissionError('denied'))
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        self.assertEqual(tv.launches, 1)

    def test_staying_on_does_not_relaunch(self):
        tv = FakeTV(['standby'], visible=lambda tv: tv.launches >= 1)
        watcher = self.watcher(tv)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        watcher.step()
        watcher.step()
        self.assertEqual(tv.launches, 1)

    def test_source_picked_after_app_shows_is_not_undone(self):
        # Before the launch CleanFrame is hidden; it shows briefly, then the viewer picks a source.
        answers = [False, False, True, False, False, False, False, False, False, False]

        def visible(tv):
            return answers.pop(0) if answers else False
        tv = FakeTV(['standby'], visible=visible)
        watcher = w.Watcher(tv, settle=0, retry_gap=4, check_gap=1, sleep=lambda s: None)
        watcher.step()
        tv.states = ['on']
        watcher.step()
        watcher.step()
        self.assertEqual(tv.launches, 1)


class Tokens(unittest.TestCase):
    def test_token_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            tv = w.TV('192.0.2.1', 'ABCDE12345.CleanFrame', os.path.join(folder, 'sub', 'token.json'))
            self.assertEqual(tv.load_token(), '')
            tv.save_token('12345678')
            self.assertEqual(tv.load_token(), '12345678')


if __name__ == '__main__':
    unittest.main()
