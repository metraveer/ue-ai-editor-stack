# UE AI Editor Stack

**Один установщик, после которого Claude Code (или любой MCP-клиент) управляет Unreal Editor 5.8+ во _всех_ проектах, текущих и будущих.**

[English version](README.md)

В UE 5.8 появился экспериментальный родной **Unreal MCP** от Epic, но его, пачку плагинов-тулсетов, автозапуск и подключение AI-клиента нужно настраивать **в каждом проекте**. Этот репозиторий делает всё один раз на уровне движка и добавляет поверх [VibeUE](https://github.com/kevinpbuckley/VibeUE): профилирование, undo-транзакции, пакетный Python, MetaSound, ландшафт, анимацию, импорт с Fab и многое другое.

После установки: открыл любой проект → запустил Claude Code в его папке → «создай Level Sequence с камерой, облетающей выделенный актор».

## Что ставит

| Компонент | Куда | Зачем |
|---|---|---|
| **AIEditorStack** (этот репозиторий) | `<UE>\Engine\Plugins\Marketplace\AIEditorStack` | Плагин только для редактора (`EnabledByDefault`, без C++, ничего не компилируется). Включает во всех проектах Unreal MCP, тулсеты Epic и VibeUE (в собранные игры ничего не попадает) и добавляет свои Python-тулсеты: [`SketchfabTools`](#модели-sketchfab-sketchfabtools), с меню *Tools → AI Editor Stack*. Для отдельного проекта отключается галочкой в *Edit → Plugins* |
| **VibeUE** (сторонний, MIT) | `<UE>\Engine\Plugins\Marketplace\VibeUE` | Собирается из исходников под твою версию движка (1–5 мин) |
| Автозапуск MCP-сервера | `%LOCALAPPDATA%\Unreal Engine\Engine\Config\UserEditorPerProjectUserSettings.ini` + защита MCP в плагине | Пользовательский слой конфигов: сервер `127.0.0.1:8000/mcp` стартует с редактором в любом проекте и любой версии UE. Если не стартовал (настройка не видна, порт занят), `AIEditorStack` запускает его сам через несколько секунд или забирает порт, когда другой редактор закроется |
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
| `open_token_dialog` | Открывает на рабочем столе окно ввода токена (см. ниже) и сразу возвращается |

Для поиска ничего не нужно. Для скачивания нужен API-токен бесплатного аккаунта Sketchfab (<https://sketchfab.com/settings/password> → *API Token*). Он вводится в маленьком окне, которое открывается любым из способов:

- в редакторе: **Tools → AI Editor Stack → Sketchfab token…**;
- агентом: если токена нет, Claude вызывает `open_token_dialog` и просит вставить токен в окно (никогда не в чат), затем проверяет его через `get_account`;
- из папки репозитория:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\tools\set_sketchfab_token.ps1
  ```
  (`-Console` — старый скрытый ввод в консоли, `-Remove` — удалить токен).

Ввод скрыт (галочка *Показать* его открывает). *Сохранить* проверяет токен на Sketchfab (`GET /v3/me`), показывает имя аккаунта или понятную ошибку и хранит токен только в `%USERPROFILE%\.ue-ai-editor-stack\sketchfab_token.txt` (специально вне AppData, см. *Если что-то не работает*; токен версии 1.3 из `%LOCALAPPDATA%` переносится туда сам); *Удалить токен* стирает все копии. Переменная окружения `SKETCHFAB_API_TOKEN`, если задана, важнее сохранённого токена. Перезапуск редактора не нужен. Окно — `AIEditorStack/Content/Python/ai_editor_stack/sketchfab_token_dialog.ps1` (WinForms, отдельный процесс, редактор не блокируется; на русской Windows интерфейс на русском).

### Первый запуск по шагам

1. Открой проект в Unreal Editor, запусти Claude Code в папке проекта и проверь, что `/mcp` показывает `unreal-mcp: connected`.
2. Попроси модель обычными словами, например:
   > Найди на Sketchfab деревянный стул, лицензия CC0, до 10 000 треугольников

   Claude покажет найденное: автора, лицензию, число треугольников, размер GLB и ссылку на sketchfab.com (по ней модель можно покрутить в 3D).
3. Выбери модель:
   > Импортируй второй вариант в /Game/Props

   В первый раз токена ещё нет, поэтому Claude откроет окно ввода токена и попросит его заполнить. Возьми токен на <https://sketchfab.com/settings/password> → *API Token* (нужен бесплатный аккаунт), вставь, нажми *Сохранить*, дождись имени аккаунта и напиши Claude «готово». Он проверит токен через `get_account` и продолжит. Токен вставляй только в окно, не в чат.
4. Редактор покажет прогресс скачивания (его можно отменить). Ассеты появятся в `/Game/Props/<Модель>_<id>` с метаданными `Sketchfab.*`, а строка атрибуции — в `<Проект>/Credits/Sketchfab.md`.

Другие запросы:

| Что пишешь | Что происходит |
|---|---|
| «Покажи ещё варианты» | Следующая страница того же поиска |
| «Найди анимированного робота для коммерческой игры, до 50 тысяч треугольников, сначала самые популярные» | `search` с `animated_only`, `max_face_count` и сортировкой по лайкам |
| «Покажи полные условия лицензии этой модели» | `get_model` |
| «Некоммерческая лицензия подойдёт, это учебный проект — поищи ещё раз» | Поиск с лицензиями NC / ND; перед импортом такой модели Claude покажет условия и дождётся твоего явного согласия |
| «Какой аккаунт Sketchfab подключён?» | `get_account` |
| «Открой окно токена Sketchfab, хочу сменить токен» | `open_token_dialog` |

Большинство бесплатных моделей под Creative Commons: **CC BY** требует указывать автора, **NC** запрещает коммерческое использование, **ND** запрещает распространять изменённые версии. Храни `Credits/Sketchfab.md` вместе с проектом. Для ассетов Fab / Quixel используй `FabService` из VibeUE (импорт из своей библиотеки работает через вход в Epic).

## Что намеренно не включено

`GameFeaturesToolset` и `ConversationToolset` тянут плагин **GameFeatures**, который в проектах без Game Features при каждом открытии показывает окно *Load Errors* (`GameFeatureData`). Включай их только в проектах на Lyra / Game Features или с CommonConversation (диалоги NPC): в `.uproject` такого проекта с `"TargetAllowList": ["Editor"]`.

## Повседневное

- Открыть проект в UE → Claude Code в папке проекта (CLI или вкладка *Code* десктопного приложения, **локальная** сессия) → `/mcp` → `unreal-mcp: connected`.
- Проверка без AI-клиента (редактор открыт): `.\tools\mcp_check.ps1` — если сервер не отвечает, покажет, какой процесс держит порт.
- Порт 8000 занимает только **один** открытый редактор. Второй пишет в лог `AI Editor Stack: port 8000 is held by PID ...` и забирает порт, когда первый закроется. После перезапуска редактора переподключи клиент (`/mcp` в Claude Code).
- Для агентов: `AIEditorStack/Docs/AGENT_NOTES.md` — известные ловушки (экспорт VT-текстур ломает Python, порядок декалей, типы сэмплеров VT, настройки цикла в шаблонах Niagara, устаревшее превью nDisplay), хелперы — в `ai_editor_stack.ue_helpers`.

## Если что-то не работает

- **MCP не подключается, хотя редактор открыт.** Ищи строки `AI Editor Stack:` в `<Проект>\Saved\Logs\<Проект>.log`. Крайний вариант — набрать в консоли редактора `ModelContextProtocol.StartServer 8000` (без номера порта команда берёт порт по умолчанию, а не из настроек). Отключить защиту: переменная окружения `AIES_MCP_AUTOSTART=0` или `bAutoStartServer=False` в `EditorPerProjectUserSettings.ini` проекта.
- **Настройки или токен Sketchfab «пропадают», если их записал Claude.** Десктопное приложение Claude — MSIX-пакет: всё новое, что оно или запущенные из него процессы создают в `%LOCALAPPDATA%` / `%APPDATA%`, Windows складывает в `%LOCALAPPDATA%\Packages\Claude_*\LocalCache`. Редактор, запущенный из меню Пуск, этих файлов не видит. С версии 1.4 установщик пишет INI процессом вне пакета, плагин при старте переносит такую секцию MCP в настоящий INI, а токен хранится вне AppData. Редактор, запущенный из оболочки Claude, видит перенаправленные файлы и может вести себя иначе — запускай редактор сам.

## Безопасность

- У Unreal MCP **нет аутентификации**, он слушает только loopback: любой процесс твоего пользователя может управлять редактором. Не открывай порт наружу.
- `execute_python_code` выполняет произвольный Python в редакторе, а VibeUE по умолчанию сразу сохраняет изменённые ассеты (`auto_save=true`). Делай бэкап или коммит проекта перед долгими сессиями.

## Удаление

1. Удалить `<UE>\Engine\Plugins\Marketplace\AIEditorStack` и `...\VibeUE`.
2. Удалить секцию `ModelContextProtocolSettings` из INI выше.
3. `claude mcp remove unreal-mcp -s user` и `claude plugin uninstall unreal-engine-skills-for-claude-code@claude-plugins-official`.
4. Убрать блок `ue-ai-editor-stack` из `~/.claude/CLAUDE.md`.
5. Удалить папку `%USERPROFILE%\.ue-ai-editor-stack` (и `%LOCALAPPDATA%\ue-ai-editor-stack` от версии 1.3 и старше): в ней токен Sketchfab (`sketchfab_token.txt`), а также исходники и сборка VibeUE. Чтобы убрать только токен, нажми *Удалить токен* в окне токена или запусти `.\tools\set_sketchfab_token.ps1 -Remove`. Импортированные модели и `Credits/Sketchfab.md` остаются в проектах.

Проект не связан с Epic Games или Anthropic. Лицензия MIT.
