"""The tray icon: her face in the notification area, and the same menu as a right-click on her.

Left-click shows or hides her. The right-click menu starts with what only the tray can do:
Show her or Hide her, Set API key (apikey.py), and Connect to Claude Code (connect.py,
which asks first, since it changes Claude Code's settings). The pet's own menu follows,
built fresh on every open so each check mark is current. The icon is her head, cut from
the first frame of her idle clip, so a skin pack brings its own.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QRect, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from . import apikey, connect
from . import skin as skins
from .model import State

log = logging.getLogger("companion")

ICON_PX = 64
CONNECT_QUESTION = (
    "Connect cc-companion to Claude Code?\n\n"
    "This adds the companion's hooks and status line to your Claude Code settings "
    "(~/.claude/settings.json), keeping everything already there and saving a backup first. "
    "Claude Code then wakes her when a session starts and shows her what it is doing. "
    "The hooks only observe: they never approve, block or change anything.\n\n"
    "You can disconnect from this menu at any time."
)
API_KEY_PROMPT = (
    "Her own words need an Anthropic API key (from console.anthropic.com).\n"
    "It is saved only as your Windows user environment variable CC_COMPANION_API_KEY.\n"
    "Each line she says costs about $0.0001-0.0003."
)


def face(skin: skins.Skin, px: int = ICON_PX) -> QIcon:
    """Her head from the first idle frame, as a square icon; the whole frame if the skin has no head zone."""
    image = face_image(skin, px)
    return QIcon() if image.isNull() else QIcon(QPixmap.fromImage(image))


def face_image(skin: skins.Skin, px: int = ICON_PX) -> QImage:
    """The image behind face(); packaging/build.py also saves it as the programs' .ico."""
    image = QImage(str(skin.state_clip(State.IDLE).frames[0]))
    if image.isNull():
        return image
    head = next((zone for zone in skin.zones if zone.name == "head"), None)
    if head is not None:
        w, h = image.width(), image.height()
        box = QRect(round(head.left * w), round(head.top * h), round((head.right - head.left) * w), round((head.bottom - head.top) * h))
        side = max(box.width(), box.height())
        square = QRect(box.center().x() - side // 2, box.center().y() - side // 2, side, side).intersected(image.rect())
        image = image.copy(square)
    return image.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def ask_api_key(parent: QWidget | None) -> str | None:
    key, ok = QInputDialog.getText(parent, "Her API key", API_KEY_PROMPT, QLineEdit.EchoMode.Password)
    return key if ok else None


def confirm_connect(parent: QWidget | None) -> bool:
    answer = QMessageBox.question(parent, "Connect to Claude Code", CONNECT_QUESTION)
    return answer == QMessageBox.StandardButton.Yes


class Tray(QObject):
    def __init__(self, window, on_api_key: Callable[[], None], on_show: Callable[[], None]) -> None:
        """`window` is the pet; `on_api_key` runs after a new key is saved; `on_show` after Show her."""
        super().__init__(window)
        self.window = window
        self.on_api_key = on_api_key
        self.on_show = on_show
        # Dialogs and the settings file are swapped in tests, so nothing opens or changes for real.
        self.ask_api_key: Callable[[QWidget | None], str | None] = ask_api_key
        self.save_api_key: Callable[[str], bool] = apikey.save_api_key
        self.has_api_key: Callable[[], bool] = apikey.has_api_key
        self.confirm_connect: Callable[[QWidget | None], bool] = confirm_connect
        self.settings_path = None  # None: Claude Code's own settings file
        self.icon = QSystemTrayIcon(face(window.skin), self)
        self.icon.setToolTip("cc-companion")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.fill)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)

    def show(self) -> None:
        self.icon.show()

    def fill(self) -> None:
        """Rebuild the menu: the tray's own items, then the pet's menu."""
        for old in self.menu.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly):
            old.deleteLater()
        self.menu.clear()
        shown = self.window.isVisible()
        self.menu.addAction("Hide her" if shown else "Show her", self.toggle).setObjectName("tray:toggle")
        self.menu.addAction("Change API key…" if self.has_api_key() else "Set API key…", self.set_api_key).setObjectName("tray:apikey")
        connected = self._connected()
        item = self.menu.addAction("Connected to Claude Code" if connected else "Connect to Claude Code…")
        item.setObjectName("tray:connect")
        item.setCheckable(True)
        item.setChecked(connected)
        item.triggered.connect(lambda _checked=False: self.disconnect() if connected else self.connect())
        self.menu.addSeparator()
        self.window.build_menu(self.menu)

    def toggle(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.on_show()

    def set_api_key(self) -> bool:
        key = self.ask_api_key(None)
        if key is None:
            return False
        if not self.save_api_key(key):
            self.icon.showMessage("cc-companion", "That key could not be saved. Paste it again, without spaces.")
            return False
        log.info("api key saved to the user environment")
        self.on_api_key()
        self.icon.showMessage("cc-companion", "Key saved. Turn her on from Persona > On if she is off.")
        return True

    def connect(self) -> bool:
        if not self.confirm_connect(None):
            return False
        done, notes = connect.connect(self.settings_path)
        log.info("connect to claude code: %s", "done" if done else "failed")
        message = "Connected. New Claude Code sessions will wake her." if done else "Could not connect."
        self.icon.showMessage("cc-companion", " ".join([message] + notes))
        return done

    def disconnect(self) -> bool:
        done, notes = connect.disconnect(self.settings_path)
        log.info("disconnect from claude code: %s", "done" if done else "failed")
        self.icon.showMessage("cc-companion", "Disconnected from Claude Code." if done else " ".join(["Could not disconnect."] + notes))
        return done

    def _connected(self) -> bool:
        try:
            return connect.is_connected(self.settings_path)
        except (OSError, ValueError):
            return False

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle()
