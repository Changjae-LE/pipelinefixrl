"""Structured logging for the base application (base-v2 / M-BE).

`LOG_FORMAT=json` (default) emits one JSON object per stdout line with the fixed
keys ``ts`` (ISO-8601 UTC), ``level``, ``logger``, ``msg`` — plus ``method`` /
``path`` / ``status`` for HTTP access lines. ``LOG_FORMAT=plain`` emits
human-readable lines. Uvicorn's ``access`` and ``error`` loggers are routed
through the same handler so every line on stdout shares one format.
"""

import datetime as _dt
import json
import logging
import sys

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # uvicorn.access records carry (client, method, path, http_version, status)
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            _client, method, path, _http_version, status = record.args
            obj["method"] = method
            obj["path"] = path
            try:
                obj["status"] = int(status)
            except (TypeError, ValueError):
                obj["status"] = status
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, separators=(",", ":"))


def configure_logging(fmt: str = "json", level: str = "info") -> None:
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    if str(fmt).lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(lvl)
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(lvl)
        lg.propagate = False
