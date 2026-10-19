"""Where the stack keeps per-user state, and how to see past MSIX AppData redirection (Python stdlib only).

Windows redirects files that a packaged (MSIX) app - and every process it starts, e.g. the Claude desktop app and
its shell - creates under %LOCALAPPDATA% / %APPDATA% to %LOCALAPPDATA%\\Packages\\<package>\\LocalCache\\...
Processes outside the package (an Unreal Editor started from the Start menu or the Epic launcher) never see those
files. So the stack keeps its own state outside AppData, in %USERPROFILE%\\.ue-ai-editor-stack, and looks into the
redirected copies only to recover state that ended up there.
"""

import ctypes
import glob
import os
import uuid

STATE_DIR_NAME = '.ue-ai-editor-stack'
LEGACY_DIR_NAME = 'ue-ai-editor-stack'
_APPMODEL_ERROR_NO_PACKAGE = 15700
_redirect = None


def state_dir() -> str:
    """Per-user folder of the stack, outside AppData so no MSIX redirection applies to it."""
    return os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'), STATE_DIR_NAME)


def legacy_state_dir() -> str:
    """Folder used up to version 1.3 (%LOCALAPPDATA%\\ue-ai-editor-stack)."""
    return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), LEGACY_DIR_NAME)


def redirected_copies(relative_to_localappdata: str) -> list:
    """Copies of %LOCALAPPDATA%\\<relative> that packaged apps wrote into their private store, newest first."""
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return []
    pattern = os.path.join(local, 'Packages', '*', 'LocalCache', 'Local', relative_to_localappdata)
    found = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    return sorted(found, key=os.path.getmtime, reverse=True)


def appdata_redirect() -> str:
    """'' if this process writes to the real %LOCALAPPDATA%, else the folder its new files there really land in.
    Probed with a temporary file: children of the Claude desktop app are redirected although
    GetCurrentPackageFullName reports no package for them."""
    global _redirect
    if _redirect is None:
        _redirect = _probe_redirect()
    return _redirect


def _probe_redirect() -> str:
    local = os.environ.get('LOCALAPPDATA')
    if os.name != 'nt' or not local:
        return ''
    name = f'.aies-redirect-probe-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    path = os.path.join(local, name)
    try:
        with open(path, 'w') as f:
            f.write('probe')
    except OSError:
        return ''
    try:
        twins = glob.glob(os.path.join(local, 'Packages', '*', 'LocalCache', 'Local', name))
        return os.path.dirname(twins[0]) if twins else ''
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def current_package() -> str:
    """Full name of the MSIX package this process runs in, '' if none. Not enough on its own to rule out
    AppData redirection - use appdata_redirect() for that."""
    if os.name != 'nt':
        return ''
    try:
        length = ctypes.c_uint32(0)
        get = ctypes.windll.kernel32.GetCurrentPackageFullName
        if get(ctypes.byref(length), None) == _APPMODEL_ERROR_NO_PACKAGE:
            return ''
        buffer = ctypes.create_unicode_buffer(length.value + 1)
        if get(ctypes.byref(length), buffer) != 0:
            return ''
        return buffer.value
    except (AttributeError, OSError):
        return ''
