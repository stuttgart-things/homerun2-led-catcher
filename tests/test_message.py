"""Tests for Message and CaughtMessage models."""

from datetime import datetime

from led_catcher.models import CaughtMessage, Message


def test_message_from_dict_full():
    data = {
        "title": "Build failed",
        "message": "Pipeline #42 failed on main",
        "severity": "error",
        "author": "github",
        "timestamp": "1700000000",
        "system": "github",
        "tags": "ci,pipeline",
        "assigneeAddress": "dev@example.com",
        "assigneeName": "Dev Team",
        "artifacts": "log.txt",
        "url": "https://github.com/org/repo/actions/runs/42",
    }
    msg = Message.from_dict(data)
    assert msg.title == "Build failed"
    assert msg.severity == "error"
    assert msg.system == "github"
    assert msg.assignee_address == "dev@example.com"
    assert msg.assignee_name == "Dev Team"
    assert msg.url == "https://github.com/org/repo/actions/runs/42"


def test_message_from_dict_minimal():
    data = {"title": "Hello", "message": "World"}
    msg = Message.from_dict(data)
    assert msg.title == "Hello"
    assert msg.message == "World"
    assert msg.severity == "info"
    assert msg.author == "unknown"
    assert msg.system == "unknown"


def test_message_defaults():
    msg = Message()
    assert msg.severity == "info"
    assert msg.author == "unknown"
    assert msg.system == "unknown"
    assert msg.title == ""


def test_caught_message():
    msg = Message(title="Test", severity="warning", system="gitlab")
    caught = CaughtMessage(
        message=msg,
        object_id="event:123",
        stream_id="1700000000-0",
    )
    assert caught.message.title == "Test"
    assert caught.object_id == "event:123"
    assert caught.stream_id == "1700000000-0"
    assert isinstance(caught.caught_at, datetime)
