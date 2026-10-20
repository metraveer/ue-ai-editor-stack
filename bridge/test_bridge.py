"""Tests for unreal_mcp_bridge.py against a fake editor MCP server. No Unreal needed:
    "<Engine>\\Engine\\Binaries\\ThirdParty\\Python3\\Win64\\python.exe" -I bridge\\test_bridge.py
"""
import http.server
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unreal_mcp_bridge.py')
TOOLS = [{'name': 'echo', 'description': 'echo', 'inputSchema': {'type': 'object'}},
         {'name': 'slow', 'description': 'slow', 'inputSchema': {'type': 'object'}}]


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeEditor:
    """Streamable-HTTP MCP server shaped like Epic's: Mcp-Session-Id header, -32600 for unknown sessions."""

    def __init__(self, port, sse=False, tools=TOOLS):
        self.sessions, self.inits, self.calls, self.sse, self.tools = set(), 0, [], sse, tools
        editor = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body=None, headers=None):
                data = b''
                ctype = 'application/json'
                if body is not None:
                    data = json.dumps(body).encode()
                    if editor.sse:
                        ctype, data = 'text/event-stream', b'event: message\ndata: ' + data + b'\n\n'
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_DELETE(self):
                editor.sessions.discard(self.headers.get('Mcp-Session-Id'))
                self._reply(200)

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method, mid = msg.get('method'), msg.get('id')
                if method == 'initialize':
                    editor.inits += 1
                    sid = 'sid-%d' % editor.inits
                    editor.sessions.add(sid)
                    return self._reply(200, {'jsonrpc': '2.0', 'id': mid, 'result': {
                        'protocolVersion': msg['params']['protocolVersion'], 'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'fake-editor'}, 'instructions': 'fake instructions'}},
                        {'Mcp-Session-Id': sid})
                sid = self.headers.get('Mcp-Session-Id')
                if sid not in editor.sessions:
                    return self._reply(400, {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32600, 'message':
                        "Unknown session id '%s' for '%s'; client should reinitialize" % (sid, method)}})
                if mid is None:
                    return self._reply(202)
                if method == 'tools/list':
                    return self._reply(200, {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': editor.tools}})
                if method == 'tools/call':
                    editor.calls.append(msg['params'])
                    args = msg['params'].get('arguments') or {}
                    if msg['params']['name'] == 'slow':
                        time.sleep(args.get('seconds', 1))
                    return self._reply(200, {'jsonrpc': '2.0', 'id': mid, 'result': {
                        'content': [{'type': 'text', 'text': json.dumps(args)}]}})
                self._reply(200, {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'no ' + method}})

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def restart(self):
        """Forget all sessions, like an editor restart on the same port."""
        self.sessions.clear()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class BridgeProc:
    def __init__(self, port, state_dir, *extra):
        self.p = subprocess.Popen([sys.executable, '-I', BRIDGE, '--port', str(port), '--state-dir', state_dir,
                                   '--keepalive', '0.4', '--poll', '0.3'] + list(extra),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q, self.notes, self.err, self.next_id = queue.Queue(), [], [], 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [self.err.append(l) for l in self.p.stderr], daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg) + '\n').encode())
        self.p.stdin.flush()

    def rpc(self, method, params=None, timeout=10.0, mid=None):
        self.next_id += 1
        mid = self.next_id if mid is None else mid
        self.send({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params or {}})
        return self.wait_for(lambda m: m.get('id') == mid, timeout)

    def wait_for(self, pred, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            if pred(m):
                return m
            self.notes.append(m)
        raise AssertionError('timed out; stderr:\n' + b''.join(self.err).decode(errors='replace'))

    def init(self):
        r = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                    'clientInfo': {'name': 't', 'version': '0'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return r

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)
        self.p.stdout.close()
        self.p.stderr.close()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.editor = self.bridge = None

    def tearDown(self):
        if self.bridge:
            self.bridge.close()
        if self.editor:
            self.editor.stop()
        self.tmp.cleanup()

    def write_cache(self, tools):
        with open(os.path.join(self.tmp.name, 'unreal_mcp_tools_cache.json'), 'w', encoding='utf-8') as f:
            json.dump({'tools': tools, 'instructions': 'cached instructions'}, f)

    def test_no_editor_lists_cache_and_call_explains(self):
        self.write_cache(TOOLS[:1])
        self.bridge = BridgeProc(self.port, self.tmp.name)
        r = self.bridge.init()
        self.assertEqual(r['result']['instructions'], 'cached instructions')
        self.assertTrue(r['result']['capabilities']['tools']['listChanged'])
        self.assertEqual([t['name'] for t in self.bridge.rpc('tools/list')['result']['tools']], ['echo'])
        res = self.bridge.rpc('tools/call', {'name': 'echo', 'arguments': {}})['result']
        self.assertTrue(res['isError'])
        self.assertIn('not reachable', res['content'][0]['text'])
        self.assertEqual(self.bridge.rpc('ping'), {'jsonrpc': '2.0', 'id': self.bridge.next_id, 'result': {}})

    def test_forwards_and_caches(self):
        for sse in (False, True):
            with self.subTest(sse=sse):
                self.port = free_port()
                self.editor = FakeEditor(self.port, sse=sse)
                self.bridge = BridgeProc(self.port, self.tmp.name)
                self.bridge.init()
                self.assertEqual(len(self.bridge.rpc('tools/list')['result']['tools']), 2)
                with open(os.path.join(self.tmp.name, 'unreal_mcp_tools_cache.json'), encoding='utf-8') as f:
                    cache = json.load(f)
                self.assertEqual(cache['instructions'], 'fake instructions')
                res = self.bridge.rpc('tools/call', {'name': 'echo', 'arguments': {'a': 'ж'}})['result']
                self.assertEqual(json.loads(res['content'][0]['text']), {'a': 'ж'})
                self.assertEqual(self.editor.inits, 1)
                self.bridge.close()
                self.editor.stop()
                self.bridge = self.editor = None

    def test_editor_restart_reopens_session(self):
        self.editor = FakeEditor(self.port)
        self.bridge = BridgeProc(self.port, self.tmp.name)
        self.bridge.init()
        self.bridge.rpc('tools/list')
        self.editor.restart()
        res = self.bridge.rpc('tools/call', {'name': 'echo', 'arguments': {'x': 1}})['result']
        self.assertNotIn('isError', res)
        self.assertEqual(self.editor.inits, 2)
        self.assertEqual(len(self.editor.calls), 1)  # rejected call was not executed twice

    def test_editor_starting_later_refreshes_tool_list(self):
        self.write_cache(TOOLS[:1])
        self.bridge = BridgeProc(self.port, self.tmp.name)
        self.bridge.init()
        self.assertEqual(len(self.bridge.rpc('tools/list')['result']['tools']), 1)
        self.editor = FakeEditor(self.port)
        self.bridge.wait_for(lambda m: m.get('method') == 'notifications/tools/list_changed', 10)
        self.assertEqual(len(self.bridge.rpc('tools/list')['result']['tools']), 2)

    def test_same_tools_no_notification(self):
        self.write_cache(TOOLS)
        self.bridge = BridgeProc(self.port, self.tmp.name)
        self.bridge.init()
        self.bridge.rpc('tools/list')
        self.editor = FakeEditor(self.port)
        res = self.bridge.rpc('tools/call', {'name': 'echo', 'arguments': {}})['result']
        self.assertNotIn('isError', res)
        time.sleep(1.0)
        self.bridge.rpc('ping')
        self.assertFalse([n for n in self.bridge.notes if n.get('method') == 'notifications/tools/list_changed'])

    def test_long_call_sends_progress(self):
        self.editor = FakeEditor(self.port)
        self.bridge = BridgeProc(self.port, self.tmp.name)
        self.bridge.init()
        res = self.bridge.rpc('tools/call', {'name': 'slow', 'arguments': {'seconds': 1.5},
                                             '_meta': {'progressToken': 7}})['result']
        self.assertNotIn('isError', res)
        prog = [n for n in self.bridge.notes if n.get('method') == 'notifications/progress']
        self.assertGreaterEqual(len(prog), 2)
        self.assertTrue(all(p['params']['progressToken'] == 7 for p in prog))
        values = [p['params']['progress'] for p in prog]
        self.assertEqual(values, sorted(set(values)))

    def test_cancel_drops_reply_and_keeps_running(self):
        self.editor = FakeEditor(self.port)
        self.bridge = BridgeProc(self.port, self.tmp.name)
        self.bridge.init()
        self.bridge.send({'jsonrpc': '2.0', 'id': 500, 'method': 'tools/call',
                          'params': {'name': 'slow', 'arguments': {'seconds': 3}}})
        time.sleep(0.5)
        self.bridge.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 500}})
        res = self.bridge.rpc('tools/call', {'name': 'echo', 'arguments': {'after': True}}, timeout=6)['result']
        self.assertNotIn('isError', res)
        time.sleep(3.0)
        self.bridge.rpc('ping')
        self.assertFalse([n for n in self.bridge.notes if n.get('id') == 500])


if __name__ == '__main__':
    unittest.main(verbosity=2)
