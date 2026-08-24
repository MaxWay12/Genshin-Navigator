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
.venv\Scripts\python -m pip install -e .
Copy-Item config.example.json config.json
```

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

Для live-трекера с отдельной отладочной картой:

```powershell
.venv\Scripts\genshin-navigator track --config config.json
```

Если включён блок `poi`, отладочная карта показывает официальные POI текущего слоя
и выделяет ближайший ещё не отмеченный сундук белым кольцом. После сбора переключитесь
на окно Navigator и нажмите `M`: идентификатор цели сохранится в локальном
`artifacts/poi_progress.json`. Navigator не отправляет эту клавишу в игру.

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
одни и те же поля. Старые плоские `x_px`/`y_px` пока сохранены для совместимости.

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
координаты. ROI сейчас
задаётся вручную; автоматическое определение миникарты и калибровка игровых координат
относятся к следующему этапу.

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
`surface_atlas`, подземные — в `layer_local` соответствующего этажа.

Каталог и официальные подземные этажи можно обновить воспроизводимо:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_underground.py `
  datasets\local\references\hoyolab_fontaine_underground --preset fontaine

.venv\Scripts\python scripts\build_underground_pyramid.py `
  datasets\local\references\hoyolab_fontaine_full_n1\metadata.json `
  datasets\local\references\hoyolab_fontaine_n1\pyramid.json `
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
  datasets\local\references\hoyolab_fontaine_n1\pyramid.json `
  datasets\local\references\hoyolab_fontaine_underground\metadata.json `
  datasets\local\references\hoyolab_fontaine_full_n1\pyramid.json
```

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
