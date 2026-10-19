import unreal

from ai_editor_stack import toolsets
from ai_editor_stack import tests

toolsets._registration.register()

tests._test_runner = unreal.PythonTestRunner.create(
    'AI.Toolsets.AIEditorStack',
    unreal.PythonTestRunnerSearchOptions(root_module=tests.__name__))
