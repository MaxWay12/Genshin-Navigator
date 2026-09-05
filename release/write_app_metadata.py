"""Ensure a portable build reports its source version, not the build venv version."""
import shutil
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
stage = Path(sys.argv[1]).resolve()
if not stage.is_relative_to(root / "dist"):
    raise ValueError("Metadata destination must be inside project dist")
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
internal = stage / "_internal"
for old in internal.glob("genshin_navigator-*.dist-info"):
    if not old.is_symlink() and old.resolve().parent == internal:
        shutil.rmtree(old)
metadata = internal / f"genshin_navigator-{version}.dist-info"
metadata.mkdir()
(metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: genshin-navigator\nVersion: {version}\n", encoding="utf-8")
