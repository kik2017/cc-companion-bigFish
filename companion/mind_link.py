"""The daemon's side of the persona's mind: start it, feed it, stop it.

The mind (python -m mind) is a child process whose stdin is the message pipe: the
daemon writes one JSON object per line (allowlisted events, display changes, pats and
the persona mode) and the mind answers through POST /say. Writes are buffered by Qt, so
a slow or stuck mind never blocks the event loop. Closing stdin tells it to exit.

It runs while the persona is On or Quiet. A crash restarts it after RESTART_DELAY_MS,
at most MAX_RESTARTS times an hour; exit code 3 (no API key, or the anthropic package
missing) leaves it off until the persona is switched on again. The daemon itself never
calls the network; only the mind does.
"""

from __future__ import annotations

import json
import logging
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer

from . import frozen
from . import settings as prefs

log = logging.getLogger("companion")

NOT_CONFIGURED = 3
RESTART_DELAY_MS = 5000
MAX_RESTARTS = 3


class MindLink(QObject):
    def __init__(
        self,
        say_token: str,
        port: int,
        program: str | None = None,
        args: list[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.say_token = say_token
        self.port = port
        default_program, default_args = frozen.mind_command()
        self.program = program or default_program
        self.args = args if args is not None else default_args
        self.mode = "off"
        self.process: QProcess | None = None
        self.restarts: list[float] = []
        self.stopping = False
        self.restart_timer = QTimer(self)
        self.restart_timer.setSingleShot(True)
        self.restart_timer.timeout.connect(self._start)

    def set_mode(self, mode: str) -> None:
        """Follow the Persona setting: off stops the mind, on and quiet start it and tell it which."""
        self.mode = mode
        if mode == "off":
            self.stop()
            return
        if not self.running():
            self.restarts.clear()
            self._start()
        self.send({"type": "persona", "mode": mode})

    def running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def send(self, message: dict) -> None:
        if not self.running():
            return
        try:
            line = json.dumps(message, ensure_ascii=False, allow_nan=False, default=str) + "\n"
        except ValueError:
            return
        self.process.write(line.encode("utf-8"))

    def stop(self, wait_ms: int = 2000) -> None:
        self.restart_timer.stop()
        if not self.running():
            return
        self.stopping = True
        self.process.closeWriteChannel()  # the mind exits at end of input
        if not self.process.waitForFinished(wait_ms):
            self.process.kill()
            self.process.waitForFinished(1000)
        self.stopping = False

    def _start(self) -> None:
        if self.mode == "off" or self.running():
            return
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CC_COMPANION_SAY_TOKEN", self.say_token)
        env.insert("CC_COMPANION_PORT", str(self.port))
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(frozen.home()))
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(str(prefs.app_dir() / "mind.err.log"), QProcess.OpenModeFlag.Append)
        process.finished.connect(self._finished)
        self.process = process
        process.start(self.program, self.args)
        if not process.waitForStarted(5000):
            log.warning("persona mind did not start: %s", process.errorString())
            return
        log.info("persona mind started (pid %d)", process.processId())
        if self.mode != "off":
            self.send({"type": "persona", "mode": self.mode})

    def restart(self) -> None:
        """Start the mind afresh (it reads its API key when it starts), if the persona is on."""
        self.stop()
        self.restarts.clear()
        self._start()

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        log.info("persona mind exited (%d)", code)
        if self.stopping or self.mode == "off":
            return
        if code == NOT_CONFIGURED and status == QProcess.ExitStatus.NormalExit:
            log.info("persona mind is not configured; see mind.log")
            return
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < 3600]
        if len(self.restarts) >= MAX_RESTARTS:
            log.warning("persona mind keeps exiting; not restarting it until the persona is switched on again")
            return
        self.restarts.append(now)
        self.restart_timer.start(RESTART_DELAY_MS)
