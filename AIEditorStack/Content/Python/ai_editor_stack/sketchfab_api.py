"""Minimal Sketchfab Data API v3 client (Python stdlib only, no Unreal dependency)."""

import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API = 'https://api.sketchfab.com/v3'
USER_AGENT = 'ue-ai-editor-stack (+https://github.com/metraveer/ue-ai-editor-stack)'
TOKEN_ENV = 'SKETCHFAB_API_TOKEN'
TOKEN_DIALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sketchfab_token_dialog.ps1')
POWERSHELL = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                          'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
TOKEN_HELP = (
    'Call SketchfabTools.open_token_dialog: it opens a window on the user\'s desktop. Ask the user to paste their '
    'token there (https://sketchfab.com/settings/password -> API Token) - never to send it in the chat - and then '
    'call get_account to confirm. In the editor the same window is under Tools > AI Editor Stack > Sketchfab token. '
    f'If the {TOKEN_ENV} environment variable is set, it takes precedence over the saved token.')
NO_TOKEN_MESSAGE = 'No Sketchfab API token on this PC. ' + TOKEN_HELP

_license_slugs: dict = {}


def token_file() -> str:
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'ue-ai-editor-stack', 'sketchfab_token.txt')


def open_token_dialog() -> subprocess.Popen:
    """Starts the token window (sketchfab_token_dialog.ps1) in its own process and returns without waiting.
    The window verifies the token with Sketchfab and saves it to token_file()."""
    if os.name != 'nt':
        raise RuntimeError(f'The token window needs Windows; set the {TOKEN_ENV} environment variable instead.')
    if not os.path.isfile(TOKEN_DIALOG):
        raise RuntimeError(f'Token window script not found: {TOKEN_DIALOG}. Re-run install.ps1.')
    return subprocess.Popen(
        [POWERSHELL, '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', TOKEN_DIALOG,
         '-TokenFile', token_file()],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


def get_token() -> tuple:
    """Returns (token, source); ('', '') when none is configured. Source never contains the token."""
    token = os.environ.get(TOKEN_ENV, '').strip()
    if token:
        return token, f'environment variable {TOKEN_ENV}'
    path = token_file()
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig') as f:
            token = f.read().strip()
        if token:
            return token, path
    return '', ''


def check_uid(uid: str) -> str:
    uid = (uid or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{32}', uid):
        raise ValueError(f'"{uid}" is not a Sketchfab model uid (32 hex characters).')
    return uid


def is_restricted(slug: str) -> bool:
    """True for licenses that forbid commercial use or modification (NC, ND) and for Editorial."""
    parts = (slug or '').split('-')
    return 'nc' in parts or 'nd' in parts or slug == 'ed'


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9]+', '_', name or '').strip('_')[:48]
    return cleaned or 'Model'


def _context() -> ssl.SSLContext:
    return ssl.create_default_context()


def request_json(path_or_url: str, params: dict = None, token: str = '', timeout: float = 30) -> dict:
    url = path_or_url if path_or_url.startswith('http') else API + path_or_url
    query = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    if query:
        url += '?' + urllib.parse.urlencode(query)
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Token ' + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout,
                                    context=_context()) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        if e.code == 401:
            raise RuntimeError('Sketchfab rejected the API token (HTTP 401). ' + TOKEN_HELP) from None
        if e.code == 404:
            raise ValueError('Not found on Sketchfab (HTTP 404). Check the model uid.') from None
        if e.code == 429:
            raise RuntimeError('Sketchfab rate limit reached (HTTP 429). Try again in a minute.') from None
        raise RuntimeError(f'Sketchfab API error HTTP {e.code}: {detail}') from None
    except urllib.error.URLError as e:
        raise RuntimeError(f'Cannot reach Sketchfab: {e.reason}') from None


def license_slugs() -> dict:
    """Maps license uid and label -> slug (search results only carry the uid and label)."""
    if not _license_slugs:
        for lic in request_json('/licenses').get('results', []):
            uid = lic.get('uid') or (lic.get('uri') or '').rstrip('/').rsplit('/', 1)[-1]
            for key in (uid, lic.get('label')):
                if key:
                    _license_slugs[key] = lic.get('slug', '')
    return _license_slugs


def slug_of(license_obj: dict, slugs: dict) -> str:
    lic = license_obj or {}
    return lic.get('slug') or slugs.get(lic.get('uid', ''), '') or slugs.get(lic.get('label', ''), '')


def download_file(url: str, dest: str, progress=None, timeout: float = 60) -> None:
    """Streams url to dest. progress(chunk_bytes, done_bytes, total_bytes) may raise to cancel."""
    tmp = dest + '.part'
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context()) as response, open(tmp, 'wb') as f:
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(len(chunk), done, total)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def extract_gltf(zip_path: str, out_dir: str) -> str:
    """Safely extracts a zipped glTF and returns the path of its .gltf file."""
    root = os.path.realpath(out_dir)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            target = os.path.realpath(os.path.join(root, member))
            if os.path.commonpath([root, target]) != root:
                raise ValueError(f'Unsafe path in archive: {member}')
        archive.extractall(root)
    found = []
    for folder, _, files in os.walk(root):
        found += [os.path.join(folder, f) for f in files if f.lower().endswith('.gltf')]
    if not found:
        raise RuntimeError('The downloaded archive contains no .gltf file.')
    return min(found, key=lambda p: p.count(os.sep))
