# GTK Progress / TODO

Текущее состояние зафиксировано для первого прикладного сценария:

- `Система`: первичное подключение, baseline, автодиагностика
- `Архив`: загрузка дня, список файлов, timeline, запуск native playback
- `Отчёты`: coverage report
- `Онлайн`: пока shell

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
- `Архив` умеет:
  - загрузить архив за день
  - показать список файлов
  - показать сегменты на timeline
  - стартовать native Hikvision playback в GTK host
  - делать seek в пределах найденных файлов
- периодическая диагностика блокируется во время активного archive playback

## Next steps

Приоритет следующего этапа:

- [ ] Добавить `pause / resume`
- [ ] Добавить `speed control`
- [ ] Добавить `frame step`
- [ ] Обновлять позицию курсора timeline от текущего playback time
- [ ] Явно показывать активный playback session/state на вкладке `Архив`
- [ ] Доработать diagnostic diff до отдельной таблицы по каналам
- [ ] Разделить baseline и last-current-state в структуре конфига

## Deferred

- [ ] Live mode
- [ ] Live grid low-res / fullscreen high-res switching
- [ ] Snapshot mode for grid
- [ ] Multi-vendor second plugin
