"""The loopback WebSocket feed: what it sends, and who it refuses."""

import json
import os
import sys
import time
from dataclasses import replace

import pytest

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication

from companion.feed import TOKEN_HEADER, EventFeed
from companion.model import DisplayState, Quota, State

LIVE = DisplayState(state=State.THINKING, session_id="s1", project="demo-project", sessions=1)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def spin(app, done, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return done()


@pytest.fixture
def feeds(app):
    made = []

    def make(token=None):
        feed = EventFeed(token=token)
        assert feed.listen(0)
        made.append(feed)
        return feed

    yield make
    for feed in made:
        feed.close()
    app.processEvents()


@pytest.fixture
def clients(app):
    made = []

    def connect(feed, origin="", token=None):
        client = QWebSocket(origin)
        client.received = []
        client.textMessageReceived.connect(client.received.append)
        request = QNetworkRequest(QUrl(f"ws://127.0.0.1:{feed.port}/"))
        if token is not None:
            request.setRawHeader(TOKEN_HEADER.encode(), token.encode())  # PySide6: bytes here, str to read
        client.open(request)
        made.append(client)
        return client

    yield connect
    for client in made:
        client.close()
    app.processEvents()


def connected(client):
    return client.state() == QAbstractSocket.SocketState.ConnectedState


def messages(client):
    return [json.loads(m) for m in client.received]


def test_feed_listens_on_loopback_only(feeds):
    assert feeds().server.serverAddress() == QHostAddress(QHostAddress.SpecialAddress.LocalHost)


def test_a_taken_port_is_reported(feeds):
    first = feeds()
    second = EventFeed()
    assert not second.listen(first.port)
    second.close()


def test_a_client_gets_the_display_on_connect_then_each_change_and_every_event(app, feeds, clients):
    feed = feeds()
    feed.publish_display(LIVE)
    client = clients(feed)
    assert spin(app, lambda: len(client.received) == 1)
    (hello,) = messages(client)
    assert hello["type"] == "display"
    assert (hello["display"]["state"], hello["display"]["project"]) == ("thinking", "demo-project")
    assert hello["display"]["quota"] == {
        "five_hour_pct": None, "five_hour_resets_at": None, "seven_day_pct": None,
        "seven_day_resets_at": None, "seen": False,
    }  # fmt: skip

    feed.publish_display(LIVE)  # unchanged: not sent again
    feed.publish_display(replace(LIVE, state=State.EDITING, tool_name="Edit", quota=Quota(five_hour_pct=23.0)))
    feed.publish_event({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Edit"})
    assert spin(app, lambda: len(client.received) == 3)
    _, change, event = messages(client)
    assert (change["display"]["state"], change["display"]["quota"]["five_hour_pct"]) == ("editing", 23.0)
    assert event == {"type": "event", "event": {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Edit"}}


def test_a_browser_origin_is_refused(app, feeds, clients):
    feed = feeds()
    client = clients(feed, origin="https://example.com")
    spin(app, lambda: connected(client), timeout=1.0)
    assert not connected(client)
    assert feed.clients == []


def test_a_token_is_required_when_set(app, feeds, clients):
    feed = feeds(token="s3cret")
    without = clients(feed)
    wrong = clients(feed, token="guess")
    right = clients(feed, token="s3cret")
    assert spin(app, lambda: connected(right))
    spin(app, lambda: not connected(without) and not connected(wrong), timeout=1.0)
    assert feed.clients and len(feed.clients) == 1
    assert not connected(without) and not connected(wrong)


def test_a_closed_client_is_dropped(app, feeds, clients):
    feed = feeds()
    client = clients(feed)
    assert spin(app, lambda: len(feed.clients) == 1)
    client.close()
    assert spin(app, lambda: feed.clients == [])
    feed.publish_event({"hook_event_name": "Stop", "session_id": "s1"})  # nobody left: no error
