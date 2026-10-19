"""Level editor menu of the UE AI Editor Stack: Tools > AI Editor Stack."""

import unreal

from ai_editor_stack import sketchfab_api

OWNER = 'AIEditorStack'
TOOLS_MENU = 'LevelEditor.MainMenu.Tools'
SUB_MENU = TOOLS_MENU + '.AIEditorStack'


def register() -> None:
    """Adds Tools > AI Editor Stack > Sketchfab token... Safe to call again: entries are replaced by name."""
    menus = unreal.ToolMenus.get()
    tools = menus.extend_menu(TOOLS_MENU)
    sub = tools.add_sub_menu(OWNER, 'AIEditorStack', 'AIEditorStack', 'AI Editor Stack',
                             'Tools of the UE AI Editor Stack plugin')
    entry = unreal.ToolMenuEntry(name='SketchfabToken', type=unreal.MultiBlockType.MENU_ENTRY,
                                 owner=unreal.ToolMenuOwner(OWNER))
    entry.set_label('Sketchfab token…')
    entry.set_tool_tip('Enter, verify or remove the Sketchfab API token used by SketchfabTools to download models')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, '',
                             'from ai_editor_stack import menu; menu.open_sketchfab_token_dialog()')
    sub.add_menu_entry('Sketchfab', entry)
    menus.refresh_all_widgets()


def open_sketchfab_token_dialog() -> None:
    try:
        sketchfab_api.open_token_dialog()
    except Exception as e:
        unreal.log_error(f'AI Editor Stack: cannot open the Sketchfab token window: {e}')
        unreal.EditorDialog.show_message('AI Editor Stack', str(e), unreal.AppMsgType.OK)
