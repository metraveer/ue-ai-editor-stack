import os
import subprocess
import tempfile
import unittest
import urllib.request
import zipfile
from unittest import mock

import unreal

from toolset_registry.tests.toolset_testcase import ToolCallTestCase
from ai_editor_stack import menu
from ai_editor_stack import sketchfab_api as api
from ai_editor_stack.toolsets.sketchfab import SketchfabTools


def _online() -> bool:
    try:
        urllib.request.urlopen('https://api.sketchfab.com/v3/licenses', timeout=10)
        return True
    except Exception:
        return False


ONLINE = _online()


class SketchfabApiTest(unittest.TestCase):
    """Pure helpers, no network."""

    def test_restricted_licenses(self):
        for slug in ('by-nc', 'by-nd', 'by-nc-sa', 'by-nc-nd', 'ed'):
            self.assertTrue(api.is_restricted(slug), slug)
        for slug in ('cc0', 'by', 'by-sa', 'free-st', 'st'):
            self.assertFalse(api.is_restricted(slug), slug)

    def test_safe_name(self):
        self.assertEqual(api.safe_name('Wooden Chair - 9MB'), 'Wooden_Chair_9MB')
        self.assertEqual(api.safe_name('***'), 'Model')

    def test_check_uid(self):
        self.assertEqual(api.check_uid('F49EF941F78B47D4B401A4191D0D7FDC'), 'f49ef941f78b47d4b401a4191d0d7fdc')
        with self.assertRaises(ValueError):
            api.check_uid('../../me')

    def test_extract_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.zip')
            with zipfile.ZipFile(bad, 'w') as z:
                z.writestr('../evil.gltf', '{}')
            with self.assertRaises(ValueError):
                api.extract_gltf(bad, os.path.join(tmp, 'out'))

    def test_extract_finds_gltf(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, 'good.zip')
            with zipfile.ZipFile(good, 'w') as z:
                z.writestr('scene.gltf', '{}')
                z.writestr('textures/a.png', 'x')
            self.assertTrue(api.extract_gltf(good, os.path.join(tmp, 'out')).endswith('scene.gltf'))

    def test_write_credits_replaces_same_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'Credits', 'Sketchfab.md')
            SketchfabTools._write_credits(path, 'a' * 32, 'first')
            SketchfabTools._write_credits(path, 'a' * 32, 'second')
            SketchfabTools._write_credits(path, 'b' * 32, 'other')
            text = open(path, encoding='utf-8').read()
            self.assertNotIn('first', text)
            self.assertEqual(text.count('second'), 1)
            self.assertIn('other', text)


class SketchfabTokenDialogTest(unittest.TestCase):
    """The token window script and how it is started; the window itself is never shown."""

    def test_script_has_utf8_bom(self):
        with open(api.TOKEN_DIALOG, 'rb') as f:
            self.assertEqual(f.read(3), b'\xef\xbb\xbf', 'Windows PowerShell 5.1 misreads the Cyrillic text without a BOM')

    def test_script_parses(self):
        check = ('$tokens = $null; $errors = $null; '
                 '$null = [Management.Automation.Language.Parser]::ParseFile($env:AIES_DIALOG, [ref]$tokens, [ref]$errors); '
                 '$errors | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }')
        done = subprocess.run([api.POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', check],
                              env={**os.environ, 'AIES_DIALOG': api.TOKEN_DIALOG}, capture_output=True, text=True,
                              timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), '')

    def test_open_starts_detached_window_process(self):
        with mock.patch.object(api.subprocess, 'Popen') as popen:
            api.open_token_dialog()
        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertTrue(os.path.isfile(command[0]), command[0])
        self.assertIn('-STA', command)
        self.assertEqual(command[command.index('-File') + 1], api.TOKEN_DIALOG)
        self.assertEqual(command[command.index('-TokenFile') + 1], api.token_file())
        self.assertTrue(options['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options['stdout'], subprocess.DEVNULL)

    def test_menu_is_registered(self):
        self.assertIsNotNone(unreal.ToolMenus.get().find_menu(menu.SUB_MENU))

    def test_menu_command_opens_window(self):
        with mock.patch.object(api.subprocess, 'Popen') as popen:
            menu.open_sketchfab_token_dialog()
        popen.assert_called_once()


class SketchfabToolsTest(ToolCallTestCase):
    """Tool calls; network tests are skipped when Sketchfab is unreachable."""

    def test_search_rejects_empty_query(self):
        with self.assertToolRaisesRuntimeError():
            SketchfabTools.search('')

    def test_search_rejects_bad_sort(self):
        with self.assertToolRaisesRuntimeError():
            SketchfabTools.search('chair', sort='random')

    def test_get_model_rejects_bad_uid(self):
        with self.assertToolRaisesRuntimeError():
            SketchfabTools.get_model('not-a-uid')

    def test_import_requires_token(self):
        with mock.patch.object(api, 'get_token', return_value=('', '')), self.assertToolRaisesRuntimeError():
            SketchfabTools.import_model('0' * 32)

    def test_get_account_without_token_points_to_dialog(self):
        with mock.patch.object(api, 'get_token', return_value=('', '')), self.assertToolRaisesRuntimeError() as cm:
            SketchfabTools.get_account()
        self.assertIn('open_token_dialog', str(cm.exception))

    def test_open_token_dialog_opens_window(self):
        with mock.patch.object(api.subprocess, 'Popen') as popen:
            SketchfabTools.open_token_dialog()
        popen.assert_called_once()

    def test_open_token_dialog_requires_script(self):
        missing = os.path.join(tempfile.gettempdir(), 'no_such_sketchfab_token_dialog.ps1')
        with mock.patch.object(api, 'TOKEN_DIALOG', missing), self.assertToolRaisesRuntimeError():
            SketchfabTools.open_token_dialog()

    @unittest.skipUnless(ONLINE, 'Sketchfab unreachable')
    def test_search_returns_commercial_models(self):
        page = SketchfabTools.search('chair', count=12)
        self.assertGreater(len(page.models), 0)
        for model in page.models:
            self.assertEqual(len(model.uid), 32)
            self.assertTrue(model.license_slug, f'no license slug for "{model.license_label}"')
            self.assertFalse(api.is_restricted(model.license_slug), model.license_slug)

    @unittest.skipUnless(ONLINE, 'Sketchfab unreachable')
    def test_search_can_include_restricted(self):
        page = SketchfabTools.search('chair', commercial_use_only=False, license_slug='by-nc', count=5)
        self.assertGreater(len(page.models), 0)
        self.assertTrue(all(m.license_slug == 'by-nc' for m in page.models))

    @unittest.skipUnless(ONLINE, 'Sketchfab unreachable')
    def test_search_license_filter(self):
        page = SketchfabTools.search('chair', license_slug='cc0', count=5)
        self.assertGreater(len(page.models), 0)
        self.assertTrue(all(m.license_slug == 'cc0' for m in page.models))

    @unittest.skipUnless(ONLINE, 'Sketchfab unreachable')
    def test_get_model_returns_license_terms(self):
        uid = SketchfabTools.search('chair', license_slug='by', count=3).models[0].uid
        details = SketchfabTools.get_model(uid)
        self.assertEqual(details.model.uid, uid)
        self.assertTrue(details.license_requirements or details.model.license_slug == 'cc0')

    @unittest.skipUnless(ONLINE, 'Sketchfab unreachable')
    def test_import_refuses_restricted_license_without_consent(self):
        page = SketchfabTools.search('chair', commercial_use_only=False, license_slug='by-nc', count=1)
        if not page.models:
            self.skipTest('no by-nc model found')
        with mock.patch.object(api, 'get_token', return_value=('dummy', 'test')), self.assertToolRaisesRuntimeError():
            SketchfabTools.import_model(page.models[0].uid)
