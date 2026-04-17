# GTK Architecture

## Цель

Новая версия приложения должна решать три задачи одновременно:

1. уйти от зависимости на `Qt/PySide6`;
2. уйти от внешней Python-обёртки `hikvision_sdk`;
3. заложить основу для поддержки разных производителей через внутренние контракты и плагины.

GTK-версия рассматривается не как "переписанный Hikvision player", а как
единая платформа видеонаблюдения с несколькими backend-плагинами.

---

## Архитектурные принципы

### 1. UI не знает о vendor SDK

GTK UI не должен знать ничего о:

- `HCNetSDK`
- `PlayCtrl`
- `NET_DVR_*`
- `GStreamer pipeline details`
- `rtsp/http/isapi` URL construction

UI работает только с внутренними контрактами приложения.

### 2. Плагин реализует backend, а не диктует UI

Каждый backend-плагин скрывает свою механику:

- `hikvision_plugin.py`
  - `HCNetSDK`
  - `PlayCtrl`
  - native window binding
  - native zoom/speed/frame-step

- `macroscop.py`
  - `GStreamer`
  - URL/pipeline construction
  - stream profile switching
  - seek/rate through `Gst`

UI получает единое поведение через capability-driven contracts.

### 3. Минимизируем число файлов, но не смешиваем слои

Целевая GTK-версия должна держаться в компактной структуре:

- `contracts.py`
- `core.py`
- `hikvision_plugin.py`
- `macroscop.py`
- `ui.py`
- `timeline.py`

Это минимальный разумный набор файлов.

Нельзя:

- смешивать UI и SDK binding в одном файле;
- смешивать timeline math и vendor-specific playback;
- распиливать проект на большое количество мелких helper-файлов.

### 4. Capability-first design

Backends будут асимметричны:

- Hikvision владеет декодером и рендером через SDK/PlayCtrl;
- другой backend может работать через `GStreamer + gtksink`;
- часть функций будет доступна только у отдельных backend.

Поэтому contracts должны описывать:

- доступные операции;
- доступные возможности (`capabilities`);
- особенности binding video surface.

---

## Целевые режимы приложения

Архитектура строится вокруг режимов, а не вокруг одного архивного playback.

### 1. `Live Grid`

Функции:

- показ нескольких каналов одновременно;
- низкое разрешение / substream для grid;
- обновление статуса online/offline;
- быстрый переход к выбранной камере;
- запрос скриншотов для ячеек grid.

### 2. `Live Focus`

Функции:

- раскрытие выбранной камеры в крупный режим или fullscreen;
- переключение с low-res stream на main/high-res stream;
- zoom;
- возврат в grid с откатом на low-res stream.

### 3. `Archive Playback`

Функции:

- запрос наличия архива;
- список/интервалы архива;
- timeline;
- start/seek/pause/resume;
- speed control;
- frame-step;
- zoom и pan, если backend поддерживает.

### 4. `Diagnostics`

Функции:

- первичная диагностика устройства и каналов;
- чтение текущей конфигурации;
- сравнение с эталонной baseline-конфигурацией;
- формирование диагностического отчёта для техподдержки.

### 5. `Archive Report`

Функции:

- отчёт о наличии архива за произвольный период;
- покрытие по каналам;
- интервалы архива и интервалы дыр;
- проценты покрытия;
- экспорт в текстовый/JSON отчёт.

---

## Внутренние контракты

Контракты должны описывать поведение, нужное UI, а не внутренний API SDK.

### Базовые dataclass

Минимальный набор доменных объектов:

- `ConnectionParams`
- `DeviceInfo`
- `ChannelInfo`
- `StreamProfile`
- `ArchiveSegment`
- `PlaybackRequest`
- `PlaybackState`
- `PluginCapabilities`
- `SnapshotResult`
- `DiagnosticBaseline`
- `DiagnosticDiff`
- `DiagnosticReport`
- `ArchiveCoverageReport`
- `VideoHostBinding`

### Основной plugin contract

Каждый backend должен реализовать единый контракт уровня устройства:

- `connect(params)`
- `disconnect()`
- `get_device_info()`
- `list_channels()`
- `get_capabilities()`

### Live contract

- `start_live(channel_id, profile, host_binding)`
- `stop_live(session_id)`
- `switch_live_profile(session_id, profile)`
- `request_live_snapshot(channel_id | session_id)`

### Archive contract

- `list_archive_days(channel_id, year, month)`
- `list_archive_segments(channel_id, period_start, period_end)`
- `start_archive_playback(request, host_binding)`
- `seek_archive(session_id, target_time)`
- `pause_archive(session_id)`
- `resume_archive(session_id)`
- `set_archive_speed(session_id, rate)`
- `step_archive_frame(session_id)`
- `stop_archive(session_id)`

### Video surface contract

- `bind_surface(host_binding)`
- `resize_surface(session_id, width, height)`
- `set_zoom(session_id, zoom_state)`
- `reset_zoom(session_id)`

### Diagnostics contract

- `read_current_configuration()`
- `compare_with_baseline(baseline)`
- `build_support_report()`

### Coverage/report contract

- `build_archive_coverage_report(channel_ids, period_start, period_end)`

---

## Capability model

Каждый plugin обязан возвращать `PluginCapabilities`.

### Примеры capability-флагов

- `supports_live`
- `supports_archive`
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

UI должен не предполагать возможности backend, а читать их из capability model.

---

## Потоковая модель

GTK-приложение должно быть двухконтурным по исполнению.

### Поток 1: GTK UI thread

Только:

- окно;
- события мыши и клавиатуры;
- drawing timeline;
- layout;
- обновление виджетов;
- отображение статуса.

В этом потоке нельзя выполнять:

- долгие SDK вызовы;
- сетевые запросы;
- диагностику;
- поиск архива;
- запуск/остановку тяжёлых live/archive операций.

### Поток 2: Backend worker thread

Только:

- plugin operations;
- connect/disconnect;
- list channels / archive;
- playback start/stop/seek;
- diagnostics;
- archive coverage report;
- snapshot requests.

Все результаты возвращаются в GTK thread через безопасный механизм:

- `GLib.idle_add(...)`
- либо собственный event dispatcher поверх `queue + idle_add`.

### Внутренние потоки backend

Внутри plugin могут существовать дополнительные внутренние потоки:

- у `HCNetSDK / PlayCtrl`
- у `GStreamer`

Но на уровне приложения основной контракт — это:

- `GTK thread`
- `worker thread`

---

## Live Grid / Focus модель

### Grid

Правила:

- grid запускает каналы в low-res/substream;
- grid не должен грузить main stream на все ячейки;
- grid ячейки должны уметь показать:
  - online/offline;
  - last frame / snapshot;
  - diagnostic warning;
  - stream status.

### Focus / Fullscreen

При раскрытии камеры:

1. backend останавливает или понижает grid-session этой камеры;
2. запускается high-res/main stream;
3. video host переключается в focus/fullscreen;
4. при наличии capabilities включается zoom.

При возврате:

1. high-res session завершается;
2. grid session восстанавливается на low-res.

---

## Timeline архитектура

`timeline.py` — отдельный модуль.

Он не должен зависеть от производителя.

### Что должно жить в `timeline.py`

- модель диапазона времени;
- масштаб и zoom timeline;
- преобразования `time <-> x`;
- hit-testing;
- hover/select/seek logic;
- drawing GTK timeline widget;
- отображение archive segments и current cursor.

### Что НЕ должно жить в `timeline.py`

- запрос архива к устройству;
- vendor SDK вызовы;
- playback control;
- диагностика.

Timeline должен получать уже подготовленные данные:

- `ArchiveSegment[]`
- `current_playback_time`
- `visible bounds`

---

## Архитектура диагностики

Диагностика — отдельный режим приложения.

### Baseline

Baseline-конфигурация должна хранить:

- идентификатор устройства;
- эталонный список каналов;
- expected names;
- expected stream profiles;
- expected resolutions/fps;
- mapping logical channel <-> backend channel id;
- ожидания по архиву.

### Current snapshot

Plugin должен уметь собрать текущее состояние:

- device info;
- channels present/missing;
- online/offline;
- stream params;
- archive availability;
- last errors.

### Diagnostic diff

Сравнение baseline с current snapshot должно показывать:

- отсутствующие каналы;
- новые/лишние каналы;
- mismatch по названиям;
- mismatch по stream profile;
- mismatch по разрешению/FPS;
- offline каналы;
- отсутствие архива там, где он ожидается;
- ошибки соединения и SDK.

### Support report

Отчёт должен быть пригоден для техподдержки:

- human-readable summary;
- структурированный JSON;
- timestamps;
- platform info;
- plugin name/version;
- backend-specific errors.

---

## Архитектура отчёта по архиву

Отчёт по архиву должен быть отдельным use case, а не побочным продуктом timeline.

### Вход

- список каналов;
- произвольный период времени;
- опциональные фильтры.

### Выход

- summary по каждому каналу;
- интервалы наличия архива;
- интервалы отсутствия;
- процент покрытия;
- агрегирование по дням/часам;
- machine-readable report object.

---

## Структура файлов GTK-версии

Целевая файловая структура:

```text
sdk-hik-GTK/
├── app.py
├── contracts.py
├── core.py
├── hikvision_plugin.py
├── macroscop.py
├── timeline.py
├── ui.py
├── ARCHITECTURE.md
└── demo_gtk_timeline.py
```

### Назначение файлов

#### `app.py`

- точка входа;
- bootstrap GTK app;
- инициализация `core`;
- запуск `ui`.

#### `contracts.py`

- dataclasses;
- enums;
- Protocol/ABC;
- capability model.

#### `core.py`

- orchestration layer;
- plugin selection;
- session lifecycle;
- worker thread;
- event bridge в GTK thread;
- сценарии `live/archive/diagnostics/report`.

#### `hikvision_plugin.py`

- Hikvision-specific SDK binding;
- PlayCtrl integration;
- native zoom/render;
- archive/live implementation.

#### `macroscop.py`

- Macroscop-specific backend;
- вероятная реализация через `GStreamer`;
- live/archive/report implementation в рамках тех же contracts.

#### `timeline.py`

- GTK timeline widget;
- timeline model;
- drawing and interaction.

#### `ui.py`

- главное окно;
- grid;
- focus/fullscreen;
- controls;
- diagnostics/report screens;
- интеграция с `core`.

---

## Миграция с Qt-версии

Перенос должен идти не "по файлам", а по слоям.

### Переносимый слой

Из Qt-версии можно переносить:

- playback/business logic;
- архивные запросы;
- часть PlayCtrl integration;
- timeline math;
- domain objects.

### Не переносить напрямую

Не переносить один-в-один:

- Qt widget hierarchy;
- `PySide6` event model;
- старую структуру `main_window.py / native_widget.py / timeline.py` как есть.

### Порядок миграции

1. описать `contracts.py`;
2. собрать `core.py`;
3. вынести `hikvision_plugin.py`;
4. сделать GTK timeline;
5. сделать GTK main UI;
6. добавить diagnostics/report mode;
7. добавить второй backend plugin.

---

## Итоговая целевая схема

```text
GTK UI
   │
   ▼
core.py
   │
   ├── hikvision_plugin.py  -> HCNetSDK + PlayCtrl
   └── macroscop.py         -> GStreamer / vendor transport

timeline.py
   ▲
   │
normalized archive/live state from core
```

Главный результат этой архитектуры:

- UI общий;
- timeline общий;
- backend различается только на plugin-уровне;
- число файлов минимально;
- live/archive/diagnostics/report заложены сразу, а не достраиваются потом поверх старой архитектуры.
