# UE AI Editor Stack

**One installer that lets Claude Code (or any MCP client) drive Unreal Editor 5.8+ in _every_ project, current and future.**

[Русская версия](README.ru.md)

Unreal Engine 5.8 ships an experimental, first-party **Unreal MCP** server, but you have to enable it plus a pile of toolset plugins, turn on auto-start and wire up your AI client **per project**. This repo does it once, engine-wide, and adds [VibeUE](https://github.com/kevinpbuckley/VibeUE) on top (profiling, undo transactions, batched Python, MetaSound, landscape, animation, Fab import and more).

After installing: open any project → start Claude Code in its folder → ask _"create a level sequence with a camera orbiting the selected actor"_.

## What it installs

| Component | Where | Why |
|---|---|---|
| **AIEditorStack** (this repo) | `<UE>\Engine\Plugins\Marketplace\AIEditorStack` | An editor-only plugin with `EnabledByDefault: true` (no C++, nothing to compile). It turns on Unreal MCP, Epic's AI toolsets and VibeUE in every project (`TargetAllowList: Editor`, so nothing ends up in packaged games) and adds its own Python toolsets: [`SketchfabTools`](#sketchfab-models-sketchfabtools). Untick it in *Edit → Plugins* to opt a project out |
| **VibeUE** (third-party, MIT) | `<UE>\Engine\Plugins\Marketplace\VibeUE` | Built from source for your exact engine version (1–5 min) |
| MCP server auto-start | `%LOCALAPPDATA%\Unreal Engine\Engine\Config\UserEditorPerProjectUserSettings.ini` | User-level config layer: the server starts with the editor on `127.0.0.1:8000/mcp` in every project and engine version |
| Claude Code wiring | `~/.claude.json`, `~/.claude/settings.json`, `~/.claude/CLAUDE.md` | User-scope MCP server `unreal-mcp`, Epic's official plugin `unreal-engine-skills-for-claude-code`, and a marked block of Unreal rules for the agent (only applies inside folders with a `.uproject`) |

Result on UE 5.8.3: **~90 toolsets** behind Unreal MCP's tool search (`list_toolsets` / `describe_toolset` / `call_tool`) plus VibeUE's direct tools (`execute_python_code`, `capture_image`, `discover_python_*`, …).

## Requirements

- Windows 10/11, Unreal Engine **5.8+** (Epic Games Launcher or `-EngineDir` for other installs)
- Git, and [Claude Code](https://claude.com/claude-code) (optional - other MCP clients can use `http://127.0.0.1:8000/mcp` directly)
- To build VibeUE: **Visual Studio 2022 17.14+ (or 2026) with the C++ workload and the .NET Framework 4.8 SDK**. Without the .NET SDK, UnrealBuildTool fails with `Could not find NetFxSDK install dir`

## Install / move to a new engine version

1. Close Unreal Editor.
2. In the repo folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   It picks the newest engine from the Epic Games Launcher. For a specific one: `-EngineDir "C:\Program Files\Epic Games\UE_5.9"`.

The script is idempotent: re-run it any time (that also updates VibeUE to its latest commit).
Options: `-SkipVibeUE`, `-SkipClaude`, `-VibeUEBranch <branch>`, `-Port <n>`.

If a newer engine lacks a toolset from the list, the installer drops it and tells you (a missing dependency would stop the editor from starting). New Epic toolsets go into `AIEditorStack/AIEditorStack.uplugin`. Hotfix updates (5.8.x → 5.8.y) need nothing.

## Sketchfab models (SketchfabTools)

A Python toolset registered in Unreal MCP next to Epic's own (`ai_editor_stack.toolsets.sketchfab.SketchfabTools`):

| Tool | What it does |
|---|---|
| `search` | Downloadable models by keywords. Hides NonCommercial / NoDerivs / Editorial licenses by default; filters by license, triangle count, animation; sorts by relevance, likes, views or date |
| `get_model` | Details and full license terms |
| `import_model` | Downloads the GLB (or zipped glTF) with a cancellable progress dialog, imports it via Interchange into `/Game/Sketchfab/<Model>_<id>`, tags the assets with `Sketchfab.*` metadata and appends a credit line to `<Project>/Credits/Sketchfab.md`. Restricted licenses require explicit consent (`allow_restricted_license`) |
| `get_account` | Checks the configured token |

Searching needs nothing. Downloading needs the API token of a free Sketchfab account (<https://sketchfab.com/settings/password> → *API Token*):

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\set_sketchfab_token.ps1
```

The input is hidden, the token is verified against Sketchfab and stored only in `%LOCALAPPDATA%\ue-ai-editor-stack\sketchfab_token.txt` (or set `SKETCHFAB_API_TOKEN`). No editor restart needed.

Most free models are Creative Commons: **CC BY** requires crediting the author, **NC** forbids commercial use, **ND** forbids distributing modified versions. Keep `Credits/Sketchfab.md` with your project. For Fab / Quixel assets use VibeUE's `FabService` (owned-library import works with your Epic login).

Tests: `AI.Toolsets.AIEditorStack` in the Session Frontend, or via `AutomationTestToolset`.

## Deliberately left out

`GameFeaturesToolset` and `ConversationToolset` pull in the **GameFeatures** plugin, which pops a *Load Errors* window (`GameFeatureData` asset-manager rule) in every project that doesn't use Game Features. Enable them only in projects built on Lyra / Game Features or using CommonConversation (add to that `.uproject` with `"TargetAllowList": ["Editor"]`).

## Daily use

- Open the project in Unreal Editor → run Claude Code in the project folder (CLI or the *Code* tab of the desktop app, **local** session) → `/mcp` should show `unreal-mcp: connected`.
- Self-test without an AI client (editor open): `.\tools\mcp_check.ps1`
- Only **one** editor instance can own port 8000 at a time.

## Security notes

- Unreal MCP has **no authentication**; it listens on loopback only. Any process of your user on this machine can drive the editor. Don't expose the port.
- `execute_python_code` / `execute_tool_script` run arbitrary Python inside the editor, and VibeUE saves changed packages by default (`auto_save=true`). Back up / commit your project before long agent sessions, and avoid `--dangerously-skip-permissions` on projects you care about.
- Data you send through MCP is "Licensed Technology" under the UE EULA - make sure your LLM provider doesn't train on it.

## Uninstall

1. Delete `<UE>\Engine\Plugins\Marketplace\AIEditorStack` and `...\VibeUE`.
2. Remove the `ModelContextProtocolSettings` section from the INI above.
3. `claude mcp remove unreal-mcp -s user` and `claude plugin uninstall unreal-engine-skills-for-claude-code@claude-plugins-official`.
4. Remove the `ue-ai-editor-stack` block from `~/.claude/CLAUDE.md`.

## Credits

- [Unreal MCP](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor) and the toolsets - Epic Games (experimental in 5.8)
- [VibeUE](https://github.com/kevinpbuckley/VibeUE) - Kevin Buckley, MIT
- [unreal-engine-skills-for-claude-code](https://github.com/EpicGames/unreal-engine-skills-for-claude-code-plugin) - Epic Games

Not affiliated with or endorsed by Epic Games or Anthropic. MIT licensed - see [LICENSE](LICENSE).
