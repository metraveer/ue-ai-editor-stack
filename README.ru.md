# UE AI Editor Stack

**Один установщик, после которого Claude Code (или любой MCP-клиент) управляет Unreal Editor 5.8+ во _всех_ проектах, текущих и будущих.**

[English version](README.md)

В UE 5.8 появился экспериментальный родной **Unreal MCP** от Epic, но его, пачку плагинов-тулсетов, автозапуск и подключение AI-клиента нужно настраивать **в каждом проекте**. Этот репозиторий делает всё один раз на уровне движка и добавляет поверх [VibeUE](https://github.com/kevinpbuckley/VibeUE): профилирование, undo-транзакции, пакетный Python, MetaSound, ландшафт, анимацию, импорт с Fab и многое другое.

После установки: открыл любой проект → запустил Claude Code в его папке → «создай Level Sequence с камерой, облетающей выделенный актор».

## Что ставит

| Компонент | Куда | Зачем |
|---|---|---|
| **AIEditorStack** (этот репозиторий) | `<UE>\Engine\Plugins\Marketplace\AIEditorStack` | Плагин только для редактора (`EnabledByDefault`, без C++, ничего не компилируется). Включает во всех проектах Unreal MCP, тулсеты Epic и VibeUE (в собранные игры ничего не попадает) и добавляет свои Python-тулсеты: [`SketchfabTools`](#модели-sketchfab-sketchfabtools). Для отдельного проекта отключается галочкой в *Edit → Plugins* |
| **VibeUE** (сторонний, MIT) | `<UE>\Engine\Plugins\Marketplace\VibeUE` | Собирается из исходников под твою версию движка (1–5 мин) |
| Автозапуск MCP-сервера | `%LOCALAPPDATA%\Unreal Engine\Engine\Config\UserEditorPerProjectUserSettings.ini` | Пользовательский слой конфигов: сервер `127.0.0.1:8000/mcp` стартует с редактором в любом проекте и любой версии UE |
| Claude Code | `~/.claude.json`, `~/.claude/settings.json`, `~/.claude/CLAUDE.md` | MCP-сервер `unreal-mcp` (user scope), официальный плагин Epic `unreal-engine-skills-for-claude-code`, блок правил для агента (действует только в папках с `.uproject`) |

## Требования

- Windows 10/11, Unreal Engine **5.8+** (из Epic Games Launcher, или укажи `-EngineDir`)
- Git и [Claude Code](https://claude.com/claude-code)
- Для сборки VibeUE: **VS 2022 17.14+ (или 2026) с C++ и .NET Framework 4.8 SDK**. Без .NET SDK UBT падает с `Could not find NetFxSDK install dir`

## Установка и переход на новую версию UE

1. Закрой Unreal Editor.
2. В папке репозитория:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   Берётся самая свежая версия UE из Epic Launcher. Для конкретной: `-EngineDir "C:\Program Files\Epic Games\UE_5.9"`.

Скрипт идемпотентный: повторный запуск безопасен и заодно обновляет VibeUE. Флаги: `-SkipVibeUE`, `-SkipClaude`, `-VibeUEBranch <ветка>`, `-Port <n>`.
Тулсеты, которых нет в новой версии движка, скрипт пропускает и сообщает об этом. Новые тулсеты Epic добавляй в `AIEditorStack/AIEditorStack.uplugin`. Хотфиксы (5.8.x) ничего не требуют.

## Модели Sketchfab (SketchfabTools)

Python-тулсет, зарегистрированный в Unreal MCP рядом с тулсетами Epic (`ai_editor_stack.toolsets.sketchfab.SketchfabTools`):

| Инструмент | Что делает |
|---|---|
| `search` | Поиск скачиваемых моделей. По умолчанию скрывает лицензии NonCommercial / NoDerivs / Editorial; фильтры по лицензии, числу треугольников, анимации; сортировка по релевантности, лайкам, просмотрам, дате |
| `get_model` | Подробности и полные условия лицензии |
| `import_model` | Качает GLB (или glTF в zip) с прогресс-окном (можно отменить), импортирует через Interchange в `/Game/Sketchfab/<Модель>_<id>`, ставит ассетам метаданные `Sketchfab.*` и дописывает строку атрибуции в `<Проект>/Credits/Sketchfab.md`. Ограниченные лицензии только с явного согласия (`allow_restricted_license`) |
| `get_account` | Проверка сохранённого токена |

Для поиска ничего не нужно. Для скачивания нужен API-токен бесплатного аккаунта Sketchfab (<https://sketchfab.com/settings/password> → *API Token*):

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\set_sketchfab_token.ps1
```

Ввод скрыт, токен проверяется на Sketchfab и хранится только в `%LOCALAPPDATA%\ue-ai-editor-stack\sketchfab_token.txt` (или задай переменную `SKETCHFAB_API_TOKEN`). Перезапуск редактора не нужен.

Большинство бесплатных моделей под Creative Commons: **CC BY** требует указывать автора, **NC** запрещает коммерческое использование, **ND** запрещает распространять изменённые версии. Храни `Credits/Sketchfab.md` вместе с проектом. Для ассетов Fab / Quixel используй `FabService` из VibeUE (импорт из своей библиотеки работает через вход в Epic).

## Что намеренно не включено

`GameFeaturesToolset` и `ConversationToolset` тянут плагин **GameFeatures**, который в проектах без Game Features при каждом открытии показывает окно *Load Errors* (`GameFeatureData`). Включай их только в проектах на Lyra / Game Features или с CommonConversation (диалоги NPC): в `.uproject` такого проекта с `"TargetAllowList": ["Editor"]`.

## Повседневное

- Открыть проект в UE → Claude Code в папке проекта (CLI или вкладка *Code* десктопного приложения, **локальная** сессия) → `/mcp` → `unreal-mcp: connected`.
- Проверка без AI-клиента (редактор открыт): `.\tools\mcp_check.ps1`
- Порт 8000 занимает только **один** открытый редактор.

## Безопасность

- У Unreal MCP **нет аутентификации**, он слушает только loopback: любой процесс твоего пользователя может управлять редактором. Не открывай порт наружу.
- `execute_python_code` выполняет произвольный Python в редакторе, а VibeUE по умолчанию сразу сохраняет изменённые ассеты (`auto_save=true`). Делай бэкап или коммит проекта перед долгими сессиями.

## Удаление

1. Удалить `<UE>\Engine\Plugins\Marketplace\AIEditorStack` и `...\VibeUE`.
2. Удалить секцию `ModelContextProtocolSettings` из INI выше.
3. `claude mcp remove unreal-mcp -s user` и `claude plugin uninstall unreal-engine-skills-for-claude-code@claude-plugins-official`.
4. Убрать блок `ue-ai-editor-stack` из `~/.claude/CLAUDE.md`.

Проект не связан с Epic Games или Anthropic. Лицензия MIT.
