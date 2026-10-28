"""Build the portable folder and its zip.

    python packaging/build.py

Needs `pip install -e .[mind,build]`. Steps: save her face as build/cc-companion.ico, run
PyInstaller on packaging/cc-companion.spec into dist/cc-companion/, copy the READMEs and
license files beside the programs, and zip the folder as
dist/cc-companion-<version>-windows-x64.zip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # the icon needs a QGuiApplication, never a window

from companion import __version__  # noqa: E402

BUILD, DIST = ROOT / "build", ROOT / "dist"
FOLDER = DIST / "cc-companion"
EXTRAS = {
    "README.md": "README.md",
    "README_EN.md": "README_EN.md",
    "LICENSE": "LICENSE.txt",
    "companion/assets/character/LICENSE.txt": "CHARACTER-LICENSE.txt",
}


def make_icon() -> Path:
    from PySide6.QtGui import QGuiApplication

    from companion import skin as skins
    from companion.tray import face_image

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - QImage scaling wants one
    path = BUILD / "cc-companion.ico"
    BUILD.mkdir(exist_ok=True)
    if not face_image(skins.load_skin(skins.default_dir()), 128).save(str(path), "ICO"):
        raise SystemExit("could not write the icon")
    return path


def main() -> int:
    print(f"icon: {make_icon()}")
    shutil.rmtree(FOLDER, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST),
         "--workpath", str(BUILD / "pyinstaller"), str(ROOT / "packaging" / "cc-companion.spec")],
        check=True,
    )  # fmt: skip
    for source, name in EXTRAS.items():
        shutil.copy2(ROOT / source, FOLDER / name)
    archive = shutil.make_archive(str(DIST / f"cc-companion-{__version__}-windows-x64"), "zip", DIST, FOLDER.name)
    size = sum(p.stat().st_size for p in FOLDER.rglob("*") if p.is_file())
    print(f"folder: {FOLDER} ({size / 1e6:.0f} MB)")
    print(f"zip: {archive} ({Path(archive).stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
