import json
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from radio import config, control, ui


class MobileControlTests(unittest.TestCase):
    host = 'phone.tailnet.ts.net'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-mobile-')
        self.addCleanup(tmp.cleanup)
        for name, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run')]:
            setting = patch.object(config, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        config.load()
        sender = patch('radio.__main__.send', return_value={'ok': True, 'status': {'state': 'PLAYING'}})
        self.send = sender.start()
        self.addCleanup(sender.stop)
        with patch.object(ui, 'Stream') as stream:
            self.stream = stream.return_value
            self.server = ui.make_server(0)
        self.mobile = ui.ThreadingHTTPServer(('127.0.0.1', 0), self.server.RequestHandlerClass)
        self.mobile.control_host = self.host
        for server in (self.server, self.mobile):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

    def request(self, path, *, host=None, token=None, body=None, server=None):
        server = server or self.mobile
        headers = {'Host': self.host if host is None else host}
        if token is not None:
            headers['X-Radio-Token'] = token
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
                                         data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), error.headers

    def test_phone_receives_the_desktop_ui_and_uncached_token(self):
        code, body, headers = self.request('/')
        self.assertEqual(code, 200)
        expected = ((ui.WEB / 'shell.html').read_text().replace('__TOKEN__', self.server.token).replace('__THEME__', 'terminal')
                    .replace('__LOCAL_CONTROL__', 'false').encode())   # the shell; the theme comes in its frame (?embed=1)
        self.assertEqual(body, expected)
        code, body, _ = self.request('/?theme=terminal&embed=1')
        self.assertEqual(body, ui.theme_page('terminal').replace('__TOKEN__', self.server.token)
                         .replace('__LOCAL_CONTROL__', 'false').encode())
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.mobile.server_address[0], '127.0.0.1')

    def test_commands_require_token_and_exact_configured_host(self):
        for host, token in [(self.host, None), (self.host, 'wrong'),
                            ('evil.example', self.server.token),
                            ('other.tailnet.ts.net', self.server.token)]:
            with self.subTest(host=host, token=bool(token)):
                self.assertEqual(self.request('/api/cmd', host=host, token=token,
                                              body={'cmd': 'next'})[0], 403)
        self.send.assert_not_called()
        self.assertEqual(self.request('/', host='evil.example')[0], 403)
        self.assertEqual(self.request('/', server=self.server)[0], 403)
        self.mobile.control_host = ''
        self.assertEqual(self.request('/')[0], 403)

    def test_phone_controls_the_same_station_and_editor(self):
        for command in ('pause', 'resume', 'next', 'skip-news'):
            with self.subTest(command=command):
                self.send.reset_mock()
                code, body, _ = self.request('/api/cmd', token=self.server.token,
                                              body={'cmd': command})
                self.assertEqual(code, 200)
                self.assertTrue(json.loads(body)['ok'])
                self.assertEqual(self.send.call_args.args[0], command)
        self.send.reset_mock()
        self.request('/api/cmd', token=self.server.token,
                     body={'cmd': 'direction', 'action': 'time', 'id': 'edition-1', 'at': 123})
        self.send.assert_called_once_with('direction', timeout=10, action='time', id='edition-1', at=123)

    def test_station_start_and_stop_are_only_allowed_on_the_local_mac_port(self):
        for command in ('play', 'stop'):
            with self.subTest(command=command):
                self.send.reset_mock()
                code, _, _ = self.request('/api/cmd', token=self.server.token, body={'cmd': command})
                self.assertEqual(code, 403)
                self.send.assert_not_called()
                code, _, _ = self.request('/api/cmd', token=self.server.token, body={'cmd': command},
                                          host=f'127.0.0.1:{self.server.server_port}', server=self.server)
                self.assertEqual(code, 200)
                self.assertEqual(self.send.call_args.args[0], command)

    def test_phone_status_keeps_the_existing_token_guard(self):
        self.assertEqual(self.request('/api/status')[0], 403)
        with patch.object(control, 'timeline', return_value=[]), patch.object(control.plugins, 'listing', return_value=[]), patch.object(control.settings, 'listing', return_value=[]), patch.object(control.ytmusic, 'status', return_value={}):
            code, body, _ = self.request('/api/status', token=self.server.token)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['status']['state'], 'PLAYING')

    def test_local_ui_still_accepts_only_its_local_hosts(self):
        host = f'127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.request('/', host=host, server=self.server)[0], 200)
        self.assertEqual(self.request('/api/cmd', host=host, token=self.server.token,
                                      body={'cmd': 'next'}, server=self.server)[0], 200)

    def test_phone_stream_uses_the_same_stream_as_the_desktop(self):
        self.stream.serve.side_effect = lambda handler: (handler.send_response(200),
                                                        handler.end_headers(),
                                                        handler.wfile.write(b'live-mp3'))
        code, body, _ = self.request('/stream?reconnect=1')
        self.assertEqual((code, body), (200, b'live-mp3'))
        self.stream.serve.assert_called_once()

    def test_configured_phone_port_reuses_handler_and_token(self):
        cfg = config.load()
        cfg['output'].update(listen_port=8766, control_host=self.host)
        factory = ui.ThreadingHTTPServer
        def allocate(address, handler):
            return factory((address[0], 0), handler)
        with patch.object(config, 'load', return_value=cfg), patch.object(ui, 'Stream'), patch.object(ui, 'ThreadingHTTPServer', side_effect=allocate):
            server = ui.make_server(0)
        try:
            self.assertIs(server.listener.RequestHandlerClass, server.RequestHandlerClass)
            self.assertEqual(server.listener.control_host, self.host)
            self.assertEqual(server.listener.server_address[0], '127.0.0.1')
        finally:
            server.listener.shutdown()
            server.listener.server_close()
            server.server_close()


class AutostartTests(unittest.TestCase):
    """The browser output: a Mac page keeps the radio on, so its button only listens."""
    def serve(self, autostart):
        tmp = tempfile.TemporaryDirectory(prefix='radio-autostart-')
        self.addCleanup(tmp.cleanup)
        for name, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run')]:
            setting = patch.object(config, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        config.load()
        for target, value in [('radio.__main__.send', None), ('radio.__main__.start_daemon', None)]:
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        import radio.__main__ as main
        self.start = main.start_daemon
        with patch.object(ui, 'Stream'):
            server = ui.make_server(0, autostart=autostart)
        phone = ui.ThreadingHTTPServer(('127.0.0.1', 0), server.RequestHandlerClass)
        for s in (server, phone):
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        return server, phone

    def get(self, server, path):
        headers = {'Host': f'127.0.0.1:{server.server_port}'}
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}', headers=headers), timeout=5) as r:
            return r.read()

    def started(self):
        for _ in range(50):
            if self.start.called:
                return True
            threading.Event().wait(.02)
        return False

    def test_a_mac_page_starts_a_stopped_radio(self):
        server, _ = self.serve(True)
        self.get(server, '/')
        self.assertTrue(self.started())

    def test_a_poll_starts_it_again_after_the_radio_stopped_itself(self):
        server, _ = self.serve(True)
        headers = {'Host': f'127.0.0.1:{server.server_port}', 'X-Radio-Token': server.token}
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/status', headers=headers), timeout=5) as r:
            status = json.load(r)['status']
        self.assertEqual((status['state'], status['output']), ('STOPPED', 'browser'))
        self.assertTrue(self.started())

    def test_the_phone_and_a_plain_server_never_start_it(self):
        _, phone = self.serve(True)
        self.get(phone, '/')
        self.assertFalse(self.started())
        server, _ = self.serve(False)
        self.get(server, '/')
        self.assertFalse(self.started())


class StreamTests(unittest.TestCase):
    def test_a_new_listener_first_gets_the_last_second_of_the_air(self):
        with patch.object(ui.threading, 'Thread'), patch.object(ui.socket, 'socket'):
            stream = ui.Stream(0)
        stream.beat = Mock()
        stream.sock.recv.side_effect = [b'old', b'one', b'two', OSError]
        clock = iter([0.0, 9.5, 10.0, 10.2, 20.0])  # packets at 0, 9.5 and 10 s; listeners join at 10.2 and 20 s
        with patch.object(ui.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(OSError):
                stream._recv()
            joined, late = stream.add(), stream.add()
        self.assertEqual([joined.get_nowait() for _ in range(joined.qsize())], [b'one', b'two'])
        self.assertTrue(late.empty(), 'the station went quiet since: no old air')

    def test_broken_connection_still_removes_listener(self):
        packets = queue.Queue()
        packets.put(b'packet')
        stream = Mock(spec=ui.Stream)
        stream.add.return_value = packets
        handler = Mock()
        handler.wfile.write.side_effect = BrokenPipeError
        ui.Stream.serve(stream, handler)
        stream.remove.assert_called_once_with(packets)
