"""Keeps Epic's Unreal MCP server reachable in every interactive editor session.

Epic's plugin starts the server only if bAutoStartServer is set, and gives up for good when the port is taken:

* bAutoStartServer lives in %LOCALAPPDATA%\\Unreal Engine\\Engine\\Config\\UserEditorPerProjectUserSettings.ini.
  When install.ps1 ran from a packaged (MSIX) app such as the Claude desktop app, Windows redirected that file into
  the app's private AppData, so an editor started from the Start menu never sees it and the server stays off.
* With two editors open, the second one fails to bind the port ("HttpListener unable to bind to 127.0.0.1:8000")
  and never retries, even after the first editor closes. Its server object still reports itself as running.

The guard checks which process really listens on the port. If nobody does, it starts the server
(ModelContextProtocol.StartServer <port>); if another process does, it says which one and takes the port over as
soon as it is free. Once this editor owns the port the guard stops watching, so a server the user stops on purpose
stays stopped. Opt out with the environment variable AIES_MCP_AUTOSTART=0 or bAutoStartServer=False in the
project's EditorPerProjectUserSettings.ini.
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import socket
import struct
import time

import unreal

from ai_editor_stack import appdata

LOG = 'AI Editor Stack: '
SECTION = '/Script/ModelContextProtocolEngine.ModelContextProtocolSettings'
DEFAULT_PORT = 8000
DEFAULT_PATH = '/mcp'
USER_INI_RELATIVE = os.path.join('Unreal Engine', 'Engine', 'Config', 'UserEditorPerProjectUserSettings.ini')
OPT_OUT_ENV = 'AIES_MCP_AUTOSTART'

FIRST_CHECK_DELAY = 4.0      # seconds after startup: Epic's own auto-start has run by then
CHECK_INTERVAL = 10.0        # while another process holds the port
SLOW_INTERVAL = 60.0         # after repeated failed start attempts
MAX_FAST_ATTEMPTS = 3

_watchdog = None


# ---------------------------------------------------------------- configuration

def _command_line() -> str:
    try:
        return unreal.SystemLibrary.get_command_line() or ''
    except Exception:
        return ''


def _full(path: str) -> str:
    return unreal.Paths.convert_relative_path_to_full(path)


def user_ini_path() -> str:
    """The global per-user INI as the editor sees it (the real one unless the editor itself runs packaged)."""
    return os.path.join(os.environ.get('LOCALAPPDATA', ''), USER_INI_RELATIVE)


def config_files() -> list:
    """INI files that may hold Unreal MCP settings, most specific first."""
    saved = _full(unreal.Paths.project_saved_dir())
    project = _full(unreal.Paths.project_config_dir())
    return [os.path.join(saved, 'Config', 'WindowsEditor', 'EditorPerProjectUserSettings.ini'),
            user_ini_path(),
            os.path.join(project, 'DefaultEditorPerProjectUserSettings.ini')]


def read_ini_value(path: str, key: str, section: str = SECTION):
    """Last value of key in [section] of an UE ini file, or None. Understands the +/-/. array prefixes."""
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    value, inside = None, False
    for raw in lines:
        line = raw.strip()
        if line.startswith('['):
            inside = line == f'[{section}]'
            continue
        if inside and '=' in line and not line.startswith(';'):
            k, v = line.split('=', 1)
            if k.strip().lstrip('+-.!') == key:
                value = v.strip()
    return value


def setting(key: str, files=None):
    for path in files or config_files():
        value = read_ini_value(path, key)
        if value is not None:
            return value, path
    return None, None


def configured_port() -> int:
    match = re.search(r'-ModelContextProtocolPort=(\d+)', _command_line(), re.IGNORECASE)
    if match and 1 <= int(match.group(1)) <= 65535:
        return int(match.group(1))
    value, _ = setting('ServerPortNumber')
    try:
        port = int(value) if value is not None else DEFAULT_PORT
    except ValueError:
        port = DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def configured_path() -> str:
    value, _ = setting('ServerUrlPath')
    return value or DEFAULT_PATH


def opt_out_reason() -> str:
    """Why the guard must not start the server, '' if it may."""
    env = os.environ.get(OPT_OUT_ENV, '').strip().lower()
    if env in ('0', 'false', 'no', 'off'):
        return f'environment variable {OPT_OUT_ENV}={env}'
    files = config_files()
    project_files = [files[0], files[2]]
    value, path = setting('bAutoStartServer', project_files)
    if value is not None and value.lower() in ('false', '0'):
        return f'bAutoStartServer={value} in {path}'
    return ''


def skip_reason() -> str:
    """Sessions where an MCP server makes no sense: commandlets, unattended runs, non-editor processes."""
    cmd = _command_line().lower()
    if re.search(r'(^|\s)-run=', cmd):
        return 'commandlet'
    try:
        if unreal.SystemLibrary.is_unattended():
            return 'unattended session'
    except Exception:
        pass
    if hasattr(unreal, 'is_editor') and not unreal.is_editor():
        return 'not an editor session'
    return ''


# ---------------------------------------------------------------- who listens on the port

class _TcpRowOwnerPid(ctypes.Structure):
    _fields_ = [('state', wt.DWORD), ('local_addr', wt.DWORD), ('local_port', wt.DWORD),
                ('remote_addr', wt.DWORD), ('remote_port', wt.DWORD), ('owning_pid', wt.DWORD)]


class _ProcessEntry(ctypes.Structure):  # PROCESSENTRY32W
    _fields_ = [('size', wt.DWORD), ('usage', wt.DWORD), ('pid', wt.DWORD), ('heap', ctypes.c_size_t),
                ('module', wt.DWORD), ('threads', wt.DWORD), ('parent', wt.DWORD), ('priority', ctypes.c_long),
                ('flags', wt.DWORD), ('exe', ctypes.c_wchar * 260)]


_win = {}


def _api():
    """Private WinDLL instances with explicit signatures (so other ctypes users in the editor are unaffected)."""
    if not _win and os.name == 'nt':
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessEntry)]
        k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessEntry)]
        ip = ctypes.WinDLL('iphlpapi')
        ip.GetExtendedTcpTable.restype = wt.DWORD
        ip.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL, wt.ULONG,
                                           ctypes.c_int, wt.ULONG]
        _win.update(kernel32=k32, iphlpapi=ip)
    return _win


def listening_pids(port: int) -> set:
    """PIDs listening on 127.0.0.1:port or 0.0.0.0:port (IPv4, as Unreal's HTTP server binds)."""
    if os.name != 'nt':
        return set()
    get_table = _api()['iphlpapi'].GetExtendedTcpTable
    size = wt.DWORD(0)
    tcp_table_owner_pid_listener, af_inet = 3, 2
    get_table(None, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value + 4096)
        size = wt.DWORD(ctypes.sizeof(buffer))
        if get_table(buffer, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0) == 0:
            break
    else:
        return set()
    count = wt.DWORD.from_buffer(buffer).value
    rows = (_TcpRowOwnerPid * count).from_buffer(buffer, ctypes.sizeof(wt.DWORD))
    pids = set()
    for row in rows:
        if socket.ntohs(row.local_port & 0xFFFF) != port:
            continue
        address = socket.inet_ntoa(struct.pack('<I', row.local_addr))
        if address in ('127.0.0.1', '0.0.0.0'):
            pids.add(int(row.owning_pid))
    return pids


def process_image(pid: int) -> str:
    """Executable path of a process, '' if it cannot be read."""
    if os.name != 'nt' or not pid:
        return ''
    kernel32 = _api()['kernel32']
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        length = wt.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return buffer.value
        return ''
    finally:
        kernel32.CloseHandle(handle)


def other_editor_pids() -> list:
    """PIDs of other UnrealEditor.exe processes of any project (Toolhelp snapshot)."""
    if os.name != 'nt':
        return []
    kernel32 = _api()['kernel32']
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    found = []
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(_ProcessEntry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.exe.lower() == 'unrealeditor.exe' and entry.pid != os.getpid():
                found.append(int(entry.pid))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def status(port: int = 0) -> dict:
    """{'port', 'state': 'running_here' | 'other_process' | 'free', 'owner_pids', 'owner_images', 'this_pid'}."""
    port = port or configured_port()
    owners = listening_pids(port)
    me = os.getpid()
    state = 'running_here' if me in owners else ('other_process' if owners else 'free')
    return {'port': port, 'state': state, 'owner_pids': sorted(owners),
            'owner_images': [process_image(p) for p in sorted(owners)], 'this_pid': me}


def start_server(port: int) -> None:
    world = None
    try:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    except Exception:
        pass
    unreal.SystemLibrary.execute_console_command(world, f'ModelContextProtocol.StartServer {port}')


# ---------------------------------------------------------------- the real per-user INI

def repair_user_ini() -> str:
    """If install.ps1 ran inside an MSIX app, its MCP section landed in the app's private AppData. Copy that section
    into the real per-user INI (only from an editor whose own AppData writes are not redirected, e.g. one started
    from the Start menu rather than from Claude's shell). Returns what was done, or ''."""
    if appdata.appdata_redirect():
        return ''
    real = user_ini_path()
    if read_ini_value(real, 'bAutoStartServer') is not None:
        return ''
    for copy in appdata.redirected_copies(USER_INI_RELATIVE):
        if read_ini_value(copy, 'bAutoStartServer') is None:
            continue
        with open(copy, encoding='utf-8-sig', errors='replace') as f:
            text = f.read()
        match = re.search(r'^\[' + re.escape(SECTION) + r'\][^\[]*', text, re.MULTILINE)
        if not match:
            continue
        block = match.group(0).strip()
        os.makedirs(os.path.dirname(real), exist_ok=True)
        existing = ''
        if os.path.isfile(real):
            with open(real, encoding='utf-8-sig', errors='replace') as f:
                existing = f.read()
        with open(real, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write((existing.rstrip() + '\n\n' if existing.strip() else '') + block + '\n')
        return f'copied the Unreal MCP settings from {copy} to {real}'
    return ''


# ---------------------------------------------------------------- watchdog

class Watchdog:
    def __init__(self, port: int):
        self.port = port
        self.started = time.monotonic()
        self.next_check = self.started + FIRST_CHECK_DELAY
        self.attempts = 0
        self.reported_owner = None
        self.handle = unreal.register_slate_post_tick_callback(self._tick)

    def stop(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None

    def _tick(self, _delta):
        now = time.monotonic()
        if now < self.next_check:
            return
        try:
            self._check(now)
        except Exception as e:  # never let a guard error break the editor tick
            unreal.log_warning(f'{LOG}MCP guard stopped after an error: {e}')
            self.stop()

    def _check(self, now):
        st = status(self.port)
        url = f'http://127.0.0.1:{self.port}{configured_path()}'
        if st['state'] == 'running_here':
            how = 'started by AI Editor Stack' if self.attempts else 'auto-started'
            unreal.log(f'{LOG}Unreal MCP server is listening on {url} ({how}, PID {st["this_pid"]}).')
            self.stop()
            return
        if st['state'] == 'other_process':
            owner = ', '.join(f'PID {p} ({os.path.basename(i) or "?"})' for p, i in zip(st['owner_pids'], st['owner_images']))
            if owner != self.reported_owner:
                unreal.log_warning(
                    f'{LOG}port {self.port} is held by {owner}, so this editor has no MCP server yet. Only one editor '
                    f'at a time can serve {url}; this one takes the port over as soon as it is free.')
                self.reported_owner = owner
            self.next_check = now + CHECK_INTERVAL
            return
        if self.attempts >= MAX_FAST_ATTEMPTS:
            if self.attempts == MAX_FAST_ATTEMPTS:
                unreal.log_error(f'{LOG}could not start the Unreal MCP server on port {self.port} after '
                                 f'{MAX_FAST_ATTEMPTS} attempts; see LogHttpListener above. Retrying every minute.')
            self.attempts += 1
            start_server(self.port)
            self.next_check = now + SLOW_INTERVAL
            return
        self.attempts += 1
        unreal.log(f'{LOG}Unreal MCP server is not listening on port {self.port}; starting it '
                   f'(attempt {self.attempts}).')
        start_server(self.port)
        self.next_check = now + 2.0


def ensure_running():
    """Entry point from init_unreal.py."""
    global _watchdog
    reason = skip_reason()
    if reason:
        return None
    try:
        repaired = repair_user_ini()
        if repaired:
            unreal.log(f'{LOG}{repaired}.')
    except Exception as e:
        unreal.log_warning(f'{LOG}could not check the per-user Unreal MCP settings: {e}')
    reason = opt_out_reason()
    if reason:
        unreal.log(f'{LOG}not starting the Unreal MCP server ({reason}).')
        return None
    path = configured_path()
    if path != DEFAULT_PATH:
        unreal.log_warning(f'{LOG}ServerUrlPath is {path}; ModelContextProtocol.StartServer always serves '
                           f'{DEFAULT_PATH}, so a server started by the guard listens on {DEFAULT_PATH}.')
    others = other_editor_pids()
    if others:
        unreal.log_warning(f'{LOG}{len(others)} other Unreal Editor process(es) running (PID '
                           f'{", ".join(map(str, others))}). Two editors on the same project overwrite each '
                           f'other\'s changes, and only one of them can own the MCP port.')
    if _watchdog:
        _watchdog.stop()
    _watchdog = Watchdog(configured_port())
    return _watchdog
