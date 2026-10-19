# AI Editor Stack - notes for agents

Traps that cost real time in agent sessions with this stack (UE 5.8, Unreal MCP, VibeUE), and what to do instead.
Read once per session, before the first editor action. Helpers live in `ai_editor_stack.ue_helpers`.

## Windows / Claude desktop app

- **AppData is redirected for the Claude desktop app.** It is an MSIX package: every *new* file that it or any
  process it starts (its shell, scripts, an editor launched from that shell) creates under `%LOCALAPPDATA%` or
  `%APPDATA%` lands in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\...`. An editor started from the Start menu
  or the Epic launcher never sees it. Symptoms seen so far: MCP auto-start INI ignored, Sketchfab token "missing"
  although `Test-Path` from the shell says it exists.
  - Files the editor must read: create them from the editor (`execute_python_code`) or outside AppData
    (`%USERPROFILE%\.ue-ai-editor-stack`, the project folder).
  - Check the real state from the editor's Python, not from the shell.
  - `ai_editor_stack.appdata.redirected_copies(rel)` lists such copies; `appdata_redirect()` tells whether the
    current process is redirected (probed with a temp file - the app's children report no package identity, so
    `GetCurrentPackageFullName` is no help). A process started through WMI (`Win32_Process.Create`) escapes the
    redirection; `install.ps1` writes its INI that way.
- **Don't start the editor from the shell** unless the user asks: it then inherits the package's AppData view and
  behaves differently from the editor the user starts. Never start a second editor on the same project.

## Unreal MCP server

- Only one editor can own the port (8000). `AIEditorStack` checks a few seconds after startup who listens: it starts
  the server if nobody does, and if another process holds the port it logs `AI Editor Stack: port 8000 is held by
  PID ...` and takes the port over when it frees. Status from Python: `ai_editor_stack.mcp_guard.status()`.
- `ModelContextProtocol.StartServer` **without a port** uses the built-in default, not Project Settings - pass the
  port. After a failed bind Epic's server still reports itself as running; only the TCP listener tells the truth.
- After the editor (re)starts, Claude Code must reconnect: ask the user to run `/mcp` (there is no tool for it).
- Self-test from a terminal: `tools\mcp_check.ps1` (shows which PID serves the port, or why nothing does).
- Don't `importlib.reload` toolset modules (`ai_editor_stack.toolsets.*`): the registered class becomes
  `Invalid Toolset Class` until the editor restarts. Reloading plain helper modules is fine.

## Python in the editor

- `TextureExporterPNG` on a **virtual texture** asserts and leaves Python broken ("unknown opcode") until the
  editor restarts. Use `ue_helpers.export_texture_png()` (refuses VT) or the texture's source file.
  If Python breaks anyway: save through `SlateInspectorToolset` (Ctrl+S on the level tab), then restart.
- `EditorAssetLibrary.load_asset` over hundreds of meshes takes minutes - filter with the asset registry first.
- Editor-only modal dialogs (`EditorDialog.show_message`) are blocked inside `execute_python_code`.
- Viewport FPS from `PerformanceService.frame_timing()` is a single noisy snapshot; compare configurations with
  `start_trace` / `stop_trace` / `analyse("trace", "")` over a few seconds each.

## Materials, decals, textures

- Sampler type must match the texture or the material fails to compile ("should be Virtual Color / Virtual Mask"):
  `ue_helpers.sampler_type_for(texture)`. Megascans/Fab textures are often VT and `TC_Masks`.
- Megascans decal packs `*_DpRA` / `DRO`: **opacity is in the B channel**, not A.
- Decals: `decal.decal_size = ...` raises (read-only attribute) - use `set_editor_property('decal_size', ...)`;
  change SortOrder with `set_sort_order(n)` (higher draws on top) - `set_editor_property('sort_order', n)` does not
  refresh rendering. `ue_helpers.set_decal()` does both.
- glTF / Interchange imports (Sketchfab): material parameters `BaseColorFactor`, `RoughnessFactor`,
  `MetallicFactor`, `BaseColorTexture`; parent `/InterchangeAssets/gltf/Substrate/M_GLTF` (Substrate projects).
  `KHR_materials_unlit` models get an unlit material; some scans import an empty mesh. `import_model` reports both
  in `warnings` and deletes empty meshes.

## Lighting, FX, nDisplay

- A viewport camera at an nDisplay eye looks **through the screen meshes**, whose preview lags behind the scene.
  To judge the real image: `ue_helpers.hide_ndisplay_screens()`, capture, `show_ndisplay_screens()` (transient
  flag, not saved).
- Light functions accept a material instance of a `MD_LightFunction` material. A flickering light makes single
  captures misleading - capture twice or disable the light function while judging.
- Under VRAM pressure ("Video memory has been exhausted") virtual shadow maps can drop pages and shadowed lights
  render black areas; test with shadows off before re-lighting.
- Niagara: systems created from engine templates (`DirectionalBurst`, ...) have **SystemState Loop Behavior =
  Once, 1 s** - set it to Infinite (and the emitter's Life Cycle Mode to Self if it loops on its own), or the
  component deactivates after a second. GPU emitters need fixed bounds. In this setup lit translucent sprites did
  not pick up local lights; an unlit additive mote placed only inside light beams reads better.
- Don't change the user's viewport layout (`set_viewport_layout`) - it resets maximized panes and creates extra
  realtime viewports (memory, FPS).
