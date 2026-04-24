# GTK Progress / TODO

Текущее состояние зафиксировано для первого прикладного сценария:

- `Система`: первичное подключение, baseline, автодиагностика
- `Архив`: загрузка дня, список файлов, timeline, запуск native playback
- `Отчёты`: coverage report
- `Онлайн`: базовый live grid/focus shell

## Phase 1 scope

- [x] Определить архитектуру GTK-версии
- [x] Выделить первый этап: `Diagnostic + Config + Archive Player`
- [x] Подготовить `contracts.py`
- [x] Подготовить `core.py` с worker thread
- [x] Подготовить `hikvision_plugin.py` как адаптер текущего backend
- [x] Подготовить `timeline.py` для archive timeline
- [x] Подготовить `ui.py` для setup/diagnostic/archive shell
- [x] Подготовить `app.py` как GTK entrypoint
- [x] Сохранение и загрузка runtime config
- [x] Первичная диагностика и формирование baseline/runtime config
- [x] Сохранять в baseline только `enabled` каналы
- [x] Автодиагностика при старте утилиты
- [x] Автодиагностика каждые 10 минут
- [x] Автообновление baseline из деградированного статуса в `ONLINE`
- [x] Определение статусов analog/IP каналов через SDK
- [x] Определение ошибок IP камер уровня `ACCOUNT ERROR` / `OFFLINE`
- [x] Вкладки интерфейса: `Онлайн / Архив / Отчёты / Система`
- [x] Загрузка каналов и архива за день
- [x] Вывод archive segments на timeline
- [x] Native archive playback в GTK X11 host window
- [x] Старт playback по выбору файла
- [x] Seek playback по клику на timeline
- [x] Остановка playback
- [x] Отчёт покрытия архива за произвольный период

## Current status

Сейчас реализовано:

- `runtime_config.json` хранит baseline enabled-каналов и последний diagnostic snapshot
- `Система` показывает diagnostic summary и diff `baseline vs current`
- `Онлайн` умеет:
  - запускать live grid для назначенных каналов
  - переключаться между `grid` и `focus`
  - показывать focus stream в отдельном host
  - возвращать канал из focus обратно в grid
- `Архив` умеет:
  - загрузить архив за день
  - показать список файлов
  - показать сегменты на timeline
  - стартовать native Hikvision playback в GTK host
  - делать seek в пределах найденных файлов
- периодическая диагностика блокируется во время активного archive playback

## Next steps

Приоритет следующего этапа:

- [x] Добавить `pause / resume`
- [x] Добавить `speed control`
- [x] Добавить `frame step`
- [x] Обновлять позицию курсора timeline от текущего playback time
- [x] Явно показывать активный playback session/state на вкладке `Архив`
- [x] Добавить SDK-driven resize видео при изменении размера окна
- [x] Добавить digital crop/zoom через SDK
- [x] Добавить drag-and-drop pan и mouse-wheel zoom
- [x] Добавить capability checks для zoom/pan функций
- [x] Доработать diagnostic diff до отдельной таблицы по каналам
- [x] Разделить baseline и last-current-state в структуре конфига
- [ ] Вкладка `Онлайн`: левое боковое меню на `Gtk.Revealer`
- [ ] Вкладка `Онлайн`: раздел `Виды` в боковом меню
- [ ] Переключение layout/grid presets из раздела `Виды`
- [ ] Создание пользовательских видов с закреплением выбранных каналов по ячейкам
- [ ] Сохранение/загрузка набора видов в runtime config
- [ ] Вкладка `Онлайн`: раздел управления воспроизведением/просмотром
- [ ] Кнопки transport control: `Play / Stop / Prev / Next`
- [ ] Листание каналов или видов вправо/влево из transport controls
- [ ] Кнопка `Скриншоты`: запрос кадров с каналов без запуска full live focus
- [ ] Отображение snapshot-кадров прямо в ячейках grid
- [ ] Явное состояние ячейки: `live / snapshot / idle / error`

## Deferred

- [x] Live mode (basic live preview support added; channel selector on Online tab)
- [ ] Live grid low-res / focus high-res switching
- [ ] Fullscreen transition for focus mode
- [ ] Отдельный инструмент/панель для вывода `self.status_label`
- [ ] Multi-vendor second plugin
