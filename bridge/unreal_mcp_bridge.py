"""stdio <-> HTTP bridge between an MCP client (Claude Code) and the Unreal Editor MCP server.

Claude Code starts this script once per session as the stdio MCP server "unreal-mcp". It answers at once, also while
no editor is running, and forwards the rest to Epic's Unreal MCP server inside the editor
(http://127.0.0.1:8000/mcp by default). Connecting Claude Code to the editor's HTTP endpoint directly breaks in
everyday use: a session started before the editor marks the server as failed after ~30 s of retries and never tries
again, the desktop app's Reconnect then runs an OAuth flow the editor does not implement, and a call that keeps the
editor busy for more than 300 s is aborted. Here:

- Tools are listed from the editor when it answers, else from the last list it returned (a cache in
  %USERPROFILE%\\.ue-ai-editor-stack). When the editor shows up or restarts with other tools, the client gets
  notifications/tools/list_changed and refreshes on its own.
- The editor session is opened lazily and re-opened after the editor restarts ("Unknown session id").
- A call while no editor answers returns a tool error saying so; the next call tries again. No /mcp needed.
- While a call runs, the client gets a progress notification every 20 s, which resets its idle timeout.

Standard library only; install.ps1 runs it with the engine's bundled Python.
"""
import argparse
import http.client
import json
import os
import socket
import sys
import threading
import time

VERSION = '1.5'
STATE_DIR = os.path.join(os.path.expanduser('~'), '.ue-ai-editor-stack')
CACHE_NAME = 'unreal_mcp_tools_cache.json'
CONNECT_TIMEOUT = 2.0      # localhost: refused or accepted almost at once
LIST_TIMEOUT = 8.0         # tools/list and initialize; the editor may be busy loading
CALL_TIMEOUT = 6 * 3600.0  # a tool call may keep the game thread busy for a long time
KEEPALIVE = 20.0
POLL = 3.0                 # how often to look for the editor while the tool list is not from it
DEFAULT_PROTOCOL = '2025-06-18'

_out_lock = threading.Lock()


def log(*parts):
    sys.stderr.write(time.strftime('%H:%M:%S ') + ' '.join(str(p) for p in parts) + '\n')
    sys.stderr.flush()


def send(msg):
    data = (json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    with _out_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


class EditorDown(Exception):
    """Nothing was sent: the editor does not accept connections or refused the session."""


class ConnectionLost(Exception):
    """The request reached the editor, then the connection broke - it may or may not have run."""


class SessionExpired(Exception):
    """The editor no longer knows our session (it restarted)."""


class Cancelled(Exception):
    pass


def _response_for(msgs, mid):
    for m in msgs:
        if isinstance(m, dict) and m.get('id') == mid and ('result' in m or 'error' in m):
            return m
    return None


def _abort(conn):
    """Unblock a thread waiting on this connection (shutdown first: a bare close may not wake recv on Windows)."""
    try:
        if conn.sock:
            conn.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _session_rejected(status, msgs):
    if status == 404:
        return True
    for m in msgs:
        err = m.get('error') if isinstance(m, dict) else None
        if err and 'session' in str(err.get('message', '')).lower() and (
                'unknown' in str(err.get('message', '')).lower() or 'reinitiali' in str(err.get('message', '')).lower()):
            return True
    return False


class Upstream:
    """Streamable-HTTP client for the editor's MCP server, one session at a time."""

    def __init__(self, host, port, path):
        self.host, self.port, self.path = host, port, path
        self.sid = None
        self.protocol = None
        self.init_result = {}
        self.generation = 0          # +1 for every session opened
        self.lock = threading.Lock()
        self._ids = 0

    @property
    def url(self):
        return 'http://%s:%d%s' % (self.host, self.port, self.path)

    def reachable(self):
        try:
            socket.create_connection((self.host, self.port), timeout=0.5).close()
            return True
        except OSError:
            return False

    def _post(self, msg, sid, read_timeout, on_message=None, on_conn=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=CONNECT_TIMEOUT)
        try:
            try:
                conn.connect()
            except OSError as e:
                raise EditorDown(str(e))
            conn.sock.settimeout(read_timeout)
            if on_conn:
                on_conn(conn)
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
            if sid:
                headers['Mcp-Session-Id'] = sid
            if self.protocol:
                headers['MCP-Protocol-Version'] = self.protocol
            try:
                conn.request('POST', self.path, body=json.dumps(msg).encode('utf-8'), headers=headers)
                resp = conn.getresponse()
                new_sid = resp.getheader('Mcp-Session-Id')
                msgs = []
                if 'text/event-stream' in (resp.getheader('Content-Type') or ''):
                    data = []
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        line = line.decode('utf-8', 'replace').rstrip('\r\n')
                        if line.startswith('data:'):
                            data.append(line[5:][1:] if line[5:6] == ' ' else line[5:])
                            continue
                        if line or not data:
                            continue
                        m = json.loads('\n'.join(data))
                        data = []
                        msgs.append(m)
                        if on_message:
                            on_message(m)
                        if 'id' in msg and _response_for([m], msg['id']):
                            break
                    if data:
                        msgs.append(json.loads('\n'.join(data)))
                else:
                    raw = resp.read()
                    if raw.strip():
                        body = json.loads(raw.decode('utf-8', 'replace'))
                        msgs = body if isinstance(body, list) else [body]
                return resp.status, msgs, new_sid
            except (OSError, http.client.HTTPException, ValueError) as e:
                raise ConnectionLost('%s: %s' % (type(e).__name__, e))
        finally:
            conn.close()

    def _open(self, protocol):
        self._ids += 1
        mid = 'aies-bridge-init-%d' % self._ids
        self.protocol = None
        status, msgs, sid = self._post({'jsonrpc': '2.0', 'id': mid, 'method': 'initialize', 'params': {
            'protocolVersion': protocol or DEFAULT_PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'ue-ai-editor-stack-bridge', 'version': VERSION}}}, None, LIST_TIMEOUT)
        res = _response_for(msgs, mid)
        if not res or 'result' not in res:
            raise EditorDown('initialize failed (HTTP %s): %s' % (status, (res or {}).get('error') or msgs))
        self.sid = sid
        self.init_result = res['result']
        self.protocol = res['result'].get('protocolVersion')
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid, LIST_TIMEOUT)
        self.generation += 1
        log('editor session opened:', self.url, 'protocol', self.protocol, 'session', sid)

    def _drop(self, sid):
        with self.lock:
            if self.sid == sid:
                self.sid = None

    def request(self, msg, protocol, read_timeout, on_message=None, on_conn=None):
        """POST a request/notification inside a session; returns (status, msgs). Raises EditorDown/ConnectionLost."""
        for attempt in (1, 2):
            with self.lock:
                if not self.sid:
                    try:
                        self._open(protocol)
                    except ConnectionLost as e:
                        raise EditorDown('initialize: %s' % e)
                sid = self.sid
            try:
                status, msgs, _ = self._post(msg, sid, read_timeout, on_message, on_conn)
            except EditorDown:
                self._drop(sid)
                raise
            # ConnectionLost keeps the session: a cancelled call breaks the connection too, and after a real
            # editor restart the next request is refused (EditorDown) or rejected (reopened below)
            if _session_rejected(status, msgs):
                log('editor rejected session', sid, '- reopening')
                self._drop(sid)
                if attempt == 2:
                    raise EditorDown('the editor rejected a fresh session (HTTP %s)' % status)
                continue
            return status, msgs

    def close(self):
        sid = self.sid
        if not sid:
            return
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=1.0)
            conn.request('DELETE', self.path, headers={'Mcp-Session-Id': sid})
            conn.getresponse().read()
            conn.close()
        except (OSError, http.client.HTTPException):
            pass


class Bridge:
    def __init__(self, upstream, state_dir=STATE_DIR):
        self.up = upstream
        self.cache_path = os.path.join(state_dir, CACHE_NAME)
        self.protocol = None
        self.served = None           # tool list last sent to the client
        self.served_generation = 0   # editor session it came from (0 = cache)
        self.state_lock = threading.Lock()
        self.pending = {}            # request id -> open HTTP connection (for cancellation)
        self.cancelled = set()
        self.poller = None
        self.closing = threading.Event()

    # ---------- cache ----------
    def load_cache(self):
        try:
            with open(self.cache_path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data.get('tools'), list) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def save_cache(self, tools):
        data = {'saved': time.strftime('%Y-%m-%dT%H:%M:%S'), 'url': self.up.url,
                'server': self.up.init_result.get('serverInfo'),
                'instructions': self.up.init_result.get('instructions'), 'tools': tools}
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_path)
        except OSError as e:
            log('could not write the tool cache:', e)

    # ---------- dispatch ----------
    def handle(self, msg):
        if not isinstance(msg, dict) or 'method' not in msg:
            return  # responses to server->client requests: this bridge sends none
        method, mid = msg['method'], msg.get('id')
        if mid is None:
            self.notification(msg)
        elif method == 'initialize':
            self.initialize(msg)
        elif method == 'ping':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {}})
        else:
            target = {'tools/list': self.tools_list, 'tools/call': self.tools_call}.get(method, self.forward)
            threading.Thread(target=self._guarded, args=(target, msg), daemon=True).start()

    def _guarded(self, target, msg):
        try:
            target(msg)
        except Exception as e:  # never leave a request unanswered
            log('internal error in', msg.get('method'), repr(e))
            if msg['id'] not in self.cancelled:
                send({'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32603, 'message': 'bridge error: %r' % e}})

    def initialize(self, msg):
        params = msg.get('params') or {}
        self.protocol = params.get('protocolVersion') or DEFAULT_PROTOCOL
        result = {'protocolVersion': self.protocol,
                  'capabilities': {'tools': {'listChanged': True}},
                  'serverInfo': {'name': 'unreal-mcp (ue-ai-editor-stack bridge)', 'version': VERSION}}
        instructions = self.load_cache().get('instructions')
        if instructions:
            result['instructions'] = instructions
        send({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})

    def notification(self, msg):
        method = msg['method']
        if method == 'notifications/initialized':
            return
        if method == 'notifications/cancelled':
            rid = (msg.get('params') or {}).get('requestId')
            self.cancelled.add(rid)
            conn = self.pending.pop(rid, None)
            if conn is not None:
                log('cancelled request', rid)
                _abort(conn)
        if self.up.sid:
            threading.Thread(target=self._notify_upstream, args=(msg,), daemon=True).start()

    def _notify_upstream(self, msg):
        try:
            self.up.request(msg, self.protocol, LIST_TIMEOUT)
        except (EditorDown, ConnectionLost):
            pass

    # ---------- tools ----------
    def fetch_tools(self):
        """Tool list from the editor (saved to the cache) - raises EditorDown/ConnectionLost."""
        mid = 'aies-bridge-list-%d' % time.time_ns()
        status, msgs = self.up.request({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/list', 'params': {}},
                                       self.protocol, LIST_TIMEOUT)
        res = _response_for(msgs, mid)
        if not res or 'result' not in res:
            raise ConnectionLost('tools/list failed (HTTP %s): %s' % (status, (res or {}).get('error')))
        tools = res['result'].get('tools') or []
        self.save_cache(tools)
        return tools, self.up.generation

    def tools_list(self, msg):
        mid = msg['id']
        if (msg.get('params') or {}).get('cursor'):
            return self.forward(msg)
        try:
            tools, gen = self.fetch_tools()
        except (EditorDown, ConnectionLost) as e:
            tools, gen = self.load_cache().get('tools') or [], 0
            log('editor not reachable (%s) - listing %d cached tools' % (e, len(tools)))
        with self.state_lock:
            self.served, self.served_generation = tools, gen
        send({'jsonrpc': '2.0', 'id': mid, 'result': {'tools': tools}})
        if gen == 0:
            self.start_poller()

    def refresh_tools(self):
        """After the editor (re)connects: tell the client if its tool list is out of date. True if refreshed."""
        try:
            tools, gen = self.fetch_tools()
        except (EditorDown, ConnectionLost) as e:
            log('tool refresh failed:', e)
            return False
        with self.state_lock:
            changed = self.served is not None and tools != self.served
            if self.served is not None:
                self.served, self.served_generation = tools, gen
        if changed:
            log('tool list changed (%d tools) - notifying the client' % len(tools))
            send({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'})
        return True

    def start_poller(self):
        with self.state_lock:
            if self.poller and self.poller.is_alive():
                return
            self.poller = threading.Thread(target=self._poll, daemon=True)
            self.poller.start()

    def _poll(self):
        while not self.closing.is_set():
            if self.served_generation and self.served_generation == self.up.generation:
                return
            if self.up.reachable() and self.refresh_tools():
                return
            self.closing.wait(POLL)

    def _after_success(self):
        if self.served is not None and self.served_generation != self.up.generation:
            threading.Thread(target=self.refresh_tools, daemon=True).start()

    def tools_call(self, msg):
        mid = msg['id']
        params = msg.get('params') or {}
        name = params.get('name', '?')
        token = (params.get('_meta') or {}).get('progressToken')
        done = threading.Event()
        state = {'progress': 0.0}
        started = time.time()

        def progress(value, message):
            if token is None or done.is_set():
                return
            with self.state_lock:
                if value <= state['progress']:
                    return
                state['progress'] = value
            send({'jsonrpc': '2.0', 'method': 'notifications/progress',
                  'params': {'progressToken': token, 'progress': value, 'message': message}})

        def keepalive():
            while not done.wait(KEEPALIVE):
                progress(state['progress'] + 1, 'Unreal Editor is still working on %s (%d s)' % (name, time.time() - started))

        def on_message(m):
            # progress the editor reports while it works (kept monotonic with the keepalive's)
            if isinstance(m, dict) and 'id' not in m and m.get('method') == 'notifications/progress':
                p = m.get('params') or {}
                progress(float(p.get('progress') or 0), p.get('message') or 'Unreal Editor: %s' % name)

        def on_conn(conn):
            self.pending[mid] = conn
            if mid in self.cancelled:
                _abort(conn)

        if token is not None:
            threading.Thread(target=keepalive, daemon=True).start()
        try:
            status, msgs = self.up.request(msg, self.protocol, CALL_TIMEOUT, on_message, on_conn)
            res = _response_for(msgs, mid)
            reply = res or self._tool_error(mid, 'Unreal Editor returned no result for %s (HTTP %s).' % (name, status))
            self._after_success()
        except EditorDown as e:
            log('call', name, '- editor not reachable:', e)
            reply = self._tool_error(mid, (
                'Unreal Editor is not reachable at %s (%s): no editor is running, it is still starting, or its MCP '
                'server is off. Ask the user to open the project in Unreal Editor, then call the tool again - this '
                'bridge reconnects by itself, no /mcp needed. Diagnostics: tools\\mcp_check.ps1 in the '
                'ue-ai-editor-stack repo.') % (self.up.url, e))
        except ConnectionLost as e:
            if mid in self.cancelled:
                return
            log('call', name, '- connection lost:', e)
            reply = self._tool_error(mid, (
                'The connection to Unreal Editor broke while %s was running (%s): the editor was closed, crashed or '
                'restarted. The call may or may not have been applied - check the editor state before repeating it.')
                % (name, e))
        finally:
            done.set()
            self.pending.pop(mid, None)
        if mid not in self.cancelled:
            send(reply)

    def forward(self, msg):
        mid = msg['id']
        try:
            status, msgs = self.up.request(msg, self.protocol, LIST_TIMEOUT)
            res = _response_for(msgs, mid)
            if res:
                return send(res)
            err = 'no response from Unreal Editor (HTTP %s)' % status
        except (EditorDown, ConnectionLost) as e:
            if msg['method'] in ('resources/list', 'resources/templates/list', 'prompts/list'):
                key = {'resources/list': 'resources', 'resources/templates/list': 'resourceTemplates',
                       'prompts/list': 'prompts'}[msg['method']]
                return send({'jsonrpc': '2.0', 'id': mid, 'result': {key: []}})
            err = 'Unreal Editor is not reachable at %s: %s' % (self.up.url, e)
        send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32000, 'message': err}})

    @staticmethod
    def _tool_error(mid, text):
        return {'jsonrpc': '2.0', 'id': mid, 'result': {'content': [{'type': 'text', 'text': text}], 'isError': True}}

    def close(self):
        self.closing.set()
        self.up.close()


def main(argv=None):
    global KEEPALIVE, POLL
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--path', default='/mcp')
    ap.add_argument('--state-dir', default=STATE_DIR)
    ap.add_argument('--keepalive', type=float, default=KEEPALIVE, help=argparse.SUPPRESS)  # tests
    ap.add_argument('--poll', type=float, default=POLL, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    KEEPALIVE, POLL = args.keepalive, args.poll
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    bridge = Bridge(Upstream(args.host, args.port, args.path), args.state_dir)
    log('ue-ai-editor-stack bridge %s: stdio <-> %s' % (VERSION, bridge.up.url))
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode('utf-8'))
        except ValueError:
            log('ignored a line that is not JSON:', raw[:200])
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            bridge.handle(m)
    bridge.close()


if __name__ == '__main__':
    main()
