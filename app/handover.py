"""双向交接确认。

系统按审计排序（起始时间）为每对相邻交接事件生成一个“交接项”，
交出方与接收方需分别填写姓名与确认时间：

* 两侧都确认且上一段结束时间等于下一段开始时间 -> 已确认（confirmed）
* 两侧都确认但时间不相等（断档 / 重叠 / 未归还）-> 时间不一致（mismatch）
* 否则                                           -> 待确认（pending）

每条确认记录绑定两条事件当时的关键字段摘要（SHA-256）与快照；
任一事件随后被编辑或删除时，原确认记录保留为失效（invalid）状态，
不再计入有效统计，需对当前交接项重新确认。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from sqlite3 import Connection
from typing import Any, Optional

from .audit import ParsedEvent, build_handover_pairs, fmt_duration

STATUS_TEXT = {
    "pending": "待确认",
    "confirmed": "已确认",
    "mismatch": "时间不一致",
    "invalid": "已失效",
}


class HandoverError(Exception):
    """交接确认业务错误（由路由层转换为 HTTP 409）。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 事件摘要与校验值 ----------

def _event_key_fields(e: ParsedEvent) -> dict[str, Any]:
    """确认绑定的关键字段：任一变化都会使原确认失效。"""
    return {
        "id": e.id,
        "seq": e.seq,
        "custodian": e.custodian,
        "start_time": e.start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": e.end.strftime("%Y-%m-%d %H:%M:%S") if e.end else None,
        "note": e.note,
    }


def _pair_payload(a: ParsedEvent, b: ParsedEvent) -> str:
    return json.dumps(
        {"from": _event_key_fields(a), "to": _event_key_fields(b)},
        ensure_ascii=False,
        sort_keys=True,
    )


def pair_digest(a: ParsedEvent, b: ParsedEvent) -> str:
    return hashlib.sha256(_pair_payload(a, b).encode("utf-8")).hexdigest()


def _pair_key(a: ParsedEvent, b: ParsedEvent) -> str:
    return f"{a.id}:{b.id}"


def _time_match(a: ParsedEvent, b: ParsedEvent) -> bool:
    """上一段结束时间等于下一段开始时间（开放保管不算）。"""
    return a.end is not None and a.end == b.start


def _time_note(a: ParsedEvent, b: ParsedEvent) -> str:
    if a.end is None:
        return "上一段尚未归还（开放保管）"
    delta = (b.start - a.end).total_seconds()
    if delta == 0:
        return "无缝衔接"
    if delta > 0:
        return f"断档 {fmt_duration(delta)}"
    return f"重叠 {fmt_duration(-delta)}"


# ---------- 失效同步 ----------

def sync_handovers(conn: Connection, sample_id: int, events: list[dict[str, Any]]) -> None:
    """把因事件编辑 / 删除 / 交接关系变化而失效的确认标记为 invalid（幂等）。

    失效记录永久保留，即使事件之后被改回原样也不恢复，必须重新确认。
    """
    pairs = build_handover_pairs(events)
    current = {_pair_key(a, b): pair_digest(a, b) for a, b in pairs}
    rows = conn.execute(
        "SELECT id, pair_key, event_digest FROM handover_confirmations "
        "WHERE sample_id = ? AND status != 'invalid'",
        (sample_id,),
    ).fetchall()
    now = _now()
    for r in rows:
        digest = current.get(r["pair_key"])
        if digest is None:
            reason = "关联事件已删除或交接关系变化"
        elif digest != r["event_digest"]:
            reason = "关联事件已被编辑，关键字段发生变化"
        else:
            continue
        conn.execute(
            "UPDATE handover_confirmations SET status = 'invalid', invalid_reason = ?, "
            "invalidated_at = ?, updated_at = ? WHERE id = ?",
            (reason, now, now, r["id"]),
        )


# ---------- 视图 ----------

def _event_brief(e: ParsedEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "seq": e.seq,
        "custodian": e.custodian,
        "start_time": e.start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": e.end.strftime("%Y-%m-%d %H:%M:%S") if e.end else None,
        "open": e.end is None,
        "note": e.note,
    }


def _record_view(row: Any) -> dict[str, Any]:
    """确认记录视图（含失效记录）：事件信息取自确认时保存的快照。"""
    summary = json.loads(row["event_summary"])
    return {
        "id": row["id"],
        "pair_key": row["pair_key"],
        "from_event": summary["from"],
        "to_event": summary["to"],
        "time_match": bool(row["time_match"]),
        "out_name": row["out_name"],
        "out_at": row["out_at"],
        "in_name": row["in_name"],
        "in_at": row["in_at"],
        "status": row["status"],
        "invalid_reason": row["invalid_reason"],
        "invalidated_at": row["invalidated_at"],
        "created_at": row["created_at"],
        "digest": row["event_digest"][:12],
    }


def _item_view(a: ParsedEvent, b: ParsedEvent, rec: Optional[Any]) -> dict[str, Any]:
    item = {
        "pair_key": _pair_key(a, b),
        "from_event": _event_brief(a),
        "to_event": _event_brief(b),
        "time_match": _time_match(a, b),
        "time_note": _time_note(a, b),
        "status": "pending",
        "confirmation": None,
    }
    if rec is not None:
        item["status"] = rec["status"]
        item["confirmation"] = {
            "id": rec["id"],
            "out_name": rec["out_name"],
            "out_at": rec["out_at"],
            "in_name": rec["in_name"],
            "in_at": rec["in_at"],
        }
    return item


def list_handovers(
    conn: Connection, sample_id: int, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """交接项列表 + 失效确认记录 + 统计。失效记录不计入有效状态。"""
    sync_handovers(conn, sample_id, events)
    pairs = build_handover_pairs(events)
    rows = conn.execute(
        "SELECT * FROM handover_confirmations WHERE sample_id = ? ORDER BY id",
        (sample_id,),
    ).fetchall()
    active: dict[str, Any] = {}
    invalid: list[dict[str, Any]] = []
    for r in rows:
        if r["status"] == "invalid":
            invalid.append(_record_view(r))
        else:
            active[r["pair_key"]] = r
    items = [_item_view(a, b, active.get(_pair_key(a, b))) for a, b in pairs]
    summary = {
        "total": len(items),
        "confirmed": sum(1 for it in items if it["status"] == "confirmed"),
        "mismatch": sum(1 for it in items if it["status"] == "mismatch"),
        "pending": sum(1 for it in items if it["status"] == "pending"),
        "invalid": len(invalid),
    }
    return {"items": items, "invalid": invalid, "summary": summary}


# ---------- 确认操作 ----------

def confirm_handover(
    conn: Connection,
    sample_id: int,
    events: list[dict[str, Any]],
    from_event_id: int,
    to_event_id: int,
    side: str,
    name: str,
    confirmed_at: str,
) -> dict[str, Any]:
    """确认交接项的一侧（out=交出方 / in=接收方），返回更新后的交接项。"""
    sync_handovers(conn, sample_id, events)
    pairs = build_handover_pairs(events)
    pair = next(
        (p for p in pairs if p[0].id == from_event_id and p[1].id == to_event_id), None
    )
    if pair is None:
        raise HandoverError("交接项不存在：两条事件当前不是相邻交接关系，请刷新后重试")
    a, b = pair
    key = _pair_key(a, b)
    digest = pair_digest(a, b)
    summary = _pair_payload(a, b)
    match = _time_match(a, b)
    now = _now()

    rec = conn.execute(
        "SELECT * FROM handover_confirmations "
        "WHERE sample_id = ? AND pair_key = ? AND status != 'invalid'",
        (sample_id, key),
    ).fetchone()
    if rec is None:
        out_name, out_at = (name, confirmed_at) if side == "out" else (None, None)
        in_name, in_at = (name, confirmed_at) if side == "in" else (None, None)
        cur = conn.execute(
            "INSERT INTO handover_confirmations "
            "(sample_id, pair_key, from_event_id, to_event_id, event_digest, event_summary, "
            " time_match, out_name, out_at, in_name, in_at, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (sample_id, key, a.id, b.id, digest, summary, int(match),
             out_name, out_at, in_name, in_at, now, now),
        )
        rec_id = cur.lastrowid
    else:
        if side == "out":
            if rec["out_name"]:
                raise HandoverError("交出方已确认，请勿重复提交")
            conn.execute(
                "UPDATE handover_confirmations SET out_name = ?, out_at = ?, updated_at = ? "
                "WHERE id = ?",
                (name, confirmed_at, now, rec["id"]),
            )
        else:
            if rec["in_name"]:
                raise HandoverError("接收方已确认，请勿重复提交")
            conn.execute(
                "UPDATE handover_confirmations SET in_name = ?, in_at = ?, updated_at = ? "
                "WHERE id = ?",
                (name, confirmed_at, now, rec["id"]),
            )
        rec_id = rec["id"]

    # 根据两侧确认情况与时间衔接重算状态
    row = conn.execute(
        "SELECT * FROM handover_confirmations WHERE id = ?", (rec_id,)
    ).fetchone()
    both = bool(row["out_name"]) and bool(row["in_name"])
    status = ("confirmed" if match else "mismatch") if both else "pending"
    conn.execute(
        "UPDATE handover_confirmations SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, rec_id),
    )
    row = conn.execute(
        "SELECT * FROM handover_confirmations WHERE id = ?", (rec_id,)
    ).fetchone()
    return _item_view(a, b, row)
