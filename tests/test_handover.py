"""双向交接确认测试：交接项生成、双侧确认状态、失效保留与重新确认、报告集成。"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="audit-handover-tests-")
os.environ["AUDIT_DB_PATH"] = os.path.join(_tmp, "test_handover.db")

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sample(client):
    code = "UT-HANDOVER-001"
    for s in client.get("/api/samples").json():
        if s["code"] == code:
            client.delete(f"/api/samples/{s['id']}")
    resp = client.post(
        "/api/samples",
        json={"code": code, "name": "交接确认测试样本", "description": "双向确认"},
    )
    assert resp.status_code == 201
    return resp.json()


def _add(client, sid, custodian, start, end=None, note=""):
    resp = client.post(
        f"/api/samples/{sid}/events",
        json={"custodian": custodian, "start_time": start, "end_time": end, "note": note},
    )
    assert resp.status_code == 201
    return resp.json()


def _confirm(client, sid, from_id, to_id, side, name="确认人", at="2026-09-18 12:00:00"):
    return client.post(
        f"/api/samples/{sid}/handovers/confirm",
        json={
            "from_event_id": from_id,
            "to_event_id": to_id,
            "side": side,
            "name": name,
            "confirmed_at": at,
        },
    )


def _confirm_both(client, sid, from_id, to_id):
    assert _confirm(client, sid, from_id, to_id, "out", name="交出方甲").status_code == 200
    return _confirm(client, sid, from_id, to_id, "in", name="接收方乙")


def test_pairs_generated_in_audit_order(client, sample):
    sid = sample["id"]
    # 录入顺序与时间顺序不一致（逆序补录），交接项仍按审计排序（起始时间）生成
    _add(client, sid, "丙", "2026-09-18 11:00:00", "2026-09-18 12:00:00")
    _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")

    resp = client.get(f"/api/samples/{sid}/handovers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == {"total": 2, "confirmed": 0, "mismatch": 0, "pending": 2, "invalid": 0}
    items = data["items"]
    assert [it["from_event"]["custodian"] for it in items] == ["甲", "乙"]
    assert [it["to_event"]["custodian"] for it in items] == ["乙", "丙"]
    assert all(it["status"] == "pending" for it in items)
    assert all(it["confirmation"] is None for it in items)
    assert all(it["time_match"] for it in items)
    assert data["invalid"] == []

    # 样本详情同样携带交接项
    detail = client.get(f"/api/samples/{sid}").json()
    assert detail["handovers"]["summary"]["total"] == 2


def test_two_sided_confirm_flow(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")

    # 单侧确认 -> 待确认
    resp = _confirm(client, sid, e1["id"], e2["id"], "out", name="甲方确认人")
    assert resp.status_code == 200
    item = resp.json()
    assert item["status"] == "pending"
    assert item["confirmation"]["out_name"] == "甲方确认人"
    assert item["confirmation"]["out_at"] == "2026-09-18 12:00:00"
    assert item["confirmation"]["in_name"] is None

    # 重复确认同一侧 -> 409
    resp = _confirm(client, sid, e1["id"], e2["id"], "out", name="换人")
    assert resp.status_code == 409

    # 另一侧确认且时间无缝 -> 已确认
    resp = _confirm(client, sid, e1["id"], e2["id"], "in", name="乙方确认人")
    assert resp.status_code == 200
    item = resp.json()
    assert item["status"] == "confirmed"
    assert item["confirmation"]["in_name"] == "乙方确认人"

    data = client.get(f"/api/samples/{sid}/handovers").json()
    assert data["summary"]["confirmed"] == 1
    assert data["summary"]["pending"] == 0


def test_mismatch_when_times_not_seamless(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:30:00", "2026-09-18 11:00:00")  # 断档 30 分钟

    item = _confirm_both(client, sid, e1["id"], e2["id"]).json()
    assert item["status"] == "mismatch"
    assert item["time_match"] is False
    assert "断档" in item["time_note"]

    data = client.get(f"/api/samples/{sid}/handovers").json()
    assert data["summary"]["mismatch"] == 1
    assert data["summary"]["confirmed"] == 0


def test_open_ended_pair_cannot_be_confirmed(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", None)  # 仍在保管

    item = _confirm_both(client, sid, e1["id"], e2["id"]).json()
    # 上一段结束时间存在且等于下一段开始 -> 可确认
    assert item["status"] == "confirmed"

    # 上一段未归还（开放保管）时，即使双侧确认也是时间不一致
    e3 = _add(client, sid, "丙", "2026-09-18 11:00:00", "2026-09-18 12:00:00")
    item = _confirm_both(client, sid, e2["id"], e3["id"]).json()
    assert item["status"] == "mismatch"
    assert item["time_match"] is False
    assert "尚未归还" in item["time_note"]


def test_edit_event_invalidates_confirmation(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")
    _confirm_both(client, sid, e1["id"], e2["id"])
    assert client.get(f"/api/samples/{sid}/handovers").json()["summary"]["confirmed"] == 1

    # 编辑其中一条事件（仅改备注）-> 原确认失效，交接项回到待确认
    resp = client.put(
        f"/api/events/{e2['id']}",
        json={"custodian": "乙", "start_time": "2026-09-18 10:00:00",
              "end_time": "2026-09-18 11:00:00", "note": "补充说明"},
    )
    assert resp.status_code == 200

    data = client.get(f"/api/samples/{sid}/handovers").json()
    assert data["summary"]["confirmed"] == 0
    assert data["summary"]["pending"] == 1
    assert data["summary"]["invalid"] == 1
    item = data["items"][0]
    assert item["status"] == "pending"
    assert item["confirmation"] is None

    # 失效记录保留确认时的快照与双方信息
    inv = data["invalid"][0]
    assert inv["status"] == "invalid"
    assert inv["out_name"] == "交出方甲" and inv["in_name"] == "接收方乙"
    assert inv["invalid_reason"]
    assert inv["invalidated_at"]
    assert inv["to_event"]["note"] == ""  # 快照是确认时的字段，不含后续编辑

    # 重新确认 -> 新记录生效，失效记录继续保留
    _confirm_both(client, sid, e1["id"], e2["id"])
    data = client.get(f"/api/samples/{sid}/handovers").json()
    assert data["summary"]["confirmed"] == 1
    assert data["summary"]["invalid"] == 1


def test_delete_event_invalidates_confirmation(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")
    e3 = _add(client, sid, "丙", "2026-09-18 11:00:00", "2026-09-18 12:00:00")
    _confirm_both(client, sid, e1["id"], e2["id"])
    _confirm(client, sid, e2["id"], e3["id"], "out", name="乙")

    # 删除中间事件 -> 两条确认记录均失效；新相邻对（甲 -> 丙）生成
    assert client.delete(f"/api/events/{e2['id']}").status_code == 204
    data = client.get(f"/api/samples/{sid}/handovers").json()
    assert data["summary"]["invalid"] == 2
    assert data["summary"]["confirmed"] == 0
    assert data["summary"]["total"] == 1
    item = data["items"][0]
    assert item["from_event"]["custodian"] == "甲"
    assert item["to_event"]["custodian"] == "丙"
    assert item["status"] == "pending"
    assert item["confirmation"] is None


def test_confirm_validation(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")
    e3 = _add(client, sid, "丙", "2026-09-18 11:00:00", "2026-09-18 12:00:00")

    def post(**kw):
        body = {"from_event_id": e1["id"], "to_event_id": e2["id"],
                "side": "out", "name": "甲", "confirmed_at": "2026-09-18 12:00:00"}
        body.update(kw)
        return client.post(f"/api/samples/{sid}/handovers/confirm", json=body)

    assert post(side="middle").status_code == 422          # side 非法
    assert post(name="  ").status_code == 422              # 姓名为空
    assert post(confirmed_at="昨天下午").status_code == 422  # 时间格式错误
    assert post(to_event_id=e3["id"]).status_code == 409   # 非相邻事件对

    # 样本不存在
    resp = client.post(
        "/api/samples/999999/handovers/confirm",
        json={"from_event_id": e1["id"], "to_event_id": e2["id"],
              "side": "out", "name": "甲", "confirmed_at": "2026-09-18 12:00:00"},
    )
    assert resp.status_code == 404
    assert client.get("/api/samples/999999/handovers").status_code == 404


def test_report_includes_handovers(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")
    _confirm_both(client, sid, e1["id"], e2["id"])

    md = client.get(f"/api/samples/{sid}/report?format=markdown")
    assert md.status_code == 200
    assert "双向交接确认" in md.text
    assert "已确认" in md.text

    html_resp = client.get(f"/api/samples/{sid}/report?format=html")
    assert "双向交接确认" in html_resp.text
    assert "已确认" in html_resp.text

    payload = client.get(f"/api/samples/{sid}/report?format=json").json()
    assert "handovers" in payload
    assert payload["handovers"]["summary"]["confirmed"] == 1

    # 事件被编辑后，失效记录同样进入报告
    client.put(
        f"/api/events/{e2['id']}",
        json={"custodian": "乙", "start_time": "2026-09-18 10:00:00",
              "end_time": "2026-09-18 11:00:00", "note": "改动"},
    )
    md = client.get(f"/api/samples/{sid}/report?format=markdown")
    assert "失效确认记录" in md.text
    assert "待确认" in md.text


def test_delete_sample_cascades_handovers(client, sample):
    sid = sample["id"]
    e1 = _add(client, sid, "甲", "2026-09-18 09:00:00", "2026-09-18 10:00:00")
    e2 = _add(client, sid, "乙", "2026-09-18 10:00:00", "2026-09-18 11:00:00")
    _confirm_both(client, sid, e1["id"], e2["id"])
    assert client.delete(f"/api/samples/{sid}").status_code == 204
    assert client.get(f"/api/samples/{sid}/handovers").status_code == 404
