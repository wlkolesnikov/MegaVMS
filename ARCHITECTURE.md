# GTK Architecture

## Статус документа

Этот документ приведён к фактическому состоянию `sdk-hik-GTK` в репозитории.
Он описывает:

- что реально реализовано сейчас;
- какие контракты использует код;
- какие части остаются roadmap, а не текущей функциональностью.

Если `TODO.md` и код расходятся с этим документом, источником истины считается код.

---

## Цель

GTK-версия решает три практические задачи:

1. уйти от зависимости на `Qt/PySide6`;
2. работать напрямую через локальный Hikvision backend, без старой внешней Python-обёртки;
3. держать UI и backend разделёнными так, чтобы позже можно было добавить второй vendor plugin.

На текущем этапе это не универсальная multi-vendor платформа, а рабочая GTK-реализация с одним backend `HikvisionPlugin` и заделом под расширение.

---

## Реально реализованные режимы

### 1. `System / Diagnostics`

Реализовано:

- первичное подключение;
- сохранение `runtime_config.json`;
- baseline по `enabled` каналам;
- startup diagnostic;
- periodic diagnostic;
- табличный diff `baseline vs current`;
- сохранение последнего diagnostic snapshot в runtime config.

Текущая диагностика не строит отдельный machine-readable support report и не хранит расширенные device expectations уровня resolution/fps/profile.

### 2. `Archive`

Реализовано:

- выбор канала;
- календарь с подсветкой дней архива;
- загрузка списка файлов за день;
- построение archive segments на timeline;
- native playback в X11 host;
- seek по timeline;
- `pause / resume`;
- speed control;
- frame step;
- polling текущего playback time и синхронизация курсора timeline;
- native zoom/pan через SDK;
- скачивание архива по диапазону времени;
- очередь из нескольких download-задач;
- выделенный download worker, не блокирующий остальные archive-запросы.

Текущий archive API ориентирован на операции по одному каналу и в UI работает с архивом по выбранному дню плюс coverage report по произвольному периоду.

### 3. `Online`

Реализовано:

- live grid;
- live focus;
- profiles `main/sub`;
- запуск grid в `substream`, если backend это поддерживает;
- запуск focus в `main stream`;
- пользовательские `views` с layout presets и сохранением в runtime config;
- боковая панель на `Gtk.Revealer` внутри popover;
- transport controls `Play / Stop / Prev / Next`;
- snapshots для каналов текущего вида;
- отображение snapshot прямо в grid;
- snapshot focus без запуска live focus;
- явные состояния ячейки `live / snapshot / idle / error`.

Не реализовано:

- fullscreen-переход для focus view;
- второй backend plugin.

### 4. `Reports`

Реализовано:

- archive coverage report для одного выбранного канала;
- произвольный период времени;
- human-readable text output.

Не реализовано:

- multi-channel coverage report;
- JSON export;
- отдельный support report.

---

## Фактическая структура файлов

```text
sdk-hik-GTK/
├── app.py
├── contracts.py
├── core.py
├── hikvision_plugin.py
├── timeline.py
├── ui.py
├── TODO.md
└── ARCHITECTURE.md
```

Дополнительного backend файла `macroscop.py` в текущей реализации нет.

### Назначение файлов

#### `app.py`

- точка входа GTK-приложения;
- создаёт `ApplicationCore`;
- поднимает `MainWindow`.

#### `contracts.py`

- реальные dataclass-модели;
- capability model;
- runtime config serialization helpers.

В текущем коде здесь нет `Protocol`/`ABC` и нет абстрактного plugin contract как Python interface.

#### `core.py`

- orchestration layer между UI и plugin;
- фоновые executors;
- возврат результатов в GTK thread через `GLib.idle_add`;
- публичные операции для live/archive/diagnostics/report.

#### `hikvision_plugin.py`

- Hikvision-specific backend;
- HCNetSDK binding;
- archive/live/snapshot/diagnostic implementation;
- native playback, zoom и transport controls.

#### `timeline.py`

- vendor-agnostic GTK timeline widget;
- time-to-pixel math;
- hit-testing;
- drawing archive segments и cursor.

#### `ui.py`

- все вкладки GTK UI;
- live grid/focus;
- archive screen;
- reports screen;
- diagnostics screen;
- download queue.

---

## Фактические доменные модели

Сейчас в `contracts.py` реально используются:

- `ConnectionParams`
- `VideoHostBinding`
- `StreamProfile`
- `ZoomState`
- `PluginCapabilities`
- `ChannelInfo`
- `OnlineView`
- `ArchiveFile`
- `ArchiveSegment`
- `ArchiveDownloadRequest`
- `ArchiveDownloadProgress`
- `ArchiveDownloadResult`
- `RuntimeConfig`
- `DiagnosticReport`
- `DiagnosticState`
- `ArchiveCoverageReport`
- `SnapshotResult`

Из более ранних архитектурных набросков сейчас не существуют как отдельные dataclass:

- `DeviceInfo`
- `PlaybackRequest`
- `PlaybackState`
- `DiagnosticBaseline`
- `DiagnosticDiff`

Их роль закрывается текущими структурами `RuntimeConfig`, `DiagnosticState` и внутренним состоянием `ui.py` / `hikvision_plugin.py`.

---

## Фактический public contract уровня `core.py`

Слой `core.py` сейчас является практическим API для UI.

### Diagnostics / config

- `run_initial_diagnostic(...)`
- `run_saved_diagnostic(...)`
- `load_runtime_config()`
- `save_runtime_config(config)`

### Common

- `get_capabilities()`
- `list_channels(...)`

### Archive

- `list_archive_days(...)`
- `list_archive_files(...)`
- `list_archive_segments(...)`
- `download_archive_by_time(...)`
- `start_archive_playback(...)`
- `stop_archive_playback(...)`
- `seek_archive_playback(...)`
- `pause_archive_playback(...)`
- `resume_archive_playback(...)`
- `set_archive_playback_speed(...)`
- `step_archive_playback_frame(...)`
- `get_archive_playback_time(...)`

### Live

- `start_live(...)`
- `stop_live(...)`
- `switch_live_profile(...)`
- `start_live_preview(...)`
- `stop_live_preview(...)`
- `request_live_snapshot(...)`

### Surface / zoom

- `resize_surface(...)`
- `set_zoom(...)`
- `reset_zoom(...)`

### Reports

- `build_archive_coverage_report(...)`

Это и есть реальный прикладной контракт текущего GTK приложения.

---

## Capability model

`PluginCapabilities` сейчас содержит:

- `supports_live`
- `supports_archive`
- `supports_archive_download`
- `supports_native_surface_binding`
- `supports_grid_low_res_profile`
- `supports_profile_switch`
- `supports_archive_seek`
- `supports_rate_control`
- `supports_frame_step`
- `supports_native_zoom`
- `supports_snapshot`
- `supports_diagnostics`
- `supports_archive_coverage_report`

Для `HikvisionPlugin` capability-значения формируются в [hikvision_plugin.py](/home/klimm/py-hik-sdk-opengl/sdk-hik-GTK/hikvision_plugin.py:2584).

Практическое правило: UI должен включать и выключать поведение на основе capabilities, а не по имени backend.

---

## Потоковая модель

Текущая модель уже не однопоточная в backend-слое.

### GTK UI thread

Здесь остаются только:

- GTK widgets;
- input events;
- timeline drawing;
- status updates;
- переключение экранов и состояний.

### `core.executor`

Основной worker для:

- diagnostics;
- list channels;
- list archive days/files/segments;
- archive playback control;
- live start/stop/switch;
- snapshot requests;
- coverage report.

### `core.download_executor`

Отдельный worker для:

- `download_archive_by_time`;
- очереди download-задач.

Это сделано специально, чтобы длительное скачивание не блокировало:

- подсветку календаря;
- загрузку списка архива за день;
- archive playback;
- coverage report;
- прочие archive/live операции.

### Внутренние потоки backend

Дополнительно внутри SDK могут жить собственные потоки `HCNetSDK` / `PlayCtrl`, но они не являются публичной архитектурой приложения.

---

## Live Grid / Focus модель

### Grid

Текущие правила:

- grid использует `LIVE_PROFILE_SUB`, если backend сообщает `supports_grid_low_res_profile=True`;
- иначе grid падает обратно на `LIVE_PROFILE_MAIN`;
- у каждой ячейки есть собственное состояние:
  - live stream;
  - snapshot;
  - snapshot error;
  - empty slot.

### Focus

Текущая реализация:

1. при открытии live focus для канала grid-session этой камеры останавливается;
2. запускается `LIVE_PROFILE_MAIN`;
3. focus показывает либо live host, либо snapshot view;
4. при закрытии focus grid может быть перезапущен.

Fullscreen в текущем коде отсутствует.

### Snapshots

Snapshots уже являются частью live screen:

- запрашиваются для всех видимых каналов активного вида;
- декодируются в `GdkPixbuf`;
- показываются прямо в grid;
- могут быть открыты в snapshot focus.

---

## Archive screen model

### Calendar / archive discovery

Текущая UI-модель работает так:

- канал выбирается в `Archive` tab;
- по месяцу загружается множество дней с архивом;
- по выбранному дню запрашиваются:
  - список файлов;
  - список segments для timeline.

### Playback

Archive playback сейчас поддерживает:

- start by file selection;
- seek by timeline;
- stop;
- pause/resume;
- speed;
- frame step;
- polling playback time;
- zoom/pan.

### Download queue

Archive download сейчас:

- принимает `ArchiveDownloadRequest`;
- создаёт UI queue;
- выполняет задачи последовательно;
- не монополизирует основной archive worker.

---

## Diagnostics model

Текущая diagnostics-модель уже проще, чем ранняя проектная версия.

### Что хранится в baseline/runtime config

Сейчас сохраняются:

- connection params;
- detected mode;
- `baseline_channels`;
- `current_channels`;
- summary последней диагностики;
- online views и selected view.

### Что не хранится в baseline

Пока не реализованы:

- expected resolutions/fps;
- expected stream profiles;
- logical mapping beyond current channel numbers;
- archive expectations per channel;
- отдельный device info baseline object.

### Current diagnostic output

Сейчас UI показывает:

- текстовый diagnostic summary;
- таблицу diff по каналам;
- baseline/current comparison по status и presence.

---

## Coverage report model

Текущая реализация coverage report уже рабочая, но уже, чем ранняя целевая формулировка.

### Реально поддерживается

- один канал;
- произвольный период;
- общий процент покрытия;
- covered seconds / total seconds;
- число segment'ов;
- список gaps;
- текстовый вывод.

### Пока не поддерживается

- multi-channel aggregation;
- day/hour aggregation output;
- machine-readable export вне текущего Python object;
- отдельные filters в UI.

---

## Conformance notes

Ниже список ключевых расхождений между старой проектной архитектурой и текущим кодом.

### Уже реализовано, но раньше было описано как будущее

- `Gtk.Revealer` sidebar для `Онлайн`;
- screenshots;
- snapshot display в grid;
- snapshot focus;
- grid substream / focus main stream;
- archive download queue;
- разделение worker и download worker.

### Было заявлено слишком широко, но в коде уже есть более узкая реализация

- coverage report сейчас single-channel, а не multi-channel;
- diagnostics baseline хранит channel baseline, а не полную device expectation model;
- contracts сейчас concrete dataclasses, а не ABC/Protocol слой;
- второй backend plugin пока отсутствует.

### Всё ещё roadmap

- fullscreen focus mode;
- отдельный support report/JSON export;
- multi-vendor plugin;
- более богатая diagnostics expectation model.

---

## Практический вывод

На текущий момент GTK-ветка уже является рабочим приложением с пятью реальными сценариями:

- diagnostics;
- archive playback;
- archive download;
- live grid/focus со snapshots;
- archive coverage report.

Архитектурно проект сейчас лучше описывать как:

- один реальный backend `HikvisionPlugin`;
- capability-driven UI;
- concrete contracts в `contracts.py`;
- `core.py` как orchestration facade;
- два backend executors: общий и download-specific;
- roadmap на fullscreen, richer diagnostics и second backend.
