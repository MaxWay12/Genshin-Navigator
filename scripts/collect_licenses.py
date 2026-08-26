from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import sys
from pathlib import Path


DISTRIBUTIONS = (
    "numpy",
    "opencv-python",
    "Pillow",
    "pywebview",
    "pythonnet",
    "clr-loader",
    "cffi",
    "pycparser",
    "bottle",
    "proxy-tools",
    "typing_extensions",
    "setuptools",
    "pyinstaller",
)
FALLBACK_LICENSES = {
    "proxy-tools": Path(__file__).resolve().parent / "licenses" / "proxy_tools_LICENSE.txt",
}
LICENSE_WORDS = ("license", "copying", "notice", "authors")


def collect(output: Path) -> dict[str, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    copied: dict[str, list[str]] = {}
    missing: list[str] = []
    for name in DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(name)
        destination = output / f"{distribution.metadata['Name']}-{distribution.version}"
        found = []
        for item in distribution.files or ():
            filename = Path(str(item)).name.lower()
            if not any(word in filename for word in LICENSE_WORDS):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            safe_name = "__".join(
                part for part in Path(str(item)).parts if part not in {".", ".."}
            )
            target = destination / safe_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            found.append(target.name)
        license_text = distribution.metadata.get("License")
        if not found and license_text and len(license_text.strip()) > 80:
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "LICENSE-from-package-metadata.txt"
            target.write_text(license_text.strip() + "\n", encoding="utf-8")
            found.append(target.name)
        if not found and name in FALLBACK_LICENSES and FALLBACK_LICENSES[name].is_file():
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "LICENSE.txt"
            shutil.copy2(FALLBACK_LICENSES[name], target)
            found.append(target.name)
        if not found:
            missing.append(f"{name} {distribution.version}")
        copied[f"{name}=={distribution.version}"] = sorted(set(found))

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        target = output / f"Python-{sys.version_info.major}.{sys.version_info.minor}"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, target / "LICENSE.txt")
        copied[f"Python {sys.version_info.major}.{sys.version_info.minor}"] = ["LICENSE.txt"]
    else:
        missing.append("Python runtime")
    if missing:
        raise RuntimeError("No distributable license text found for: " + ", ".join(missing))
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect license texts for the portable build")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    copied = collect(args.output)
    for package, files in copied.items():
        print(f"{package}: {', '.join(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
