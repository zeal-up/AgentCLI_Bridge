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
from typing import Any

from . import config

log = logging.getLogger(__name__)


def _run(sql: str, env: str | None = None, timeout: int = 180) -> list[dict]:
    env = env or config.DB_ENV
    cmd = [
        "lark-cli", "apps", "+db-execute",
        "--app-id", config.APP_ID,
        "--profile", config.LARK_PROFILE,
        "--as", "user",
        "--env", env,
        "--sql", sql,
        "--yes",
        "--format", "json",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"db-execute rc={p.returncode} stderr={p.stderr.strip()[:800]}")
    obj = json.loads(p.stdout)
    if not obj.get("ok"):
        err = obj.get("error", {})
        raise RuntimeError(f"db-execute error: {err.get('message') or err}")
    return obj["data"].get("results", [])


def query(sql: str, env: str | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as list of dicts."""
    out: list[dict[str, Any]] = []
    for r in _run(sql, env=env):
        if r.get("sql_type") == "SELECT" and r.get("data"):
            try:
                out.extend(json.loads(r["data"]))
            except json.JSONDecodeError:
                log.warning("non-JSON SELECT data: %s", r.get("data", "")[:200])
    return out


def execute(sql: str, env: str | None = None) -> int:
    """Run INSERT/UPDATE/DELETE (multi-statement ok). Return affected rows."""
    results = _run(sql, env=env)
    return sum((r.get("affected_rows") or 0) for r in results)


def sql_str(s: Any) -> str:
    """SQL-literal for a Python value (NULL-safe, single-quote escaped)."""
    if s is None:
        return "NULL"
    s = str(s).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"
