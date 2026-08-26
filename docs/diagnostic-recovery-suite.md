# Diagnostic recovery suites

Live tracking diagnostics distinguish three outcomes:

- `transient_recovered`: tracking returned within the captured post-trigger frames;
- `unresolved`: no usable non-stale tracking position returned;
- `manual_report`: the user explicitly captured the ring buffer.

Run a local recovery regression with:

```powershell
.venv\Scripts\genshin-navigator diagnostic-suite `
  datasets/local/portability/sumeru-live-recovery-suite.json `
  --config config.sumeru-portability-anchors.example.json `
  --report datasets/local/portability/sumeru-live-recovery-report.json
```

This suite verifies recovery behavior and time only. It must not be reported as a
positional false-lock test unless the source sequence also has coordinate checkpoints.
Short bundles that end before recovery can be retained with `"gating": false`; they
remain visible in the report without producing an unsupported pass/fail conclusion.
Diagnostic images and local manifests stay under `datasets/local` and are not committed.
