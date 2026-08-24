# Localization datasets

Локальные наборы хранятся в `datasets/local/` и не попадают в систему контроля
версий: игровые скриншоты могут содержать UID и другие пользовательские данные.

Формат `annotations.json`:

```json
{
  "reference": "reference.png",
  "frames": [
    {
      "image": "frames/frame_001.png",
      "expected": {"x_px": 100.0, "y_px": 200.0}
    }
  ]
}
```

Для безопасного набора рекомендуется сохранять только crop миникарты, а не полный
кадр игры.

## Stateful scenarios, format_version 1

Последовательности хранятся в `datasets/local/scenarios/<name>/` и потому не
попадают в Git. Команда `record-sequence` создаёт `frames/*.png` только с crop
миникарты и манифест `scenario.json`:

```json
{
  "format_version": 1,
  "name": "surface to underground",
  "interval_seconds": 0.1,
  "expectations": [
    {
      "name": "end",
      "start_seconds": 25.0,
      "end_seconds": 30.0,
      "tracking": "required",
      "region_id": "fontaine",
      "layer_id": "underground:map2:group90:floor78",
      "position": {"x": 512.0, "y": 384.0, "tolerance_px": 25.0},
      "stationary_from_seconds": 27.0
    }
  ],
  "frames": [
    {"image": "frames/minimap_00000.png", "timestamp_seconds": 0.0}
  ]
}
```

`tracking` бывает `required` или `optional`. `position` необязательна и задаётся в
системе координат ожидаемого слоя: `surface_atlas` для поверхности либо
`layer_local` для конкретного подземного этажа. `tolerance_px` задаёт допустимую
ошибку контрольной привязки. Времена кадров должны быть неотрицательными и строго
возрастающими; пути кадров не могут выходить из каталога сценария.

Старые одиночные datasets с `annotations.json` остаются совместимыми и проверяются
командой `evaluate`. Последовательности проверяются отдельной командой
`evaluate-sequence`.
