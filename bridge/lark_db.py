"""Wrapper around `lark-cli apps +db-execute` for outbound read/write to the
Miaoda hosted Postgres. All calls use the hermes profile + --as user + --yes.

The db-execute envelope returns:
  SELECT  -> data.results[*].data is a JSON string of rows (list[dict])
  DML     -> data.results[*].affected_rows
"""
from __future__ import annotations
import json
import logging
import subprocess
import time
from typing import Any

from . import config

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_RETRY_DELAYS_SEC = (1.0, 2.0, 4.0, 8.0)


class DbExecuteError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None


def _is_retryable_error(err: dict[str, Any] | None, fallback_text: str = "") -> bool:
    if not err:
        err = {}
    message = str(err.get("message") or fallback_text).lower()
    return bool(
        err.get("retryable")
        or err.get("subtype") == "rate_limit"
        or err.get("code") == 99991400
        or "frequency limit" in message
        or "rate limit" in message
    )


def _run(sql: str, env: str | None = None, timeout: int = 180) -> list[dict]:
    env = env or config.DB_ENV
    cmd = [
        "lark-cli", "apps", "+db-execute",
        "--app-id", config.APP_ID,
        "--profile", config.LARK_PROFILE,
        "--as", "user",
        "--environment", env,
        "--sql", sql,
        "--yes",
        "--format", "json",
    ]
    last_error: DbExecuteError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            body = _extract_json(p.stderr) or _extract_json(p.stdout) or {}
            err = body.get("error") if isinstance(body.get("error"), dict) else None
            retryable = _is_retryable_error(err, p.stderr or p.stdout)
            detail = (p.stderr or p.stdout).strip()[:800]
            last_error = DbExecuteError(
                f"db-execute rc={p.returncode} stderr={detail}",
                retryable=retryable,
            )
        else:
            try:
                obj = json.loads(p.stdout)
            except json.JSONDecodeError as exc:
                raise DbExecuteError(
                    f"db-execute returned non-JSON stdout: {p.stdout.strip()[:800]}"
                ) from exc

            if obj.get("ok"):
                # Normalize to a list of result objects.
                #   newer lark-cli (>=1.0.65):
                #     SELECT -> data is a list of rows already parsed
                #     DML    -> data is a single dict {"command":..,"rows_affected":N}
                #     multi-statement -> data is a list of per-stmt dicts
                #   older lark-cli: data is {"results": [ {sql_type, data}, ... ]}
                data = obj.get("data")
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    if "results" in data and isinstance(data["results"], list):
                        return data["results"]
                    return [data]
                return []

            err = obj.get("error", {})
            retryable = _is_retryable_error(err if isinstance(err, dict) else None)
            last_error = DbExecuteError(
                f"db-execute error: {err.get('message') if isinstance(err, dict) else err}",
                retryable=retryable,
            )

        if not last_error.retryable or attempt >= _MAX_ATTEMPTS:
            break
        delay = _RETRY_DELAYS_SEC[min(attempt - 1, len(_RETRY_DELAYS_SEC) - 1)]
        log.warning(
            "db-execute retryable failure on attempt %d/%d; retrying in %.1fs: %s",
            attempt,
            _MAX_ATTEMPTS,
            delay,
            last_error,
        )
        time.sleep(delay)

    assert last_error is not None
    raise last_error


def query(sql: str, env: str | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as list of dicts.

    Handles two lark-cli response shapes:
      - newer (>=1.0.65): data is a list of result rows already parsed
        (e.g. [{"id": "...", "agent": "copilot"}]).
      - older: data is a list of wrappers {"sql_type": "SELECT",
        "data": "<json string of rows>"}.
    """
    out: list[dict[str, Any]] = []
    for r in _run(sql, env=env):
        if not isinstance(r, dict):
            continue
        # Newer shape: a real result row (no sql_type wrapper, or data is a
        # list/dict already). Treat it as a row directly.
        if "sql_type" not in r:
            out.append(r)
            continue
        # Older shape: wrapper with a JSON-string data field.
        if r.get("sql_type") == "SELECT" and r.get("data"):
            try:
                out.extend(json.loads(r["data"]))
            except json.JSONDecodeError:
                log.warning("non-JSON SELECT data: %s", r.get("data", "")[:200])
    return out


def execute(sql: str, env: str | None = None) -> int:
    """Run INSERT/UPDATE/DELETE (multi-statement ok). Return affected rows."""
    results = _run(sql, env=env)
    return sum(
        (r.get("rows_affected") or r.get("affected_rows") or 0)
        for r in results
        if isinstance(r, dict)
    )


def sql_str(s: Any) -> str:
    """SQL-literal for a Python value (NULL-safe, single-quote escaped)."""
    if s is None:
        return "NULL"
    s = str(s).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"
