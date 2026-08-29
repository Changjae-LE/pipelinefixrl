import json
import logging

from app.obs import JsonFormatter


def _record(name, msg, args=()):
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, None)


def test_json_formatter_has_required_keys():
    obj = json.loads(JsonFormatter().format(_record("app", "hello world")))
    assert set(obj) >= {"ts", "level", "logger", "msg"}
    assert obj["level"] == "INFO"
    assert obj["logger"] == "app"
    assert obj["msg"] == "hello world"


def test_json_formatter_extracts_access_fields():
    rec = _record(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %s',
        ("127.0.0.1:0", "GET", "/health", "1.1", 200),
    )
    obj = json.loads(JsonFormatter().format(rec))
    assert obj["method"] == "GET"
    assert obj["path"] == "/health"
    assert obj["status"] == 200
