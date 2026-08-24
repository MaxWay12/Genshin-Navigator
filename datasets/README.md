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

