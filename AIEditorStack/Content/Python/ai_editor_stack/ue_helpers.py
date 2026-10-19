"""Small editor helpers for traps that cost real time in agent sessions (see Docs/AGENT_NOTES.md).

    from ai_editor_stack import ue_helpers as h
    h.sampler_type_for(texture)                       # sampler that compiles for VT / mask / normal textures
    h.export_texture_png(texture, 'C:/out/t.png')     # refuses virtual textures instead of breaking Python
    h.set_decal(decal_actor.decal, material, size=(40, 300, 300), sort_order=3)
    with h.ndisplay_screens_hidden():                 # judge the scene, not the stale nDisplay screen preview
        ...capture...
"""

import contextlib
import os

import unreal


def is_virtual_texture(texture) -> bool:
    try:
        return bool(texture.get_editor_property('virtual_texture_streaming'))
    except Exception:
        return False


def sampler_type_for(texture, normal: bool = False):
    """MaterialSamplerType that matches the texture, so TextureSample / TextureSampleParameter2D nodes compile.
    Wrong types fail with 'Sampler type is Color, should be Virtual Color' or '... should be Virtual Mask'."""
    st = unreal.MaterialSamplerType
    vt = is_virtual_texture(texture)
    compression = texture.get_editor_property('compression_settings')
    if normal or compression == unreal.TextureCompressionSettings.TC_NORMALMAP:
        return st.SAMPLERTYPE_VIRTUAL_NORMAL if vt else st.SAMPLERTYPE_NORMAL
    if compression == unreal.TextureCompressionSettings.TC_MASKS:
        return st.SAMPLERTYPE_VIRTUAL_MASKS if vt else st.SAMPLERTYPE_MASKS
    if compression == unreal.TextureCompressionSettings.TC_GRAYSCALE:
        return st.SAMPLERTYPE_VIRTUAL_GRAYSCALE if vt else st.SAMPLERTYPE_GRAYSCALE
    if texture.get_editor_property('srgb'):
        return st.SAMPLERTYPE_VIRTUAL_COLOR if vt else st.SAMPLERTYPE_COLOR
    return st.SAMPLERTYPE_VIRTUAL_LINEAR_COLOR if vt else st.SAMPLERTYPE_LINEAR_COLOR


def source_file(asset) -> str:
    """First source file the asset was imported from, '' if unknown."""
    try:
        data = asset.get_editor_property('asset_import_data')
        files = data.extract_filenames() if data else []
        return files[0] if files else ''
    except Exception:
        return ''


def export_texture_png(texture, filename: str) -> str:
    """Exports a texture as PNG. Virtual textures are refused: TextureExporterPNG asserts on them and leaves the
    editor's Python broken ('unknown opcode') until the editor restarts."""
    if is_virtual_texture(texture):
        src = source_file(texture)
        hint = f' Its source file is {src}.' if src and os.path.isfile(src) else ''
        raise RuntimeError(f'{texture.get_name()} is a virtual texture; exporting it would break Python in this '
                           f'editor session.{hint} Use the source file, or a non-VT copy of the texture.')
    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
    task = unreal.AssetExportTask()
    task.set_editor_property('object', texture)
    task.set_editor_property('filename', filename)
    task.set_editor_property('automated', True)
    task.set_editor_property('prompt', False)
    task.set_editor_property('replace_identical', True)
    task.set_editor_property('exporter', unreal.TextureExporterPNG())
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError(f'Export of {texture.get_name()} to {filename} failed; see the Output Log.')
    return filename


def set_decal(decal_component, material=None, size=None, sort_order=None):
    """Sets decal material / size / sort order so the change shows up. decal.decal_size = ... raises (read-only
    attribute) and set_editor_property('sort_order', n) does not refresh the render state: a decal set that way can
    still draw below another one with a lower SortOrder. Higher SortOrder draws on top."""
    if material is not None:
        decal_component.set_decal_material(material)
    if size is not None:
        decal_component.set_editor_property('decal_size', unreal.Vector(*size))
    if sort_order is not None:
        decal_component.set_sort_order(int(sort_order))
    return decal_component


@contextlib.contextmanager
def temporarily_hidden(actors):
    """Hides actors in the editor viewports for the duration (transient flag, never saved with the level)."""
    changed = [a for a in actors if a and not a.is_temporarily_hidden_in_editor()]
    for actor in changed:
        actor.set_is_temporarily_hidden_in_editor(True)
    try:
        yield changed
    finally:
        for actor in changed:
            actor.set_is_temporarily_hidden_in_editor(False)


def ndisplay_root_actors() -> list:
    cls = getattr(unreal, 'DisplayClusterRootActor', None)
    if cls is None:
        return []
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    return [a for a in actors if isinstance(a, cls)]


@contextlib.contextmanager
def ndisplay_screens_hidden():
    """A viewport camera placed at an nDisplay eye looks THROUGH the screen meshes, which show a preview render
    that lags behind the scene. Hide the nDisplay actors while capturing to see the real scene."""
    with temporarily_hidden(ndisplay_root_actors()) as hidden:
        yield hidden


def hide_ndisplay_screens() -> list:
    """Same as ndisplay_screens_hidden() for captures made by a separate tool call (capture_image): hide now,
    call show_ndisplay_screens() afterwards. Returns the actor labels it hid."""
    hidden = []
    for actor in ndisplay_root_actors():
        if not actor.is_temporarily_hidden_in_editor():
            actor.set_is_temporarily_hidden_in_editor(True)
            hidden.append(actor.get_actor_label())
    return hidden


def show_ndisplay_screens() -> list:
    shown = []
    for actor in ndisplay_root_actors():
        if actor.is_temporarily_hidden_in_editor():
            actor.set_is_temporarily_hidden_in_editor(False)
            shown.append(actor.get_actor_label())
    return shown
