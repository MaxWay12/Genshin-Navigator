from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


FORBIDDEN_ARTIFACT_PARTS = {
    "genshin_navigator.db",
    "hoyolab_webview",
    "poi_progress.json",
    "hud_state.json",
    "backups",
    "diagnostics",
    "failures",
    "scenarios",
}
UID_WITH_VALUE = re.compile(rb"\bUID\s*[:=]?\s*\d{9,10}\b", re.I)
AUTH_VALUE = re.compile(
    rb"(?:cookie|authorization|ltoken|ltuid|account_id)\s*[:=]\s*[^\s,;}{]{8,}",
    re.I,
)


def tracked_files(root: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", "ls-files", "-z"],
        cwd=root,
    )
    return [root / item.decode("utf-8") for item in output.split(b"\0") if item]


def inspect_file(path: Path, *, artifact: bool) -> list[str]:
    issues: list[str] = []
    relative_parts = {part.lower() for part in path.parts}
    if artifact and relative_parts & FORBIDDEN_ARTIFACT_PARTS:
        issues.append("forbidden personal-state path")
    if artifact and "datasets" in relative_parts and "local" in relative_parts:
        issues.append("release must not bundle downloaded content or user state")
    try:
        data = path.read_bytes()
    except OSError as error:
        return [f"unreadable: {error}"]
    profile = os.environ.get("USERPROFILE", "").encode("utf-8")
    local_profile = re.compile(re.escape(profile), re.I) if profile else None
    path_pattern = local_profile
    checks = [
        ("UID value", UID_WITH_VALUE),
    ]
    # Third-party Python sources and DLLs contain harmless cookie/auth API names.
    # Secret-bearing application data is textual metadata, not executable code.
    sensitive_suffixes = {".json", ".txt", ".log", ".ini", ".cfg", ".yaml", ".yml", ".db", ".sqlite"}
    if not artifact or path.suffix.lower() in sensitive_suffixes:
        checks.append(("authentication value", AUTH_VALUE))
    if path_pattern is not None:
        checks.insert(0, ("absolute user path", path_pattern))
    for label, pattern in checks:
        if pattern.search(data):
            issues.append(label)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tracked and release files for private state")
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    targets = [(path, False) for path in tracked_files(root) if path.is_file()]
    if args.artifact is not None:
        artifact = args.artifact.resolve()
        targets.extend((path, True) for path in artifact.rglob("*") if path.is_file())
    failures: list[str] = []
    for path, is_artifact in targets:
        for issue in inspect_file(path, artifact=is_artifact):
            try:
                shown = path.relative_to(root)
            except ValueError:
                shown = path
            failures.append(f"{shown}: {issue}")
    if failures:
        print("Release privacy audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 2
    print(f"Release privacy audit passed ({len(targets)} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
