# PyInstaller spec for the portable build: one folder, two programs sharing one runtime.
#
#   cc-companion.exe       windowed: the pet (Qt)
#   cc-companion-cli.exe   console: launch, statusline, mind (no Qt)
#
# Run through packaging/build.py, which makes the icon first and zips the folder after.

from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "build" / "cc-companion.ico")

datas = [
    (str(ROOT / "companion" / "assets"), "companion/assets"),
    (str(ROOT / "mind" / "identity.example.md"), "mind"),
    # Connect to Claude Code loads the installer from its file, beside its snippet.
    (str(ROOT / "hooks" / "install.py"), "hooks"),
    (str(ROOT / "hooks" / "settings.snippet.json"), "hooks"),
]
# What hooks/install.py imports, since it is loaded from a file the analysis never reads.
installer_imports = ["argparse", "copy", "ctypes", "ctypes.wintypes", "shutil"]
unused_qt = ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtWebEngineCore", "PySide6.QtOpenGL", "tkinter"]

app = Analysis(
    [str(ROOT / "packaging" / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=installer_imports,
    excludes=unused_qt + ["mind", "anthropic"],
)
cli = Analysis(
    [str(ROOT / "packaging" / "cli.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "mind" / "identity.example.md"), "mind")],
    hiddenimports=["companion.statusline", "companion.launcher", "mind.__main__"],
    # The status line starts this program every time Claude Code refreshes it, so nothing
    # whose runtime hook slows the start-up: setuptools and multiprocessing are never used.
    excludes=["PySide6", "shiboken6", "tkinter", "setuptools", "pkg_resources", "_distutils_hack", "distutils", "multiprocessing"],
)

app_exe = EXE(
    PYZ(app.pure),
    app.scripts,
    [],
    exclude_binaries=True,
    name="cc-companion",
    console=False,
    icon=ICON,
)
cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="cc-companion-cli",
    console=True,
    icon=ICON,
)
COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas, name="cc-companion")
