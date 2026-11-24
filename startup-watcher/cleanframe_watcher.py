#!/usr/bin/env python3
"""Open CleanFrame whenever a Samsung TV turns on.

Runs on an always-on LAN machine (for example a Raspberry Pi). Python 3.7+, standard
library only. It changes no TV settings: it polls the TV's local REST API for its power
state and, on a standby/off -> on transition, asks the TV to open the configured app
with the REST launch call, falling back to Samsung's local remote-control WebSocket (its
first connection shows an Allow prompt on the TV; the returned token is saved).

    python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --pair
    python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame --launch-now
    python3 cleanframe_watcher.py --tv TV_IP --app PACKAGE.CleanFrame
"""
import argparse
import base64
import json
import logging
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

LOG = logging.getLogger('cleanframe-watcher')
REMOTE_NAME = 'CleanFrame watcher'


# ---------- Minimal WebSocket client (TLS, text frames) ----------
def encode_frame(payload, opcode=0x1, mask=None):
    """Client frames are always masked (RFC 6455)."""
    data = payload.encode() if isinstance(payload, str) else payload
    mask = mask if mask is not None else os.urandom(4)
    head = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack('!H', n)
    else:
        head += bytes([0x80 | 127]) + struct.pack('!Q', n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def decode_frame(read):
    """Read one frame with read(n) -> bytes. Returns (opcode, payload bytes)."""
    b1, b2 = read(2)
    opcode, n = b1 & 0x0F, b2 & 0x7F
    if n == 126:
        n = struct.unpack('!H', read(2))[0]
    elif n == 127:
        n = struct.unpack('!Q', read(8))[0]
    mask = read(4) if b2 & 0x80 else None
    data = read(n) if n else b''
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


class WebSocket:
    def __init__(self, host, port, path, timeout):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # The TV uses a self-signed certificate on the LAN.
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = context.wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ('GET ' + path + ' HTTP/1.1\r\nHost: ' + host + ':' + str(port) + '\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode())
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError('TV closed the WebSocket handshake.')
            response += chunk
        head, self.buffer = response.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise ConnectionError('TV refused the WebSocket: ' + head.split(b'\r\n', 1)[0].decode(errors='replace'))

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('TV closed the WebSocket.')
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def send(self, text):
        self.sock.sendall(encode_frame(text))

    def recv(self):
        while True:
            opcode, data = decode_frame(self._read)
            if opcode == 0x9:
                self.sock.sendall(encode_frame(data, opcode=0xA))
            elif opcode == 0x8:
                raise ConnectionError('TV closed the WebSocket.')
            elif opcode in (0x1, 0x2):
                return data.decode(errors='replace')

    def close(self):
        try:
            self.sock.sendall(encode_frame(b'', opcode=0x8))
        except OSError:
            pass
        self.sock.close()


# ---------- TV API ----------
class TV:
    def __init__(self, host, app_id, token_file, timeout=5):
        self.host, self.app_id, self.token_file, self.timeout = host, app_id, token_file, timeout

    def _rest(self, path, method='GET'):
        request = urllib.request.Request('http://' + self.host + ':8001/api/v2/' + path, method=method)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode(errors='replace')
        return json.loads(body) if body.strip().startswith(('{', '[')) else body

    def power_state(self):
        """'on', 'standby' or 'off' (API unreachable)."""
        try:
            info = self._rest('')
        except (OSError, ValueError, urllib.error.URLError):
            return 'off'
        state = str(info.get('device', {}).get('PowerState', 'on')).lower()
        return 'on' if state == 'on' else 'standby'

    def app_visible(self):
        """True/False when the TV reports it, None when this TV does not say."""
        try:
            info = self._rest('applications/' + self.app_id)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return bool(info.get('visible')) if isinstance(info, dict) and 'visible' in info else None

    def load_token(self):
        try:
            with open(self.token_file) as handle:
                return json.load(handle).get('token') or ''
        except (OSError, ValueError):
            return ''

    def save_token(self, token):
        folder = os.path.dirname(os.path.abspath(self.token_file))
        os.makedirs(folder, exist_ok=True)
        temp = self.token_file + '.tmp'
        with open(temp, 'w') as handle:
            json.dump({'token': token}, handle)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.token_file)

    def remote(self, wait_for_allow):
        """Open the remote channel, saving a new token. Returns the connected WebSocket."""
        name = base64.b64encode(REMOTE_NAME.encode()).decode()
        token = self.load_token()
        path = '/api/v2/channels/samsung.remote.control?name=' + name + ('&token=' + token if token else '')
        ws = WebSocket(self.host, 8002, path, timeout=wait_for_allow)
        try:
            while True:
                message = json.loads(ws.recv())
                event = message.get('event')
                if event == 'ms.channel.connect':
                    new = (message.get('data') or {}).get('token')
                    if new and new != token:
                        self.save_token(str(new))
                        LOG.info('Saved a new TV token.')
                    return ws
                if event == 'ms.channel.unauthorized':
                    raise PermissionError('The TV denied the remote connection. Allow it on the TV, or check Settings > General > External Device Manager > Device Connection Manager.')
                if event == 'ms.channel.timeOut':
                    raise TimeoutError('The TV Allow prompt timed out.')
        except BaseException:
            ws.close()
            raise

    def launch(self, wait_for_allow=10, route=None):
        """Ask the TV to open the app.

        REST POST brings a running web app to the front on the tested 2020 Q95T; the WebSocket
        DEEP_LINK request was accepted there but left the app in the background. The paired
        WebSocket with NATIVE_LAUNCH is kept as a fallback for TVs that refuse the REST call.
        """
        if route in (None, 'rest'):
            try:
                if self._rest('applications/' + self.app_id, method='POST') is not False:
                    return 'rest'
                LOG.warning('TV refused the REST launch.')
            except (OSError, ValueError, urllib.error.URLError) as error:
                LOG.warning('REST launch failed (%s).', error)
            if route == 'rest':
                raise ConnectionError('REST launch failed.')
        ws = self.remote(wait_for_allow)
        try:
            ws.send(json.dumps({'method': 'ms.channel.emit', 'params': {
                'event': 'ed.apps.launch', 'to': 'host', 'data': {'appId': self.app_id, 'action_type': 'NATIVE_LAUNCH'}}}))
            time.sleep(1)
        finally:
            ws.close()
        return 'websocket'


# ---------- Watch loop ----------
class Watcher:
    def __init__(self, tv, interval=3.0, settle=4.0, attempts=4, retry_gap=4.0, check_gap=0.5, sleep=time.sleep):
        self.tv, self.interval, self.settle, self.attempts, self.retry_gap, self.sleep = tv, interval, settle, attempts, retry_gap, sleep
        self.check_gap = check_gap
        self.previous = None

    def wait_for_app(self):
        """Check the app often during one retry gap.

        True as soon as it is seen on screen: after that a change (such as picking a source) is the
        viewer's choice and must not be undone by another launch. None if the TV never reports
        visibility, otherwise False.
        """
        checks = max(1, int(round(self.retry_gap / self.check_gap))) if self.check_gap > 0 else 1
        answered = False
        for _ in range(checks):
            self.sleep(self.retry_gap / checks)
            visible = self.tv.app_visible()
            if visible:
                return True
            answered = answered or visible is not None
        return False if answered else None

    def on_power_on(self):
        self.sleep(self.settle)
        for attempt in range(1, self.attempts + 1):
            if self.tv.power_state() != 'on':
                LOG.info('TV left the on state before CleanFrame opened.')
                return False
            visible = self.tv.app_visible()
            if visible:
                LOG.info('CleanFrame is on screen.')
                return True
            try:
                # REST first; the last attempt uses the paired WebSocket in case REST is accepted but ignored.
                route = self.tv.launch(route='websocket' if attempt == self.attempts and self.attempts > 1 else None)
                LOG.info('Launch request %d sent (%s).', attempt, route)
            except PermissionError as error:
                LOG.error('%s', error)
                return False
            except (OSError, ValueError, urllib.error.URLError) as error:
                LOG.warning('Launch request %d failed: %s', attempt, error)
            shown = self.wait_for_app()
            if shown:
                LOG.info('CleanFrame is on screen.')
                return True
            if shown is None and visible is None:
                # This TV does not report visibility; one accepted request is all we can confirm.
                return True
        LOG.warning('CleanFrame was not seen on screen after %d launch requests.', self.attempts)
        return False

    def step(self):
        state = self.tv.power_state()
        if self.previous is None:
            LOG.info('Watching; TV is %s. Existing state is left alone.', state)
        elif state != self.previous:
            LOG.info('TV is now %s.', state)
            if state == 'on':
                self.on_power_on()
                state = self.tv.power_state()
        self.previous = state
        return state

    def run(self):
        while True:
            self.step()
            self.sleep(self.interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open CleanFrame whenever the Samsung TV turns on.')
    parser.add_argument('--tv', default=os.environ.get('CLEANFRAME_TV'), help='TV IP address')
    parser.add_argument('--app', default=os.environ.get('CLEANFRAME_APP'), help='CleanFrame application ID, for example ABCDE12345.CleanFrame')
    parser.add_argument('--token-file', default=os.environ.get('CLEANFRAME_TOKEN_FILE', os.path.join(os.path.expanduser('~'), '.config', 'cleanframe-watcher', 'tv-token.json')))
    parser.add_argument('--interval', type=float, default=3.0, help='Seconds between power checks')
    parser.add_argument('--settle', type=float, default=4.0, help='Seconds to wait after power-on before launching')
    parser.add_argument('--pair', action='store_true', help='Connect once and wait up to 60 s for Allow on the TV')
    parser.add_argument('--launch-now', action='store_true', help='Open CleanFrame once and exit')
    parser.add_argument('--status', action='store_true', help='Print the power state and app visibility, then exit')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    if not args.tv or not args.app:
        parser.error('--tv and --app are required (or CLEANFRAME_TV / CLEANFRAME_APP).')
    tv = TV(args.tv, args.app, args.token_file)
    if args.status:
        print(json.dumps({'power': tv.power_state(), 'visible': tv.app_visible(), 'token_saved': bool(tv.load_token())}))
        return 0
    if args.pair:
        LOG.info('Accept "%s" on the TV within 60 seconds.', REMOTE_NAME)
        tv.remote(60).close()
        LOG.info('Paired.' if tv.load_token() else 'Connected, but the TV returned no token.')
        return 0
    if args.launch_now:
        LOG.info('Launch request sent (%s).', tv.launch(wait_for_allow=60))
        return 0
    try:
        Watcher(tv, interval=args.interval, settle=args.settle).run()
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
