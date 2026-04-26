# MegaVMS GTK

GTK-клиент для Hikvision под Linux/X11.

Этот репозиторий содержит актуальную GTK-реализацию проекта, вынесенную из более крупного локального рабочего дерева. Приложение работает напрямую с нативным Hikvision Linux SDK (`HCNetSDK` + `PlayCtrl`) и сейчас сфокусировано на четырёх практических направлениях:

- системная диагностика и сравнение с базовым состоянием;
- поиск архива и его воспроизведение;
- очередь скачивания архива по диапазону времени;
- live-просмотр в режимах grid/focus со snapshot-кадрами.

## Текущее состояние

Проект уже пригоден для работы, но пока не позиционируется как законченная VMS-платформа с поддержкой нескольких вендоров.

На данный момент реализовано:

- GTK3-интерфейс с вкладками `Online`, `Archive`, `Reports`, `System`
- стартовая и периодическая диагностика
- сохранение runtime-конфигурации
- определение дней архива с отметками в календаре
- archive timeline, native playback, seek, pause/resume, speed, frame-step
- zoom/pan архива через SDK
- очередь скачивания архива с отдельным фоновым worker-потоком
- режимы live grid и live focus
- пользовательские виды и шаблоны раскладки
- получение snapshot-кадров для видимых каналов
- одноканальный отчёт по покрытию архива

Пока не реализовано:

- fullscreen-режим для focus
- multi-channel coverage report
- support report и экспорт в JSON / machine-readable формат
- второй backend другого вендора

## Платформа и требования

Текущая целевая среда:

- Linux
- сессия X11
- Python 3.10+
- GTK 3 через PyGObject
- локально доступные библиотеки Hikvision Linux SDK

Интерфейс использует X11 window binding для нативных видеоповерхностей. Wayland в текущей реализации не является поддерживаемой средой.

Ожидаемая раскладка Hikvision SDK находится в `HIKVISION_LIB_DIR` или, по умолчанию, в `~/.local/lib/hikvision`.

Минимально ожидаемая структура:

```text
~/.local/lib/hikvision/
├── libhcnetsdk.so
├── libPlayCtrl.so
├── libHCCore.so
├── libhpr.so
└── HCNetSDKCom/
```

## Runtime-конфигурация

Приложение хранит своё runtime-состояние в:

```text
.data/runtime_config.json
```

Там сохраняются параметры подключения, базовое и текущее состояние диагностики, а также сохранённые online views.

## Переменные окружения

- `HIKVISION_LIB_DIR`: переопределяет путь к SDK
- `HIK_PLAYER_LOG_LEVEL`: уровень логирования, например `DEBUG` или `INFO`
- `HIK_PLAYER_ARCHIVE_DAYS_FALLBACK_SCAN`: дополнительный backend-флаг для резервного сканирования дней архива

## Быстрый старт

Сначала установите системные зависимости для своей Linux-системы. Минимально нужны Python, GTK3 introspection bindings и файлы Hikvision SDK, перечисленные выше.

Запуск из корня проекта:

```bash
cd sdk-hik-GTK
python3 app.py
```

При старте launcher сам проверяет, что `LD_LIBRARY_PATH` включает каталог Hikvision SDK и `HCNetSDKCom`.

## Структура проекта

```text
.
├── app.py
├── contracts.py
├── core.py
├── hikvision_plugin.py
├── timeline.py
├── ui.py
├── TODO.md
└── ARCHITECTURE.md
```

Роли файлов:

- `app.py`: точка входа GTK и bootstrap runtime environment
- `contracts.py`: доменные модели и capability flags
- `core.py`: orchestration layer и worker executors
- `hikvision_plugin.py`: Hikvision-specific интеграция с SDK
- `timeline.py`: widget временной шкалы архива
- `ui.py`: GTK-экраны и пользовательская логика

## Заметки по разработке

- Сейчас backend только один: `HikvisionPlugin`.
- В `core.py` используется отдельный executor для archive download, чтобы длинные скачивания не блокировали остальную archive-часть интерфейса.
- Код построен по capability-driven модели: интерфейс включает и выключает возможности на основе capability flags, а не по имени backend.

## Документация

Дополнительные документы проекта:

- [ARCHITECTURE.md](ARCHITECTURE.md): текущее описание архитектуры по фактическому коду
- [TODO.md](TODO.md): реализованный scope и отложенные задачи

Эти документы сейчас ближе к реальному состоянию кода, чем более ранние проектные наброски.
