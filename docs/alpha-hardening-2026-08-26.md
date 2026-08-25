# Alpha Hardening v1 baseline

## Автоматические проверки

- Unit/integration: 121/121.
- Surface: 4/4, median error 3.902 px, P95 3.959 px.
- Multispot: 3/5; два прежних `not_enough_feature_matches` сохранены как baseline.
- Underground: 3/3 правильных слоя.
- Golden gating: surface teleport и underground stationary проходят все hard KPI.

## Canonical surface → floor78 v2

- false locks: 0;
- layer accuracy: 1.0 (129 samples);
- one-frame layer runs: 0;
- max reacquire: 1.5 s;
- required tracking coverage: 0.9923;
- stationary jitter P95: 6.249 px — informational fail при лимите 5 px.

Запись корректна. После остановки сглаженная позиция около трёх секунд догоняет
сырое положение по оси Y (примерно 10.7 px), после чего семь секунд остаётся
абсолютно неподвижной. Порог tracker не менялся. Сценарий оставлен informational,
чтобы следующий hardening этап мог отдельно измерить settling time и jitter steady
state, не скрывая обнаруженный хвост smoothing.

## Diagnostic bundle

Ручной `NumAdd` report: format v4, 16 minimap crops (8 до события и 8 после),
top-N candidates присутствуют. Проверка не обнаружила UID, cookies, auth headers,
полного экрана или абсолютных пользовательских путей. Replay проходит через тот же
matcher/tracker pipeline; format v3 остаётся читаемым.

## Progress safety

Контрольный export находится только локально в
`datasets/local/backups/fontaine-progress-hardening.json`: format v1, Fontaine,
9 collected и 0 remote-ignore. Рабочая SQLite-база во время проверки не изменялась.
