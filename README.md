# Genshin Navigator

**Genshin Navigator v0.1.0-alpha** — пассивный GPS-помощник для мира Genshin Impact.

Navigator смотрит только на изображение миникарты, определяет положение игрока и
показывает ближайший сундук, направление, расстояние и официальную подсказку в
отдельном HUD-окне. Это бесплатный open-source pet-проект, не связанный с HoYoverse.

## Что принципиально делает и не делает Navigator

Navigator получает обычное изображение экрана Windows, анализирует область миникарты,
показывает собственное отдельное окно и хранит карты, POI и прогресс локально.

Navigator **не** управляет персонажем, не эмулирует игровой ввод, не читает и не
изменяет память Genshin, не внедряется в процесс игры и не устанавливает
низкоуровневые клавиатурные хуки. Глобальные клавиши реализованы стандартным Windows
`RegisterHotKey` и управляют только Navigator.

Проект описывает техническое поведение программы и не утверждает, что сторонние
инструменты официально разрешены HoYoverse.

## Возможности alpha

- live-локализация по миникарте и stateful tracking;
- безопасные состояния `TRACKING`, `ACQUIRING`, `RELOCATING` и `LOST`;
- поверхность и подземные этажи Фонтейна;
- экспериментальная поверхность пустыни Сумеру;
- официальный каталог сундуков и телепортов;
- sticky target, previous/next, skip, collected и undo;
- компактный HUD, карточка подсказки и полная карта;
- офлайн SQLite-прогресс и кэш просмотренных подсказок;
- необязательная ручная additive-синхронизация отметок HoYoLAB;
- обезличенные diagnostic bundles только из crops миникарты.

## Поддерживаемые регионы

| Регион | Статус | Объём поддержки |
| --- | --- | --- |
| Фонтейн | **Supported alpha** | Поверхность, 59 подземных этажей, POI, HUD, hints, progress |
| Сумеру — пустыня | **Experimental** | Поверхность, POI, HUD, hints, local progress |

Сумеру пока не включает подземные этажи. В визуально пустых руинах Navigator может
временно перейти в `LOST`; это безопаснее уверенной неправильной позиции.

## Системные требования

- Windows 10/11 x64;
- Genshin Impact в оконном или безрамочном режиме;
- включённый Num Lock;
- рекомендуемая готовая конфигурация: 1920×1080, масштаб Windows 100%;
- Edge WebView2 — только для необязательного входа HoYoLAB;
- интернет нужен для обновлений, подсказок и sync, но не для обычной навигации.

## Установка portable build

1. Скачайте `GenshinNavigator-v0.1.0-alpha-windows-x64.zip`.
2. Сверьте SHA-256 с соседним файлом `.sha256`.
3. Распакуйте архив в обычную доступную для записи папку.
4. Для Фонтейна запустите `Start-Fontaine.cmd`.
5. Для экспериментального Сумеру запустите `Start-Sumeru-Experimental.cmd`.

Не запускайте EXE внутри ZIP: рядом с программой создаётся локальная папка
`datasets/local` с прогрессом, кэшем и настройками HUD.

### Первый запуск и ROI

При первом запуске `.cmd` автоматически создаёт рабочий `config.json` для Фонтейна
или `config.sumeru.json` для Сумеру. Готовые значения используют миникарту `216×216`
с левым верхним углом `(57, 19)` для 1920×1080. Если разрешение или UI отличаются,
измените секцию `roi` в созданном рабочем файле нужного региона:

```json
"roi": {"left": 57, "top": 19, "width": 216, "height": 216}
```

После чтения кадра diagnostics сохраняют только этот crop, а не полный экран.

## Управление

При включённом Num Lock:

- `Num4` / `Num6` — предыдущая / следующая цель;
- `Num2` — временно пропустить цель;
- удерживать `Num5` одну секунду — отметить сундук собранным;
- `Num8` — отменить последнее действие;
- `Num0` — HUD / полная карта;
- `Num7` — раскрыть или закрыть подсказку;
- `Num1` / `Num3` — страницы текста подсказки;
- `NumDecimal` — разблокировать HUD для перемещения / закрепить;
- `NumAdd` — сохранить обезличенный diagnostic bundle;
- `Num9` — закрыть Navigator.

Закреплённый HUD не получает фокус и пропускает клики. Если клавиши работают на
рабочем столе, но не в Genshin, запустите Navigator и игру с одинаковыми правами.

## HUD

Компактный режим показывает цель, прямолинейное расстояние, слой, состояние трекера и
north-up стрелку. При `LOST`, stale-позиции или телепорте цель сохраняется, но стрелка
и расстояние замораживаются и затемняются.

`Num7` раскрывает официальное описание и изображение точки. Просмотренная карточка
кэшируется и доступна офлайн. Ошибка HoYoLAB влияет только на карточку, не на GPS.

## Прогресс и офлайн-режим

Главное состояние хранится в `datasets/local/data/genshin_navigator.db`. Отметки
переживают перезапуск, а перед изменением схемы создаётся backup. Обычный запуск не
требует сети. Portable archive не содержит чужой базы, прогресса, cookies или reports.

Экспорт и безопасное объединение прогресса в установке из исходников:

```powershell
.venv\Scripts\genshin-navigator progress-export progress.json --config config.example.json --region fontaine
.venv\Scripts\genshin-navigator progress-import progress.json --config config.example.json --region fontaine
```

## Интеграция HoYoLAB

Интеграция необязательна. Вход открывается в отдельном профиле Edge WebView2 и не
читает cookies установленного браузера:

```powershell
GenshinNavigator.exe hoyolab-login --config config.json
GenshinNavigator.exe progress-sync --config config.json --region fontaine
GenshinNavigator.exe hoyolab-logout --config config.json
```

Sync показывает preview и требует подтверждение. Он только добавляет отметки и не
снимает их автоматически. Это отметки интерактивной карты, а не реальное состояние
сундуков игрового мира. HoYoLAB API недокументирован и может измениться; локальный GPS
и прогресс продолжат работать офлайн.

## Diagnostics и privacy

`NumAdd` или команда ниже сохраняют последовательность crops миникарты:

```powershell
GenshinNavigator.exe diagnostic-record --config config.json --duration 5
```

Bundle не должен содержать полный экран, UID, cookies, auth headers, полный config или
абсолютный пользовательский путь. Перед отправкой его всё равно можно проверить.

В `datasets/local` вне Git хранятся SQLite, progress, профиль HoYoLAB, кэш изображений,
положение HUD, screenshots, сценарии и diagnostics.

## Известные ограничения

- Это alpha, а не завершённый продукт.
- Сумеру экспериментален и ограничен поверхностью пустыни.
- В sparse/low-observability зонах возможны краткие `LOST`.
- Для Сумеру расстояние может показываться как uncalibrated.
- Стрелка не учитывает стены, высоту, входы в пещеры и пеший маршрут.
- Скрытые или сюжетно заблокированные сундуки требуют ручного skip.
- Изменения UI или официальных карт могут потребовать обновления assets.
- Глобальные клавиши могут конфликтовать с другими программами.

## Troubleshooting

### Tracker постоянно LOST

- Проверьте разрешение, масштаб Windows и `roi`.
- Убедитесь, что миникарта полностью попадает в crop.
- Закройте большую карту и дождитесь обычной миникарты.
- Запустите правильный регион через соответствующий `.cmd`.

### Нет целей

- Проверьте каталог региона в `datasets/local/poi`.
- Запустите `data-status` с конфигурацией региона.
- Цели другого региона намеренно не выбираются.

### Не работают NumPad-клавиши

- Включите Num Lock.
- Проверьте одинаковый уровень прав Genshin и Navigator.
- Закройте программу, занявшую ту же глобальную клавишу.

### HoYoLAB недоступен

Продолжайте пользоваться GPS офлайн. Для login установите актуальный Microsoft Edge
WebView2 Runtime. Сетевая ошибка не должна повреждать локальный прогресс.

## Разработка из исходников

Проверенная среда: Python 3.12, NumPy 2.5.2, OpenCV 4.14.0, Pillow 11.3.0,
pywebview 6.2.1.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python scripts\release_smoke.py
```

Запуск через региональный manifest:

```powershell
.venv\Scripts\genshin-navigator track --regions regions.json --region fontaine
.venv\Scripts\genshin-navigator track --regions regions.json --region sumeru_desert
```

Обновление публичных POI выполняется отдельно и транзакционно:

```powershell
.venv\Scripts\genshin-navigator sync-data --config config.example.json
.venv\Scripts\genshin-navigator sync-data --config config.sumeru.example.json
```

Сборка portable artifact:

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File release\build_portable.ps1
```

Скрипт включает только выбранные официальные assets и каталоги, запускает privacy-аудит
и создаёт SHA-256.

## Архитектура

```text
screen capture → minimap gate → localization → tracker → Position Model
                                                        ↓
Region Manifest → Providers → SQLite → Repositories → Navigation → HUD
```

CV не знает о HoYoLAB, SQLite или прогрессе. Navigation не обращается к сети. Runtime
использует только локальный data provider.

## Support the project

Navigator бесплатен и не имеет paywall. Добровольная ссылка может быть добавлена позже:

```text
DONATION_LINK_PLACEHOLDER
```

Реальный игровой UID разработчика намеренно не включён.

## Лицензия

[MIT](LICENSE). Названия Genshin Impact и HoYoLAB принадлежат правообладателям.
