"""Entry point: python -m companion.

Binds the loopback server first, so a second daemon exits quietly before any window
appears, then runs the Qt event loop. The server thread hands events over through a
queued Signal; only the main thread touches the store and the window.

Quit from the menu hides the pet but keeps the listener up until no session is live.
Hooks still point here, and a hook to a closed port is refused at once but shows an
error notice, so exiting while sessions run would add a notice to every hook they fire.
"Hide until all sessions end" leaves the same way, but no new session brings the pet back.

Each run makes a fresh /say token and leaves it in the app data folder, where local
tools and the persona read it; a line that passes the server goes to the window.
While the persona is On or Quiet, the persona's mind runs as a child process
(mind_link.py) and gets every allowlisted event, display change and pat, and what you
type to her in the chat box. Her tray icon (tray.py) carries the same menu, plus Show
her, Set API key and Connect to Claude Code.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import secrets
import sys
import time
from collections.abc import Callable
from time import perf_counter  # by name: tests swap the module's `time` for a fake clock

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import connect, frozen
from . import settings as prefs
from .model import State, Store
from .reducer import next_transition, project, reduce
from .server import SAY_INTERVAL_S, CompanionServer, port_from_env
from .feed import EventFeed, events_port_from_env
from .mind_link import MindLink
from .tray import Tray
from .window import CompanionWindow

log = logging.getLogger("companion")

# A session the user starts, resumes or forks brings a quit pet back, as a fresh launch
# would; /clear and compaction happen inside a session and leave it hidden. A pet hidden
# until all sessions end stays hidden whatever the source.
RETURN_SOURCES = {"startup", "resume", "fork"}

# Stops a session may finish without a status line report before the probe logs it. The
# first turn can end before the status line's first run, so one Stop proves nothing.
PROBE_AFTER_STOPS = 2

# QTimer.start() raises OverflowError past a signed 32-bit interval (about 24.8 days). A real
# quota reset is at most 7 days out, but the server accepts resets_at up to 1e12.
MAX_TIMER_MS = 2**31 - 1

FIRST_RUN_HINT = "I'm here. Right-click this icon and choose Connect to Claude Code to wake me with your sessions."


class Controller(QObject):
    event_received = Signal(object)
    say_received = Signal(object)

    def __init__(
        self,
        window: CompanionWindow,
        server: CompanionServer,
        exit_app: Callable[[], None] = QApplication.quit,
        feed: EventFeed | None = None,
        mind: MindLink | None = None,
    ) -> None:
        super().__init__()
        self.window = window
        self.server = server
        self.feed = feed
        self.mind = mind
        self._mind_display: dict | None = None
        self.exit_app = exit_app
        self.store = Store()
        self.shown_state: State | None = None
        self.leaving = False
        # Set by "Hide until all sessions end": leaving, and new sessions do not cancel it.
        self.hidden_for_run = False
        # Status line probe: Stop count per session, and the sessions already logged.
        self._stops: dict[str, int] = {}
        self._probed: set[str] = set()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.event_received.connect(self.on_event)
        self.say_received.connect(self.on_say)
        window.quit_requested.connect(self.request_quit)
        window.hide_requested.connect(self.request_hide)
        if mind is not None:
            window.patted.connect(lambda zone: mind.send({"type": "pat", "zone": zone}))
            window.persona_changed.connect(self.on_persona)
            window.chattiness_changed.connect(lambda level: mind.send({"type": "chattiness", "level": level}))
            window.private_changed.connect(lambda day: mind.send({"type": "private", "day": day}))
            window.chat_sent.connect(self.on_chat)
            window.no_gap_changed.connect(self.on_no_gap)
            window.limit_changed.connect(lambda limit: mind.send({"type": "limit", "limit": limit}))
            window.search_changed.connect(lambda search: mind.send({"type": "search", "search": search}))

    @Slot(object)
    def on_event(self, event: dict) -> None:
        # The receipt stamp is timing data for this process, never session state.
        received_at = event.pop("_rx", None)
        if self.feed is not None:
            self.feed.publish_event(event)
        if self.mind is not None:
            self.mind.send({"type": "event", "event": event})
        self.store = reduce(self.store, event, time.time())
        if (
            self.leaving
            and not self.hidden_for_run
            and event.get("hook_event_name") == "SessionStart"
            and event.get("source") in RETURN_SOURCES
        ):
            self.leaving = False
            self.window.show()
            log.info("new session; quit cancelled")
        self._probe_status(event)
        self._render(received_at)

    @Slot(str)
    def on_persona(self, mode: str) -> None:
        self._mind_display = None  # a mind that starts now needs the display too
        self.mind.set_mode(mode)
        self.refresh()

    @Slot(str)
    def on_chat(self, text: str) -> None:
        """A line typed to her: to the mind, with leave for one reply past the /say interval. Never logged."""
        if self.mind is None or not self.mind.running():
            return  # the chat box times out and says there was no answer
        self.server.grant_reply()
        self.mind.send({"type": "chat", "text": text})

    @Slot(bool)
    def on_no_gap(self, on: bool) -> None:
        """No pause: the /say interval goes, and the mind drops its own gaps and the pat cooldown."""
        self.server.set_say_interval(0.0 if on else SAY_INTERVAL_S)
        if self.mind is not None:
            self.mind.send({"type": "no_gap", "on": on})
        log.info("no pause %s", "on" if on else "off")

    @Slot(object)
    def on_say(self, speech: dict) -> None:
        """A line that passed /say: {"text", "mood", "ttl_s", "reply"}."""
        self.window.say(speech["text"], speech["mood"], speech["ttl_s"], reply=speech.get("reply", False))

    @Slot()
    def request_quit(self) -> None:
        self.leaving = True
        self.window.hide()
        log.info("quit requested; listening until live sessions end")
        self.refresh()

    @Slot()
    def request_hide(self) -> None:
        self.hidden_for_run = self.leaving = True
        self.window.hide()
        log.info("hide requested; hidden until live sessions end")
        self.refresh()

    @Slot()
    def cancel_leaving(self) -> None:
        """Show her from the tray: a pending Quit or "Hide until all sessions end" is called off."""
        if self.leaving:
            log.info("shown from the tray; quit cancelled")
        self.leaving = self.hidden_for_run = False
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        self._render(None)

    def _render(self, received_at: float | None) -> None:
        """Project and show the store; `received_at` is the event's server receipt time, if any."""
        now = time.time()
        display = project(self.store, now)
        self.server.session_count = display.sessions
        if self.leaving and display.sessions == 0:
            self.timer.stop()
            self.exit_app()
            return
        if display.state is not self.shown_state:
            self.shown_state = display.state
            log.info("state %s session=%s", display.state.value, (display.session_id or "-")[:8])
        if self.feed is not None:
            self.feed.publish_display(display)
        if self.mind is not None and self.mind.running():
            shown = dataclasses.asdict(display)
            if shown != self._mind_display:
                self._mind_display = shown
                self.mind.send({"type": "display", "display": shown})
        repainted = self.window.set_display(display)
        # Receipt to the end of the bubble's synchronous repaint, for /health.
        if repainted and received_at is not None:
            self.server.record_paint((perf_counter() - received_at) * 1000)
        # Overlays (error flash, done hold), expiry and quota resets change the display with no event.
        upcoming = next_transition(self.store, now)
        if upcoming is None:
            self.timer.stop()
        else:
            self.timer.start(min(max(0, int((upcoming - now) * 1000) + 5), MAX_TIMER_MS))

    def _probe_status(self, event: dict) -> None:
        """Log once per session that keeps finishing turns with no status line report.

        After install, the log then shows which hosts (such as the Desktop Code tab) never
        run the status line, with no test project needed.
        """
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        if event.get("hook_event_name") == "SessionEnd":
            self._stops.pop(session_id, None)
            self._probed.discard(session_id)
            return
        if event.get("hook_event_name") != "Stop":
            return
        self._stops[session_id] = stops = self._stops.get(session_id, 0) + 1
        rec = self.store.sessions.get(session_id)
        if stops >= PROBE_AFTER_STOPS and rec is not None and not rec.status_seen and session_id not in self._probed:
            self._probed.add(session_id)
            log.info("no status line report for session=%s", session_id[:8])


def main(argv: list[str] | None = None) -> int:
    prefs.setup_logging()
    port = port_from_env()
    try:
        server = CompanionServer(sink=lambda event: None, port=port, token=os.environ.get("CC_COMPANION_TOKEN"))
    except OSError:
        log.info("port %d already in use; another companion is running", port)
        return 0

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(prefs.APP_NAME)
    settings = prefs.load()
    window = CompanionWindow(settings)
    feed = None
    if settings.events_feed:
        feed = EventFeed(token=os.environ.get("CC_COMPANION_TOKEN"))
        events_port = events_port_from_env()
        if feed.listen(events_port):
            log.info("events feed on ws://127.0.0.1:%d/", feed.port)
        else:
            log.warning("events feed port %d is taken; the feed stays off", events_port)
            feed = None
    # Written only once this process holds the port, so a second daemon never replaces it.
    say_token = secrets.token_urlsafe(32)
    prefs.write_say_token(say_token)
    mind = MindLink(say_token, port)
    controller = Controller(window, server, feed=feed, mind=mind)
    server.sink = controller.event_received.emit
    server.say_sink = controller.say_received.emit
    server.say_enabled = lambda: window.settings.persona != "off"
    server.say_token = say_token
    if prefs.read_no_gap():
        server.set_say_interval(0.0)
    controller.refresh()
    window.show()
    # One-off native window, glyph and first-frame work happens before serving, so the first
    # hook request does not wait on it; requests meanwhile queue on the bound socket.
    window.bubble.warm_up()
    app.processEvents()
    server.start_thread()
    log.info("daemon up on 127.0.0.1:%d, window at %d,%d", port, window.x(), window.y())
    if settings.persona != "off":
        controller.on_persona(settings.persona)
    tray = Tray(window, on_api_key=mind.restart, on_show=controller.cancel_leaving)
    window.api_key_needed.connect(tray.set_api_key)
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()
        if frozen.is_frozen() and not connect.is_connected():
            tray.icon.showMessage("cc-companion", FIRST_RUN_HINT)

    code = app.exec()
    mind.stop()
    if feed is not None:
        feed.close()
    server.shutdown()
    server.server_close()
    log.info("daemon stopped")
    return code


if __name__ == "__main__":
    sys.exit(main())
