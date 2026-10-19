"""Private, manually entered broker DCA plan registry; never submits trades."""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo


class CoreDcaService:
    def __init__(self, root: Path):
        self.path = root / "data/private/web_core_dca.sqlite3"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("""CREATE TABLE IF NOT EXISTS revisions (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at TEXT NOT NULL,
            plans_json TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS plan_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            locked INTEGER NOT NULL CHECK (locked IN (0, 1))
        )""")
        connection.execute("INSERT OR IGNORE INTO plan_state(id,locked) VALUES (1,0)")
        connection.commit()
        os.chmod(self.path, 0o600)
        return connection

    def current(self) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision,saved_at,plans_json FROM revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            locked = bool(connection.execute("SELECT locked FROM plan_state WHERE id=1").fetchone()[0])
        if row is None:
            return {"revision": 0, "saved_at": None, "plans": [], "locked": locked, "source": "manual_entry"}
        return {"revision": row[0], "saved_at": row[1],
                "plans": json.loads(row[2]), "locked": locked, "source": "manual_entry"}

    @staticmethod
    def validate(payload: object) -> list[dict]:
        if not isinstance(payload, dict) or not isinstance(payload.get("plans"), list):
            raise ValueError("定投计划格式无效")
        plans = payload["plans"]
        if len(plans) > 30:
            raise ValueError("最多登记30条定投计划")
        normalized = []
        for row in plans:
            if not isinstance(row, dict):
                raise ValueError("定投计划格式无效")
            code, cadence, amount = row.get("fund_code"), row.get("cadence"), row.get("amount_cny")
            if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
                raise ValueError("基金代码必须为6位数字")
            if cadence not in {"daily", "weekly", "monthly"}:
                raise ValueError("请选择每日、每周或每月")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
                raise ValueError("每次金额无效")
            if amount < 1 or amount > 5000 or round(amount, 2) != amount:
                raise ValueError("每次金额须为1至5000元，最多两位小数")
            day = row.get("day")
            if cadence == "daily":
                if day is not None:
                    raise ValueError("每日定投不需要指定日期")
            elif cadence == "weekly":
                if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 5:
                    raise ValueError("每周请选择周一至周五")
            elif isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
                raise ValueError("每月日期请选择1至28日")
            normalized.append({"fund_code": code, "cadence": cadence,
                               "day": day, "amount_cny": round(float(amount), 2)})
        return normalized

    def save(self, payload: object) -> dict:
        plans = self.validate(payload)
        expected = payload.get("revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("页面版本无效，请刷新后重试")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT locked FROM plan_state WHERE id=1").fetchone()[0]:
                raise ValueError("计划已锁定，请先解锁编辑")
            row = connection.execute("SELECT COALESCE(MAX(revision),0) FROM revisions").fetchone()
            if row[0] != expected:
                raise ValueError("计划已在另一页面更改，请刷新后重试")
            saved_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            cursor = connection.execute(
                "INSERT INTO revisions(saved_at,plans_json) VALUES (?,?)",
                (saved_at, json.dumps(plans, ensure_ascii=False, separators=(",", ":"))),
            )
        return {"revision": cursor.lastrowid, "saved_at": saved_at,
                "plans": plans, "locked": False, "source": "manual_entry"}

    def set_lock(self, payload: object) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("locked"), bool):
            raise ValueError("锁定状态无效")
        expected = payload.get("revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("页面版本无效，请刷新后重试")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision,plans_json FROM revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            revision = row[0] if row else 0
            if revision != expected:
                raise ValueError("计划已在另一页面更改，请刷新后重试")
            locked = bool(connection.execute("SELECT locked FROM plan_state WHERE id=1").fetchone()[0])
            if locked == payload["locked"]:
                raise ValueError("锁定状态已改变，请刷新后重试")
            saved_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            cursor = connection.execute(
                "INSERT INTO revisions(saved_at,plans_json) VALUES (?,?)",
                (saved_at, row[1] if row else "[]"),
            )
            connection.execute("UPDATE plan_state SET locked=? WHERE id=1", (int(payload["locked"]),))
        return {"revision": cursor.lastrowid, "saved_at": saved_at,
                "plans": json.loads(row[1]) if row else [], "locked": payload["locked"],
                "source": "manual_entry"}
