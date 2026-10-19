import os
import socket
import tempfile
import unittest
from unittest import mock

import unreal

from ai_editor_stack import appdata
from ai_editor_stack import mcp_guard
from ai_editor_stack import sketchfab_api as api
from ai_editor_stack import ue_helpers
from ai_editor_stack.toolsets.sketchfab import SketchfabTools

SECTION_TEXT = ('[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]\n'
                'bAutoStartServer=True\nServerPortNumber=8123\nServerUrlPath=/mcp\n')


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


class FakeProfile:
    """Temporary %USERPROFILE% and %LOCALAPPDATA% with an MSIX-redirected store under LOCALAPPDATA\\Packages."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.tmp.name, 'profile')
        self.local = os.path.join(self.profile, 'AppData', 'Local')
        self.redirected_local = os.path.join(self.local, 'Packages', 'Claude_test', 'LocalCache', 'Local')
        os.makedirs(self.local)
        self.env = mock.patch.dict(os.environ, {'USERPROFILE': self.profile, 'LOCALAPPDATA': self.local})
        self.env.start()
        os.environ.pop(api.TOKEN_ENV, None)
        return self

    def __exit__(self, *exc):
        self.env.stop()
        self.tmp.cleanup()


class AppDataTest(unittest.TestCase):
    def test_state_dir_is_outside_appdata(self):
        with FakeProfile() as p:
            self.assertEqual(appdata.state_dir(), os.path.join(p.profile, '.ue-ai-editor-stack'))
            self.assertFalse(appdata.state_dir().startswith(p.local))

    def test_redirected_copies_are_found(self):
        with FakeProfile() as p:
            copy = os.path.join(p.redirected_local, 'ue-ai-editor-stack', 'x.txt')
            _write(copy, 'x')
            self.assertEqual(appdata.redirected_copies(os.path.join('ue-ai-editor-stack', 'x.txt')), [copy])

    def test_current_package_is_a_string(self):
        self.assertIsInstance(appdata.current_package(), str)

    def test_redirect_probe_cleans_up(self):
        with FakeProfile() as p:
            self.assertEqual(appdata._probe_redirect(), '')
            self.assertEqual([f for f in os.listdir(p.local) if f.startswith('.aies-redirect-probe')], [])


class SketchfabTokenLocationTest(unittest.TestCase):
    def test_token_file_is_outside_appdata(self):
        with FakeProfile() as p:
            self.assertTrue(api.token_file().startswith(os.path.join(p.profile, '.ue-ai-editor-stack')))

    def test_token_written_by_a_packaged_app_is_found_and_moved(self):
        with FakeProfile() as p:
            _write(os.path.join(p.redirected_local, 'ue-ai-editor-stack', 'sketchfab_token.txt'), 'abc123\n')
            token, source = api.get_token()
            self.assertEqual(token, 'abc123')
            self.assertIn('moved from', source)
            self.assertNotIn('abc123', source)
            self.assertTrue(os.path.isfile(api.token_file()))

    def test_legacy_localappdata_token_is_found(self):
        with FakeProfile() as p:
            _write(os.path.join(p.local, 'ue-ai-editor-stack', 'sketchfab_token.txt'), 'old')
            self.assertEqual(api.get_token()[0], 'old')

    def test_environment_variable_wins(self):
        with FakeProfile():
            _write(api.token_file(), 'file')
            with mock.patch.dict(os.environ, {api.TOKEN_ENV: 'env'}):
                self.assertEqual(api.get_token(), ('env', f'environment variable {api.TOKEN_ENV}'))

    def test_remove_token_deletes_every_copy(self):
        with FakeProfile() as p:
            copies = [api.token_file(), os.path.join(p.local, 'ue-ai-editor-stack', 'sketchfab_token.txt'),
                      os.path.join(p.redirected_local, 'ue-ai-editor-stack', 'sketchfab_token.txt')]
            for c in copies:
                _write(c, 't')
            self.assertEqual(len(api.remove_token()), 3)
            self.assertEqual(api.get_token(), ('', ''))

    def test_no_token_message_names_the_file(self):
        with FakeProfile():
            self.assertIn(api.token_file(), api.no_token_message())
            self.assertIn('open_token_dialog', api.NO_TOKEN_MESSAGE)


class McpGuardTest(unittest.TestCase):
    def test_read_ini_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, 'a.ini')
            _write(ini, '[Other]\nServerPortNumber=1\n' + SECTION_TEXT + '[Next]\nServerPortNumber=2\n')
            self.assertEqual(mcp_guard.read_ini_value(ini, 'ServerPortNumber'), '8123')
            self.assertIsNone(mcp_guard.read_ini_value(ini, 'Missing'))
            self.assertIsNone(mcp_guard.read_ini_value(os.path.join(tmp, 'none.ini'), 'ServerPortNumber'))

    def test_port_from_command_line(self):
        with mock.patch.object(mcp_guard, '_command_line', return_value='-foo -ModelContextProtocolPort=8765'):
            self.assertEqual(mcp_guard.configured_port(), 8765)

    def test_port_from_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, 'a.ini')
            _write(ini, SECTION_TEXT)
            with mock.patch.object(mcp_guard, '_command_line', return_value=''), \
                    mock.patch.object(mcp_guard, 'config_files', return_value=[ini]):
                self.assertEqual(mcp_guard.configured_port(), 8123)

    def test_opt_out_by_environment(self):
        with mock.patch.dict(os.environ, {mcp_guard.OPT_OUT_ENV: '0'}):
            self.assertIn(mcp_guard.OPT_OUT_ENV, mcp_guard.opt_out_reason())

    def test_opt_out_by_project_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = os.path.join(tmp, 'saved.ini')
            _write(saved, SECTION_TEXT.replace('bAutoStartServer=True', 'bAutoStartServer=False'))
            files = [saved, os.path.join(tmp, 'user.ini'), os.path.join(tmp, 'default.ini')]
            with mock.patch.dict(os.environ, {mcp_guard.OPT_OUT_ENV: ''}), \
                    mock.patch.object(mcp_guard, 'config_files', return_value=files):
                self.assertIn('bAutoStartServer=False', mcp_guard.opt_out_reason())

    def test_commandlets_are_skipped(self):
        with mock.patch.object(mcp_guard, '_command_line', return_value='Project.uproject -run=ResavePackages'):
            self.assertEqual(mcp_guard.skip_reason(), 'commandlet')

    def test_listening_socket_of_this_process_is_seen(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertIn(os.getpid(), mcp_guard.listening_pids(port))
            st = mcp_guard.status(port)
            self.assertEqual(st['state'], 'running_here')
            self.assertTrue(st['owner_images'][0].lower().endswith('.exe'))
        finally:
            listener.close()
        self.assertEqual(mcp_guard.status(port)['state'], 'free')

    def test_repair_user_ini_copies_the_redirected_section(self):
        with FakeProfile() as p, mock.patch.object(appdata, 'appdata_redirect', return_value=''):
            _write(os.path.join(p.redirected_local, mcp_guard.USER_INI_RELATIVE), '; installer\n' + SECTION_TEXT)
            real = mcp_guard.user_ini_path()
            _write(real, '[SomethingElse]\nA=1\n')
            self.assertIn('copied', mcp_guard.repair_user_ini())
            self.assertEqual(mcp_guard.read_ini_value(real, 'ServerPortNumber'), '8123')
            self.assertEqual(mcp_guard.read_ini_value(real, 'A', 'SomethingElse'), '1')
            self.assertEqual(mcp_guard.repair_user_ini(), '', 'second run must not duplicate the section')

    def test_repair_is_skipped_when_this_editor_is_redirected(self):
        with FakeProfile() as p, mock.patch.object(appdata, 'appdata_redirect', return_value='C:/x/LocalCache/Local'):
            _write(os.path.join(p.redirected_local, mcp_guard.USER_INI_RELATIVE), SECTION_TEXT)
            self.assertEqual(mcp_guard.repair_user_ini(), '')

    def test_other_editor_pids_excludes_this_process(self):
        self.assertNotIn(os.getpid(), mcp_guard.other_editor_pids())

    @staticmethod
    def _states(*names):
        it = iter([{'port': 8000, 'state': n, 'owner_pids': [1] if n == 'other_process' else [],
                    'owner_images': ['C:/x/UnrealEditor.exe'] if n == 'other_process' else [], 'this_pid': 2}
                   for n in names])
        return lambda port: next(it)

    def _watchdog(self, *states):
        patches = [mock.patch.object(mcp_guard, 'status', side_effect=self._states(*states)),
                   mock.patch.object(mcp_guard, 'start_server'),
                   mock.patch.object(mcp_guard.unreal, 'register_slate_post_tick_callback', return_value='handle'),
                   mock.patch.object(mcp_guard.unreal, 'unregister_slate_post_tick_callback')]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        return mcp_guard.Watchdog(8000), mocks[1], mocks[3]

    def test_watchdog_starts_the_server_when_the_port_is_free(self):
        dog, start, unregister = self._watchdog('free', 'running_here')
        dog._check(0)
        start.assert_called_once_with(8000)
        dog._check(5)
        unregister.assert_called_once_with('handle')

    def test_watchdog_waits_for_another_editor_then_takes_over(self):
        dog, start, unregister = self._watchdog('other_process', 'other_process', 'free', 'running_here')
        dog._check(0)
        dog._check(10)
        start.assert_not_called()
        dog._check(20)
        start.assert_called_once_with(8000)
        dog._check(25)
        unregister.assert_called_once()

    def test_watchdog_leaves_a_running_server_alone(self):
        dog, start, unregister = self._watchdog('running_here')
        dog._check(0)
        start.assert_not_called()
        unregister.assert_called_once()


class UeHelpersTest(unittest.TestCase):
    def test_sampler_for_engine_textures(self):
        normal = unreal.load_asset('/Engine/EngineMaterials/DefaultNormal')
        st = unreal.MaterialSamplerType
        self.assertIn(ue_helpers.sampler_type_for(normal), (st.SAMPLERTYPE_NORMAL, st.SAMPLERTYPE_VIRTUAL_NORMAL))

    def test_sampler_for_virtual_masks(self):
        tex = mock.MagicMock()
        values = {'virtual_texture_streaming': True, 'srgb': False,
                  'compression_settings': unreal.TextureCompressionSettings.TC_MASKS}
        tex.get_editor_property.side_effect = values.get
        self.assertEqual(ue_helpers.sampler_type_for(tex), unreal.MaterialSamplerType.SAMPLERTYPE_VIRTUAL_MASKS)

    def test_export_refuses_virtual_textures(self):
        tex = mock.MagicMock()
        tex.get_editor_property.side_effect = lambda name: True if name == 'virtual_texture_streaming' else None
        tex.get_name.return_value = 'T_VT'
        with self.assertRaises(RuntimeError) as cm:
            ue_helpers.export_texture_png(tex, os.path.join(tempfile.gettempdir(), 'never.png'))
        self.assertIn('virtual texture', str(cm.exception))


class SketchfabInspectTest(unittest.TestCase):
    def test_empty_meshes_are_deleted_and_unlit_flagged(self):
        empty = mock.MagicMock(spec=unreal.StaticMesh)
        empty.get_num_vertices.return_value = 0
        empty.get_name.return_value = 'Object_2'
        unlit = mock.MagicMock(spec=unreal.MaterialInstanceConstant)
        unlit.get_name.return_value = 'material_0'
        parent = mock.MagicMock()
        parent.get_name.return_value = 'MI_Unlit_Opaque_DS'
        unlit.get_editor_property.return_value = parent
        assets = {'/Game/A/Object_2.Object_2': empty, '/Game/A/material_0.material_0': unlit}
        fake = mock.MagicMock()  # unreal's classes are immutable, so patch the module reference instead
        fake.StaticMesh, fake.MaterialInstance = unreal.StaticMesh, unreal.MaterialInstance
        fake.EditorAssetLibrary.load_asset.side_effect = assets.get
        fake.EditorAssetLibrary.delete_asset.return_value = True
        with mock.patch('ai_editor_stack.toolsets.sketchfab.unreal', fake):
            keep, warnings = SketchfabTools._inspect(list(assets))
        fake.EditorAssetLibrary.delete_asset.assert_called_once_with('/Game/A/Object_2')
        self.assertEqual(keep, ['/Game/A/material_0.material_0'])
        self.assertEqual(len(warnings), 2)
