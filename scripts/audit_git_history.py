from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
from pathlib import Path


PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "HoYoLAB credential": re.compile(
        rb"(?:ltoken(?:_v2)?|ltuid(?:_v2)?|cookie_token(?:_v2)?|account_id)"
        rb"\s*[:=]\s*[\"']?[A-Za-z0-9._-]{12,}",
        re.I,
    ),
    "authorization value": re.compile(
        rb"authorization\s*[:=]\s*[\"']?(?:bearer\s+)?[A-Za-z0-9._-]{16,}",
        re.I,
    ),
    "UID value": re.compile(rb"\bUID\s*[:=]?\s*\d{9,10}\b", re.I),
}
KNOWN_SYNTHETIC_VALUES = (
    b"ltoken_v2=secret",
    b"ltoken_v2=do-not-print",
)


def _git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", *args],
        cwd=root,
        input=input_data,
    )


def audit_history(root: Path, *, max_blob_bytes: int = 32 * 1024 * 1024) -> dict[str, object]:
    lines = _git(root, "rev-list", "--objects", "--all").splitlines()
    entries: dict[bytes, set[str]] = {}
    for line in lines:
        object_id, _, raw_path = line.partition(b" ")
        if raw_path:
            entries.setdefault(object_id, set()).add(raw_path.decode("utf-8", "replace"))
    findings = []
    skipped = []
    profile = os.environ.get("USERPROFILE", "").encode("utf-8")
    checks = dict(PATTERNS)
    if profile:
        checks["absolute user path"] = re.compile(re.escape(profile), re.I)
    checked = 0
    batch = _git(root, "cat-file", "--batch", input_data=b"".join(
        object_id + b"\n" for object_id in entries
    ))
    stream = io.BytesIO(batch)
    for object_id, paths in entries.items():
        header = stream.readline().split()
        if len(header) != 3:
            raise RuntimeError(f"Could not read Git object {object_id.decode()[:12]}")
        object_type, size = header[1], int(header[2])
        data = stream.read(size)
        stream.read(1)
        if object_type != b"blob":
            continue
        if size > max_blob_bytes:
            skipped.append({"object": object_id.decode()[:12], "paths": sorted(paths), "size": size})
            continue
        for synthetic in KNOWN_SYNTHETIC_VALUES:
            data = data.replace(synthetic, b"")
        checked += 1
        for label, pattern in checks.items():
            if pattern.search(data):
                findings.append({
                    "kind": label,
                    "object": object_id.decode()[:12],
                    "paths": sorted(paths),
                })
    return {"checked_blobs": checked, "findings": findings, "skipped_large_blobs": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan every reachable Git blob without printing secrets")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = audit_history(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Checked {report['checked_blobs']} unique Git-history blobs.")
        for finding in report["findings"]:
            print(
                f"- {finding['kind']}: {', '.join(finding['paths'])} "
                f"(object {finding['object']})"
            )
        for skipped in report["skipped_large_blobs"]:
            print(
                f"- NOT SCANNED (large): {', '.join(skipped['paths'])} "
                f"({skipped['size']} bytes)"
            )
    return 2 if report["findings"] or report["skipped_large_blobs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
