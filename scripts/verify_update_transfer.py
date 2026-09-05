"""Exercise a built ZIP against a read-only source, using an isolated copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

from genshin_navigator.portable_transfer import PortableTransfer, atomic_json
from genshin_navigator.updates import ReleaseInfo, UpdateService


def database_state(path):
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(repr(row) for row in connection.execute("SELECT * FROM " + quoted))
            result[name] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source, archive = args.source.resolve(), args.archive.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="update-smoke-", dir=args.report.parent) as temp:
        dest = Path(temp) / "Новая версия GPS"
        dest.mkdir()
        files = PortableTransfer(dest)._files(source)
        originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        databases = {p.relative_to(source): database_state(p) for p in files if p.suffix.lower() in (".db", ".sqlite", ".sqlite3")}
        tag = archive.name.removeprefix("GenshinNavigator-").removesuffix("-windows-x64.zip")
        base = f"https://github.com/MaxWay12/Genshin-Navigator/releases/download/{tag}/"
        release = ReleaseInfo(tag, "local smoke", archive.name, base + archive.name, base + archive.name + ".sha256", archive.stat().st_size)
        def opener(request, **kwargs):
            path = Path(str(archive) + ".sha256") if request.full_url.endswith(".sha256") else archive
            return path.open("rb")
        update = UpdateService(source, opener=opener)
        transfer = update.apply(release, dest, stopped=True)
        for relative, expected in databases.items():
            if database_state(dest / relative) != expected:
                raise AssertionError("Transferred SQLite rows changed")
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != digest for p, digest in originals.items()):
            raise AssertionError("Source files changed")
        version = subprocess.run([str(dest / "GenshinNavigator.exe"), "--version"], cwd=dest,
                                 capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        report = {"passed": True, "source_unchanged": True, "unicode_destination": True,
                  "transferred_files": transfer["file_count"], "transferred_bytes": transfer["bytes"],
                  "database_count": len(databases), "database_rows_preserved": True,
                  "tables": {str(path): {name: count for name, (count, digest) in tables.items()} for path, tables in databases.items()},
                  "auth_transferred": (dest / "datasets/local/auth").exists(), "binary_version": version,
                  "network": "local fixture transport; no account or network calls"}
        atomic_json(args.report, report)
        print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
