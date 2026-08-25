# Genshin Navigator

MVP пассивно оценивает позицию игрока по изображению миникарты. Программа получает
обычный снимок экрана через Windows, вырезает настроенную область и сопоставляет её
с пользовательским эталонным изображением карты.

Проект **не** читает память Genshin, не внедряет код, не ставит хуки, не отправляет
нажатия и не управляет игрой.

## Что выдаёт MVP

- позицию с регионом, слоем и явно указанной системой координат;
- нормализованную позицию от `0` до `1`;
- оценку поворота и масштаба миникарты;
- уверенность, число совпадений и диагностическую причину при неудаче.

## Быстрый старт (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m pip install -e .
Copy-Item config.example.json config.json
```

Проверенная среда: Python 3.12, NumPy 2.5.2, OpenCV 4.14.0, Pillow 11.3.0
и pywebview 6.2.1 на Windows.
`pyproject.toml` оставляет совместимые диапазоны для обычной установки, а
`requirements-lock.txt` воспроизводит среду, на которой выполнен baseline.

### Фонтейн с нуля

После установки следующий путь последовательно получает официальный атлас и
подземные этажи, строит итоговую пирамиду, загружает POI, запускает тесты и трекер:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_atlas.py `
  datasets\local\references\hoyolab_fontaine_full_n1 `
  --zoom N1 --x 32:43 --y 12:23

.venv\Scripts\python scripts\fetch_hoyolab_underground.py `
  datasets\local\references\hoyolab_fontaine_underground --preset fontaine

.venv\Scripts\python scripts\build_underground_pyramid.py `
  datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\surface_pyramid.json `
  datasets\local\references\hoyolab_fontaine_underground\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\pyramid.json

.venv\Scripts\python scripts\fetch_hoyolab_poi.py `
  datasets\local\poi\fontaine.json `
  --surface-metadata datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  --underground-metadata datasets\local\references\hoyolab_fontaine_underground\metadata.json

.venv\Scripts\genshin-navigator sync-data --config config.json
.venv\Scripts\genshin-navigator data-status --config config.json

.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\genshin-navigator track --config config.json
```

Карты и POI остаются в `datasets/local` и не входят в Git. Перед первым запуском
проверьте ROI под своё разрешение экрана; остальные актуальные параметры уже есть
в `config.example.json`.

### Локальные данные и обновление POI

По умолчанию используется `storage_backend: auto`. При первом запуске существующие
`fontaine.json` и `poi_progress.json` импортируются в
`datasets/local/data/genshin_navigator.db`, после чего трекер читает каталог и
прогресс только из SQLite. Интернет для запуска и навигации не требуется.

Обновление из публичной интерактивной карты выполняется отдельно:

```powershell
.venv\Scripts\genshin-navigator sync-data --config config.json
.venv\Scripts\genshin-navigator data-status --config config.json
```

`sync-data` сначала загружает и проверяет весь новый каталог, а затем заменяет
рабочий снимок одной транзакцией. При ошибке сети, неизвестном слое или повреждённом
ответе прежние данные остаются рабочими. Версия контента вычисляется по фактическому
ответу HoYoLAB; для воспроизводимого снимка можно явно передать `--map-version`.

Тяжёлые карты и изображения в SQLite не записываются. База хранит только POI,
метрики, прогресс, версии и пути к локальным assets. Временный аварийный режим можно
включить через `data.storage_backend: json`; после появления рабочей SQLite-базы
автоматического молчаливого отката к JSON нет.

### Синхронизация прогресса HoYoLAB

Локальная SQLite-база остаётся главным состоянием Navigator. Подключение HoYoLAB
нужно только для ручного обмена отметками интерактивной карты и не влияет на
локализацию или офлайн-запуск GPS.

Первый вход выполняется в отдельном профиле Edge WebView2:

```powershell
.venv\Scripts\genshin-navigator hoyolab-login --config config.json
```

Войдите на открывшейся официальной карте. Когда заголовок сообщит об успешном
подключении, закройте окно. Cookies остаются только в
`datasets/local/auth/hoyolab_webview`, не записываются в SQLite и исключены из Git.

Проверка состояния и двусторонняя additive-синхронизация:

```powershell
.venv\Scripts\genshin-navigator progress-status --config config.json
.venv\Scripts\genshin-navigator progress-sync --config config.json
```

`progress-sync` сначала показывает preview с количеством загружаемых и отправляемых
точек, а затем просит подтверждение. Синхронизация только добавляет collected-отметки
и никогда не снимает их в HoYoLAB. Для заранее проверенного неинтерактивного запуска
доступен флаг `--yes`.

Если отменить локальную отметку, которая уже присутствует в HoYoLAB, Navigator
сохранит локальное исключение и не будет возвращать её при следующем pull. Удалённая
отметка при этом не изменяется. Полностью удалить изолированную сессию можно командой:

```powershell
.venv\Scripts\genshin-navigator hoyolab-logout --config config.json
```

### Резервная копия и перенос прогресса

Перед изменением схемы SQLite Navigator создаёт консистентную резервную копию в
`datasets/local/backups` и хранит пять последних копий. Переносимый JSON содержит
только collected-отметки и локальные исключения HoYoLAB — без cookies, подсказок и
изображений:

```powershell
.venv\Scripts\genshin-navigator progress-export artifacts/fontaine-progress.json --config config.json
.venv\Scripts\genshin-navigator progress-import artifacts/fontaine-progress.json --config config.json
```

Обычный import сначала показывает preview и безопасно объединяет данные. Флаг
`--replace` заменяет только прогресс выбранного региона и перед применением создаёт
дополнительную резервную копию. Неизвестные POI перечисляются в preview и не
добавляются в базу.

1. Поместите цельное изображение карты в `assets/world_map.png`. Оно должно быть
   того же визуального стиля и масштаба, что и содержимое миникарты.
2. Сохраните тестовый снимок рабочего стола:

```powershell
.venv\Scripts\genshin-navigator capture --output artifacts/screen.png
```

3. В `config.json` укажите прямоугольник `roi` вокруг круглой миникарты.
4. Проверьте поиск без запуска непрерывного режима:

```powershell
.venv\Scripts\genshin-navigator locate --config config.json --screenshot artifacts/screen.png
```

5. Для пассивного обновления позиции по текущему экрану:

```powershell
.venv\Scripts\genshin-navigator watch --config config.json
```

Для live-трекера с отдельным компактным HUD:

```powershell
.venv\Scripts\genshin-navigator track --config config.json
```

Если включены блоки `poi` и `navigation`, HUD показывает закреплённую цель,
приблизительное расстояние, читаемое название слоя и north-up стрелку. `Num0`
глобально переключает HUD и полную карту, не меняя выбранную цель. Первоначально
выбирается ближайший несобранный сундук, но при движении цель сама не переключается.

Глобальное управление при включённом `Num Lock` не требует активировать Navigator:

- `Num4` / `Num6` — предыдущая / следующая цель;
- `Num2` — пропустить до закрытия приложения;
- удерживать `Num5` одну секунду — отметить собранной;
- `Num8` — отменить последнее `skip` или `collected`;
- `Num0` — компактный HUD / полная карта;
- `Num7` — раскрыть / закрыть официальную подсказку текущей цели;
- `Num1` / `Num3` — предыдущая / следующая страница подсказки;
- `NumDecimal` — разблокировать HUD для перетаскивания / закрепить и сохранить.
- `Num9` — закрыть Navigator из игры.

Закреплённый HUD остаётся поверх окон, не получает фокус и пропускает клики. Его
положение хранится локально в `datasets/local/ui/hud_state.json`. Navigator
регистрирует системные горячие клавиши Windows, но не отправляет ввод в игру и не
устанавливает низкоуровневых клавиатурных хуков. Конфликт занятой клавиши
показывается в HUD, не отключая остальные команды.

Подсказка загружается из публичной интерактивной карты HoYoLAB только при первом
раскрытии цели. Текст, изображение и ссылки сохраняются в ограниченном локальном
кэше `datasets/local/cache/poi`, поэтому уже просмотренная карточка доступна без
интернета. Сеть обслуживается отдельным потоком и не останавливает CV-трекинг.
Обычные пользовательские комментарии не используются. Авторизация нужна только
отдельной ручной команде синхронизации прогресса; загрузка подсказок остаётся публичной.

Если Genshin запущен от имени администратора, Navigator необходимо запустить с
тем же уровнем прав, иначе Windows не позволит ему увидеть глобальные клавиши над
активной игрой. HUD показывает предупреждение при обычном запуске.

При активном окне остаются запасные клавиши:

- `N` / `P` — следующая / предыдущая цель;
- `S` — пропустить до закрытия приложения;
- `M` — отметить собранной и атомарно сохранить прогресс;
- `U` — отменить последнее `skip` или `collected`;
- `Q` / `Esc` — закрыть окно.

Для каждого слоя запоминается своя цель. При потере трека, релокализации или
невидимой миникарте цель остаётся закреплённой, но линия и показания замораживаются
и сереют; стрелка скрывается до свежей подтверждённой позиции. Navigator никогда
не выбирает POI другого региона, этажа или системы координат.

### Калибровка расстояния

Чтобы перевести единицы официальной карты в приблизительные игровые метры, один раз
выполните три поверхностных замера:

```powershell
.venv\Scripts\genshin-navigator calibrate-distance `
  --config config.json `
  --output datasets\local\calibration\fontaine.json `
  --samples 3
```

Для каждого замера выберите в игре относительно ровный отрезок 100–300 м. Прямо в
игре нажмите `F8` для начала, переключитесь в окно калибровки, введите расстояние с
игрового маркера и `Enter`, вернитесь в игру, пройдите до конца и снова нажмите `F8`.
`C` остаётся запасной клавишей, но работает только при активном окне калибровки.
Команда использует тот же screen gate,
matcher и tracker, что live-режим, и не сохраняет полный экран. Результат принимается,
только если все три коэффициента согласуются в пределах 10%. Неполная или ошибочная
попытка остаётся в `fontaine.json.draft` и не заменяет последнюю рабочую калибровку.

После успешной калибровки окно показывает `~123 m (straight)`. Это прямая 2D-дистанция,
а не длина пешего маршрута: стены, входы в пещеры и перепад высоты пока не учитываются.
Без рабочей калибровки отображается `distance=uncalibrated`; atlas-px никогда не
выдаются за метры.

Окно закрывается клавишей `Q`, `Esc` или обычной кнопкой закрытия. Трекер не отправляет
ввод в Genshin: клавиши обрабатываются только когда активно его собственное окно.

При включённом блоке `failure_recorder` переход уже установленного трека в `LOST`
или невозможность найти начальную позицию дольше `acquisition_timeout_seconds`
создаёт инцидент в `artifacts/failures`. В него входят только вырезанные миникарты до
и после сбоя и `metadata.json` с диагностикой. Полный кадр игры не сохраняется. Если
программа закрыта во время записи, неполный инцидент всё равно сохраняется.

Блок `screen_gate` проверяет наличие постоянного компаса миникарты. При открытой
полноэкранной карте, загрузке или `Alt+Tab` время трекера замораживается, статус окна
становится `PAUSED`, а Failure Recorder не получает эти кадры. Проверка не зависит от
цветов и содержания карты, поэтому не отбрасывает стандартную миникарту подземелий.

Блок `local_search` включает сопровождение уже найденной позиции. После подтверждения
трек ищется сначала в небольшом радиусе на том же слое карты, а при неудаче снова
запускается глобальная локализация. Это помогает на однообразных дорогах и площадях,
не ослабляя защиту от ложных совпадений в других частях мира.

Отладочное окно автоматически показывает общий атлас для поверхности и переключается
на соответствующее полотно этажа для подземной локации. Переход на другой этаж
принимается только после нескольких последовательных совпадений с одним и тем же слоем.

### Position Model v1

Публичная позиция имеет единый контракт: `region_id`, `layer_id`,
`coordinate_space`, `x`, `y`, `confidence`, `state`, `timestamp` и
`reference_id`. Команды `locate`/`watch`, live-трекер и записи ошибок используют
одни и те же поля. В JSON также всегда присутствует `schema_version=1`. Старые
плоские `x_px`/`y_px` пока сохранены для совместимости.

Семантика состояния является частью контракта. Координаты пригодны для потребителей
только при `state=TRACKING` и `stale=false`. В `ACQUIRING` и `LOST` поле `position` отсутствует. В
`RELOCATING` может публиковаться последняя подтверждённая позиция, но только с
`stale=true`. Navigation/UI проверяют `state` и `stale`, а не интерпретируют
внутренние SIFT-метрики самостоятельно.

Для поверхности `coordinate_space=surface_atlas`: `x/y` относятся к общему атласу
региона. Для подземелья `coordinate_space=layer_local`: `x/y` относятся прямо к
изображению конкретного этажа, указанного в `layer_id`. Координаты разных этажей
нельзя сравнивать или смешивать. Поэтому подземный маркер отображается на полотне
своего этажа и больше не проецируется приблизительно на наземную карту Тейвата.

Остановка — `Ctrl+C`. Коды завершения `locate`: `0` — позиция найдена, `2` —
совпадение недостаточно надёжно, `1` — ошибка конфигурации или файла.

Аннотированный локальный набор можно прогнать отдельной командой:

```powershell
.venv\Scripts\genshin-navigator evaluate datasets/local/fontaine_v1
```

Команда выводит долю найденных кадров, медианную/P95-ошибку и среднее время обработки.

## Ограничения MVP

Качество зависит от эталонной карты, масштаба интерфейса и наличия визуальных
деталей. Подземные этажи хранятся отдельными картами и не имеют общей высотной
координаты. ROI сейчас задаётся вручную. Навигация показывает прямое направление к
цели, но пока не строит проходимый маршрут вокруг стен, через входы в пещеры или
между высотами.

Официальная интерактивная карта публикует подземные этажи отдельными изображениями с
границами в общей системе координат HoYoLAB. Пилотную группу или область можно
выгрузить вместе с привязкой так:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_underground.py `
  datasets\local\references\hoyolab_underground_pilot --group-id 109

.venv\Scripts\python scripts\fetch_hoyolab_underground.py temp --list-only `
  --near -4348.5 -692.5 --radius 500
```

Для каждого этажа создаётся собственный `layer_id`; `metadata.json` содержит URL
оригинала, размеры, мировые границы и формулу преобразования пикселей в координаты
HoYoLAB. Для полной выгрузки нужно явно передать `--all`; для экспериментов лучше
начинать с `--group-id` или `--near`.

## Каталог POI Фонтейна

Готовый каталог `datasets/local/poi/fontaine.json` собран из публичного API
[официальной интерактивной карты HoYoLAB](https://act.hoyolab.com/ys/app/interactive-map/index.html#/map/2).
Он содержит обычные, богатые, драгоценные, роскошные и удивительные сундуки,
гидрокулы, статуи Архонтов и точки телепортации. Наземные метки переводятся в
`surface_atlas`, подземные — в `layer_local` соответствующего этажа. Раздел `spaces`
хранит линейную метрику поверхности и всех 59 этажей. Каталог старого формата без
этого раздела по-прежнему загружается, но расстояние остаётся недоступным.

Каталог и официальные подземные этажи можно обновить воспроизводимо:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_underground.py `
  datasets\local\references\hoyolab_fontaine_underground --preset fontaine

.venv\Scripts\python scripts\build_underground_pyramid.py `
  datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\surface_pyramid.json `
  datasets\local\references\hoyolab_fontaine_underground\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\pyramid.json

.venv\Scripts\python scripts\fetch_hoyolab_poi.py `
  datasets\local\poi\fontaine.json `
  --surface-metadata datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  --underground-metadata datasets\local\references\hoyolab_fontaine_underground\metadata.json
```

Готовый пресет Фонтейна включает Великое озеро, крепость Меропид, Элинас,
Аннапаузис, Эриний и Ремурию. Полный атлас и пирамиду можно пересобрать так:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_atlas.py `
  datasets\local\references\hoyolab_fontaine_full_n1 `
  --zoom N1 --x 32:43 --y 12:23

.venv\Scripts\python scripts\fetch_hoyolab_underground.py `
  datasets\local\references\hoyolab_fontaine_underground --preset fontaine

.venv\Scripts\python scripts\build_underground_pyramid.py `
  datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\surface_pyramid.json `
  datasets\local\references\hoyolab_fontaine_underground\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\pyramid.json
```

`fetch_hoyolab_atlas.py` создаёт безопасный базовый
`surface_pyramid.json`; следующая команда добавляет к нему официальные подземные
слои и пишет итоговый `pyramid.json`. После этого выполните команду выгрузки POI
из предыдущего блока и проверьте пути в `config.json`.

Проверка по парным скриншотам игры и внутриигровой карты:

```powershell
.venv\Scripts\python scripts\evaluate_pyramid_screenshots.py `
  datasets\local\fontaine_underground_v1 --config config.json
```

Текущий baseline использует SIFT-признаки: они устойчивы к разнице масштаба между
миникартой и эталоном. Центральная стрелка игрока и край круглой миникарты исключаются
из анализа маской.

Если задан `pyramid_path`, локализатор автоматически проверяет базовый атлас и
детальные уровни. Поверхностные уровни приводятся к координатам общего атласа,
а подземные сохраняют точные локальные координаты собственного этажа.

Для локальных экспериментов эталон можно собрать из уже идентифицированного публичного
слоя HoYoLAB:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_atlas.py `
  datasets\local\references\hoyolab_fontaine_n1 --zoom N1 --x 32:43 --y 12:19
```

Подробный уровень центрального Фонтейна можно собрать из тайлов Appsample уровня 15
и автоматически привязать к каноническому атласу:

```powershell
.venv\Scripts\python scripts\fetch_appsample_atlas.py `
  datasets\local\references\appsample_fontaine_l15

.venv\Scripts\python scripts\build_scaled_reference.py `
  datasets\local\references\appsample_fontaine_l15\atlas.png `
  datasets\local\references\appsample_fontaine_l15\atlas_x2.png --scale 0.5

.venv\Scripts\python scripts\register_reference.py `
  datasets\local\references\hoyolab_fontaine_n1\atlas.png `
  datasets\local\references\appsample_fontaine_l15\atlas_x2.png `
  datasets\local\references\appsample_fontaine_l15\registration_x2.json `
  --match-scale 1.0

.venv\Scripts\python scripts\crop_registered_reference.py `
  datasets\local\references\appsample_fontaine_l15\atlas.png `
  datasets\local\references\appsample_fontaine_l15\registration.json `
  datasets\local\references\appsample_fontaine_l15\regions\court_of_fontaine_x4.png `
  --x 1800 --y 1300 --width 1800 --height 1800

.venv\Scripts\python scripts\crop_registered_reference.py `
  datasets\local\references\appsample_fontaine_l15\atlas.png `
  datasets\local\references\appsample_fontaine_l15\registration.json `
  datasets\local\references\appsample_fontaine_l15\regions\epicles_x4.png `
  --x 3600 --y 1500 --width 1800 --height 1800
```

Тайлы и собранный атлас остаются в исключённом из Git каталоге `datasets/local`.
Перед распространением таких данных отдельно проверьте условия использования источника.

## Проверка

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

Сохранённый Failure Recorder инцидент можно воспроизвести без запуска игры:

```powershell
.venv\Scripts\python scripts\replay_failure.py artifacts\failures\<incident>
```

## Сценарный benchmark

Recorder сохраняет только crop миникарты с монотонным временем. Полный экран и UID
на диск не пишутся. Запись начинается сразу после запуска команды, а частота берётся
из `interval_seconds` текущей конфигурации.

Три базовых сценария записываются в исключённый из Git каталог:

```powershell
.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\surface_walk --config config.json --duration 20 `
  --name "surface walk and stop" --expected-start-layer surface `
  --expected-end-layer surface --stationary-last-seconds 5

.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\surface_teleport --config config.json --duration 30 `
  --name "surface teleport" --expected-start-layer surface `
  --expected-end-layer surface

.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\layer_transition --config config.json --duration 30 `
  --name "surface to underground" --expected-start-layer surface `
  --expected-end-layer underground:map2:group90:floor78
```

В третьей команде замените конечный `layer_id` на реально выбранный этаж из
`pyramid.json`. Начальную и конечную точки сверяйте по внутриигровой карте; при
необходимости добавьте их в `scenario.json` как контрольные позиции по схеме из
`datasets/README.md`.

Replay проходит тем же путём, что live-режим: screen gate → matcher/local search →
tracker. Он ничего не захватывает с экрана:

```powershell
.venv\Scripts\genshin-navigator evaluate-sequence `
  datasets\local\scenarios\surface_walk --config config.json `
  --report artifacts\benchmarks\surface_walk.json
```

JSON-отчёт различает отсутствие позиции и false lock, измеряет точность слоя,
acquire/reacquire, длительность `LOST`, stationary jitter и среднюю/P95 задержку.
Длительность `LOST` учитывает только время с видимой миникартой; открытая карта,
загрузка и Alt+Tab считаются паузой screen gate.
Критерии контрольной проверки: false locks = 0, нет одно-кадровых смен слоя,
reacquire ≤ 3 с и P95 jitter ≤ 5 atlas-px.
