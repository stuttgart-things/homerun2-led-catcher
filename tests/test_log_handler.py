"""Tests for the log handler."""

import logging
from datetime import datetime, timezone

from led_catcher.handlers.log_handler import log_handler
from led_catcher.models import CaughtMessage, Message


def test_log_handler_logs_at_correct_level(caplog):
    msg = Message(title="Build failed", severity="error", system="github")
    caught = CaughtMessage(message=msg, object_id="event:1", stream_id="1-0")

    with caplog.at_level(logging.DEBUG):
        log_handler(caught)

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert "Build failed" in caplog.records[0].message


def test_log_handler_info_severity(caplog):
    msg = Message(title="Deploy succeeded", severity="info", system="ansible")
    caught = CaughtMessage(message=msg, object_id="event:2", stream_id="2-0")

    with caplog.at_level(logging.DEBUG):
        log_handler(caught)

    assert caplog.records[0].levelno == logging.INFO


def test_log_handler_success_maps_to_info(caplog):
    msg = Message(title="All good", severity="success", system="sthings")
    caught = CaughtMessage(message=msg, object_id="event:3", stream_id="3-0")

    with caplog.at_level(logging.DEBUG):
        log_handler(caught)

    assert caplog.records[0].levelno == logging.INFO


def test_log_handler_skips_stale_message(caplog):
    # Timestamp from far in the past
    msg = Message(title="Old event", severity="info", timestamp="1000000000")
    caught = CaughtMessage(message=msg, object_id="event:4", stream_id="4-0")

    with caplog.at_level(logging.DEBUG):
        log_handler(caught)

    # Should log at debug level about skipping, not at info level
    assert all("stale" in r.message or r.levelno == logging.DEBUG for r in caplog.records)


def test_log_handler_fresh_message(caplog):
    # Fresh timestamp
    ts = str(datetime.now(timezone.utc).timestamp())
    msg = Message(title="Fresh event", severity="warning", timestamp=ts)
    caught = CaughtMessage(message=msg, object_id="event:5", stream_id="5-0")

    with caplog.at_level(logging.DEBUG):
        log_handler(caught)

    assert any(r.levelno == logging.WARNING for r in caplog.records)
