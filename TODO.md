# GTK Progress / TODO

Документ синхронизирован с текущим состоянием кода.

## Реализовано

- [x] Архитектурное разделение `contracts.py / core.py / hikvision_plugin.py / timeline.py / ui.py`
- [x] GTK entrypoint и bootstrap приложения
- [x] Сохранение и загрузка `runtime_config.json`
- [x] Первичная диагностика и baseline по `enabled` каналам
- [x] Автодиагностика при старте
- [x] Периодическая автодиагностика
- [x] Автообновление baseline из деградированного состояния в `ONLINE`
- [x] Определение статусов analog/IP каналов через SDK
- [x] Определение ошибок IP камер уровня `ACCOUNT ERROR` / `OFFLINE`
- [x] Вкладки интерфейса: `Онлайн / Архив / Отчёты / Система`
- [x] Загрузка каналов и архива за день
- [x] Подсветка дней архива в календаре
- [x] Вывод archive segments на timeline
- [x] Native archive playback в GTK X11 host window
- [x] Старт playback по выбору файла
- [x] Seek playback по клику на timeline
- [x] Остановка playback
- [x] `pause / resume`
- [x] `speed control`
- [x] `frame step`
- [x] Обновление курсора timeline от текущего playback time
- [x] Явное отображение playback session/state на вкладке `Архив`
- [x] SDK-driven resize видео при изменении размера окна
- [x] Digital crop/zoom через SDK
- [x] Drag-and-drop pan и mouse-wheel zoom
- [x] Capability checks для zoom/pan функций
- [x] Скачивание архива по диапазону времени
- [x] Очередь скачивания для нескольких archive download задач
- [x] Параллельная работа archive download и остальных archive-операций через отдельный download worker
- [x] Отчёт покрытия архива за произвольный период
- [x] Вкладка `Онлайн`: левое боковое меню на `Gtk.Revealer`
- [x] Вкладка `Онлайн`: раздел `Виды`
- [x] Переключение layout/grid presets
- [x] Создание пользовательских видов с закреплением каналов по ячейкам
- [x] Сохранение/загрузка видов в runtime config
- [x] Раздел управления воспроизведением/просмотром на вкладке `Онлайн`
- [x] Кнопки transport control: `Play / Stop / Prev / Next`
- [x] Листание каналов или видов вправо/влево из transport controls
- [x] Кнопка `Скриншоты`
- [x] Запрос snapshot-кадров для каналов текущего вида
- [x] Отображение snapshot-кадров прямо в ячейках grid
- [x] Snapshot focus
- [x] Явное состояние ячейки: `live / snapshot / idle / error`
- [x] Live grid low-res / focus high-res switching
- [x] Разделение baseline и last-current-state в структуре конфига
- [x] Табличный diagnostic diff по каналам

## Текущее состояние

Сейчас приложение уже покрывает четыре пользовательских раздела и пять прикладных сценариев:

- `Система`: первичное подключение, baseline, startup/periodic diagnostics, diff по каналам
- `Архив`: календарь, загрузка дня, timeline, native playback, transport controls, zoom, download queue
- `Онлайн`: live grid/focus, пользовательские виды, sidebar, snapshots, snapshot focus
- `Отчёты`: coverage report по одному каналу за произвольный период

Дополнительные факты по текущей реализации:

- периодическая диагностика пропускается во время активных `live / archive playback / archive download` операций
- очередь скачивания не блокирует загрузку дней архива, календарь и archive playback
- основной backend сейчас один: `HikvisionPlugin`

## Отложено

- [ ] Fullscreen transition для focus mode
- [ ] Отдельный инструмент/панель для вывода `self.status_label`
- [ ] Multi-channel coverage report
- [ ] Export coverage/support reports в machine-readable формат
- [ ] Более богатая diagnostics baseline model: stream profiles, fps/resolution expectations, archive expectations
- [ ] Multi-vendor second plugin
- [ ] Второй backend (`macroscop.py` или аналог)
- [ ] Полноценный support report для техподдержки
- [ ] Расширенный export отчётов
