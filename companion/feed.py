"""Optional loopback WebSocket feed of what the pet shows, for other local tools. Off by default.

    "events_feed": true in settings.json, then ws://127.0.0.1:47622/
    (CC_COMPANION_EVENTS_PORT moves it)

Every message is one JSON object:

    {"type": "display", "display": {...}}   the DisplayState, on connect and on each change
    {"type": "event", "event": {...}}       each hook or status line event, as allowlisted
                                            by server.py, so it carries no prompt, tool or
                                            transcript content

Any web page can open a WebSocket to localhost, so a handshake that carries an Origin
header (which browsers always send and native clients do not) is refused. When
CC_COMPANION_TOKEN is set, a client must also send it in an X-Companion-Token header.
Qt objects: main thread only. The feed never sends anything back into Claude Code.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import os

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketCorsAuthenticator, QWebSocketServer

from .model import DisplayState

DEFAULT_EVENTS_PORT = 47622
TOKEN_HEADER = "X-Companion-Token"

log = logging.getLogger("companion")


def events_port_from_env() -> int:
    try:
        return int(os.environ.get("CC_COMPANION_EVENTS_PORT", DEFAULT_EVENTS_PORT))
    except ValueError:
        return DEFAULT_EVENTS_PORT


def display_message(display: DisplayState) -> str:
    return json.dumps({"type": "display", "display": dataclasses.asdict(display)}, allow_nan=False)


def event_message(event: dict) -> str:
    return json.dumps({"type": "event", "event": event}, allow_nan=False)


class EventFeed(QObject):
    def __init__(self, token: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.token = token
        self.clients: list[QWebSocket] = []
        self.last_display: str | None = None
        self.server = QWebSocketServer("cc-companion", QWebSocketServer.SslMode.NonSecureMode, self)
        self.server.originAuthenticationRequired.connect(self._check_origin)
        self.server.newConnection.connect(self._accept)

    def listen(self, port: int) -> bool:
        """Bind 127.0.0.1:`port` (0 picks a free port); False if the port is taken."""
        return self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), port)

    @property
    def port(self) -> int:
        return self.server.serverPort()

    def close(self) -> None:
        for client in list(self.clients):
            client.close()
        self.server.close()

    def publish_display(self, display: DisplayState) -> None:
        message = display_message(display)
        if message != self.last_display:
            self.last_display = message
            self._send(message)

    def publish_event(self, event: dict) -> None:
        if self.clients:
            self._send(event_message(event))

    def _send(self, message: str) -> None:
        for client in self.clients:
            client.sendTextMessage(message)

    def _check_origin(self, authenticator: QWebSocketCorsAuthenticator) -> None:
        authenticator.setAllowed(not authenticator.origin())

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            sent = bytes(client.request().rawHeader(TOKEN_HEADER)).decode("latin-1")
            if client.origin() or (self.token and not hmac.compare_digest(sent, self.token)):
                log.info("events feed refused a client")
                client.close()
                client.deleteLater()
                continue
            self.clients.append(client)
            client.disconnected.connect(lambda c=client: self._drop(c))
            if self.last_display is not None:
                client.sendTextMessage(self.last_display)

    def _drop(self, client: QWebSocket) -> None:
        if client in self.clients:
            self.clients.remove(client)
            client.deleteLater()
