# Changelog

## v0.1.0-alpha — 2026-08-26

First public alpha of Genshin Navigator.

### Included

- Passive minimap localization and stateful tracking.
- Fontaine surface and underground navigation with official POI.
- Experimental Sumeru desert surface navigation and official POI.
- Compact, detailed, and full-map HUD modes with global numpad controls.
- Sticky chest targets, skip, collected, and undo.
- Offline-first SQLite progress and cached HoYoLAB hints.
- Optional manual additive progress synchronization with HoYoLAB.
- Privacy-safe minimap diagnostics and replay benchmarks.
- Portable Windows build with no bundled personal state.

### Known limitations

- Fontaine is the supported alpha vertical slice; Sumeru is experimental.
- Sumeru underground floors are not included.
- Sparse desert ruins can temporarily become `LOST`; false positions are rejected.
- Distance in Sumeru can be shown as uncalibrated until a regional calibration exists.
- Navigation uses straight-line direction and distance, not obstacle-aware routes.
- Global hotkeys require Navigator and Genshin to run at the same privilege level.
- HoYoLAB APIs are undocumented and can change independently of Navigator.
