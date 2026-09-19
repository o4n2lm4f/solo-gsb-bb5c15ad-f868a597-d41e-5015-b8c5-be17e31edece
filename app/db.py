"""SQLite 数据访问层。

启动时调用 ``init_db()`` 建表并写入演示数据，保证开箱即可看到
断档 / 重叠 / 逆序三类审计效果。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Iterator

DB_PATH = os.environ.get("AUDIT_DB_PATH", os.path.join(os.path.dirname(__file__), "audit.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI 请求级依赖：请求结束自动提交并关闭连接。"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id  INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    custodian  TEXT    NOT NULL,
    start_time TEXT    NOT NULL,
    end_time   TEXT,
    note       TEXT    NOT NULL DEFAULT '',
    seq        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_sample ON events(sample_id);

CREATE TABLE IF NOT EXISTS signoffs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id     INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    signer        TEXT    NOT NULL,
    signed_at     TEXT    NOT NULL,
    remark        TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'active',
    revoke_reason TEXT,
    revoked_at    TEXT,
    snapshot      TEXT    NOT NULL,
    digest        TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signoffs_sample ON signoffs(sample_id);

-- 双向交接确认：from/to_event_id 故意不做外键——事件被删除后，
-- 失效确认记录仍需保留供追溯（样本删除则级联清理）。
CREATE TABLE IF NOT EXISTS handover_confirmations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id      INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    pair_key       TEXT    NOT NULL,
    from_event_id  INTEGER NOT NULL,
    to_event_id    INTEGER NOT NULL,
    event_digest   TEXT    NOT NULL,
    event_summary  TEXT    NOT NULL,
    time_match     INTEGER NOT NULL DEFAULT 0,
    out_name       TEXT,
    out_at         TEXT,
    in_name        TEXT,
    in_at          TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending',
    invalid_reason TEXT,
    invalidated_at TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_handovers_sample ON handover_confirmations(sample_id);

-- 同一交接项同一时间只允许一条有效（非失效）确认记录
CREATE UNIQUE INDEX IF NOT EXISTS idx_handovers_active_pair
    ON handover_confirmations(sample_id, pair_key) WHERE status != 'invalid';
"""


def init_db() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        _seed_demo_data(conn)


def _seed_demo_data(conn: sqlite3.Connection) -> None:
    """数据库为空时写入两个演示样本（其中一个故意包含断档与重叠）。"""
    if conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] > 0:
        return

    today = date.today()
    base = datetime.combine(today, datetime.min.time()).replace(hour=9)

    def t(h: float, m: float = 0) -> str:
        return (base + timedelta(hours=h, minutes=m)).strftime("%Y-%m-%d %H:%M:%S")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO samples (code, name, description, created_at) VALUES (?, ?, ?, ?)",
        ("SAM-DEMO-001", "演示样本·异常链路", "用于演示断档、重叠与逆序录入的检测效果", now),
    )
    sid = cur.lastrowid
    rows = [
        ("张三（采集组）", t(0), t(1), "现场采集并密封"),
        ("李四（运输组）", t(1), t(2, 30), "冷链车运输，温度 2-8℃"),
        # 录入顺序逆序（第三条先登记，时间却最早）
        ("赵六（档案室）", t(0, 30), t(0, 45), "事后补录的档案登记——时间早于录入顺序"),
        # 与上一条之间存在 40 分钟无人保管断档
        ("王五（检测组）", t(3, 10), t(4, 30), "实验室接收并检测"),
        # 与王五区间重叠
        ("孙七（留样柜）", t(4), t(5, 15), "检测期间并行留样"),
        # 仍在保管中，未归还
        ("周八（留样柜）", t(5, 15), None, "留样观察中"),
    ]
    for seq, (custodian, start, end, note) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO events (sample_id, custodian, start_time, end_time, note, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, custodian, start, end, note, seq),
        )

    cur = conn.execute(
        "INSERT INTO samples (code, name, description, created_at) VALUES (?, ?, ?, ?)",
        ("SAM-DEMO-002", "演示样本·完整链路", "各段交接无缝衔接，当前仍在保管中", now),
    )
    sid = cur.lastrowid
    rows2 = [
        ("张三（采集组）", t(0), t(1), "现场采集"),
        ("李四（运输组）", t(1), t(2, 30), "冷链运输"),
        ("王五（检测组）", t(2, 30), None, "检测中，尚未归还"),
    ]
    for seq, (custodian, start, end, note) in enumerate(rows2, start=1):
        conn.execute(
            "INSERT INTO events (sample_id, custodian, start_time, end_time, note, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, custodian, start, end, note, seq),
        )
