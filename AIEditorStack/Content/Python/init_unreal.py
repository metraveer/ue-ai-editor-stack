import unreal

from ai_editor_stack import menu
from ai_editor_stack import toolsets
from ai_editor_stack import tests

toolsets._registration.register()

tests._test_runner = unreal.PythonTestRunner.create(
    'AI.Toolsets.AIEditorStack',
    unreal.PythonTestRunnerSearchOptions(root_module=tests.__name__))

menu.register()

try:
    # Starts the Unreal MCP server when Epic's auto-start did not (settings invisible, port taken by another editor)
    from ai_editor_stack import mcp_guard
    mcp_guard.ensure_running()
except Exception as e:  # the guard must never stop the rest of the stack from loading
    unreal.log_warning(f'AI Editor Stack: MCP guard not started: {e}')
