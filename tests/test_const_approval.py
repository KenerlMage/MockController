# -*- coding: utf-8 -*-
"""SR01-AR03 / SR01-AR04：机器常数审批与审批权限测试

全部用 ``tmp_path`` 独立 db，绝不触碰 repo/ 与 controller.db。
覆盖：无权限拒绝（message 含产品与类型）、授组后通过、驳回必填理由、已决单再决被拒、
create 通过后可见 / pending 时不可见、废弃墓碑、dry-run 与非 dry-run 推送日志、
用户组申请重复提交拒绝与状态流转、HTTP 路由端到端。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ate_db  # noqa: E402
import const_approval as ap  # noqa: E402

PRODUCT = "1000-A"
PN = "SPM-RH-01"
KEY = "SPM_RH"
GROUP = "const-eng"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "controller.db"
    ate_db.init_db(path)
    return path


def _payload(version: str = "2026.11.01", **overrides) -> dict:
    data = {
        "version": version,
        "product": PRODUCT,
        "subsystem": "SPM",
        "name": "SPM 读头机常数",
        "fields": [
            {
                "key": "gain_offset",
                "label": "增益偏移",
                "type": "float",
                "unit": "mV",
                "default": 0.0,
                "min": -50.0,
                "max": 50.0,
            },
            {
                "key": "head_grade",
                "label": "读头等级",
                "type": "enum",
                "unit": "",
                "default": "B",
                "options": ["A", "B", "C"],
            },
        ],
    }
    data.update(overrides)
    return data


def _grant(db, kind: str = "create", group: str = GROUP, product: str = PRODUCT) -> dict:
    res = ap.save_approval_role(product, kind, group, True, db_path=db)
    assert res["success"], res
    return res


def _submit(db, kind: str = "create", payload: dict | None = None, applicant: str = "alice") -> int:
    res = ap.create_request(
        kind, PN, KEY, _payload() if payload is None else payload, applicant, db_path=db
    )
    assert res["success"], res
    return res["id"]


def _model_row(db, version: str | None = None):
    with ate_db.connect(db) as conn:
        if version:
            return conn.execute(
                "SELECT * FROM constant_model WHERE pn = ? AND model_key = ? AND version = ?",
                (PN, KEY, version),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM constant_model WHERE pn = ? AND model_key = ? ORDER BY id DESC LIMIT 1",
            (PN, KEY),
        ).fetchone()


# ---------------------------------------------------------------- 审批权限


def test_decide_without_permission_rejected_with_product_and_kind(db):
    rid = _submit(db)
    res = ap.decide_request(rid, True, "bob:other-group", db_path=db)

    assert res["success"] is False
    assert PRODUCT in res["message"]
    assert "新建" in res["message"]
    # 没权限时不得改状态
    assert ap.list_requests("pending", db_path=db)["count"] == 1
    assert ap.list_requests("approved", db_path=db)["count"] == 0


def test_decide_with_permission_approved_and_model_written(db):
    _grant(db)
    rid = _submit(db)

    res = ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)

    assert res["success"] is True
    assert res["request"]["status"] == "approved"
    assert res["request"]["approver"] == "bob"
    assert res["applied"]["version"] == "2026.11.01"
    row = _model_row(db, "2026.11.01")
    assert row is not None
    assert row["field_count"] == 2
    assert row["product"] == PRODUCT
    # 版本头补建且不抢占生效版本
    with ate_db.connect(db) as conn:
        header = conn.execute(
            "SELECT * FROM constant_version WHERE version = ?", ("2026.11.01",)
        ).fetchone()
    assert header is not None and header["is_active"] == 0


def test_decide_accepts_explicit_groups_argument(db):
    _grant(db)
    rid = _submit(db)
    res = ap.decide_request(rid, True, "bob", db_path=db, groups=[GROUP])
    assert res["success"] is True


def test_reject_without_reason_is_refused_then_reject_with_reason(db):
    _grant(db)
    rid = _submit(db)

    missing = ap.decide_request(rid, False, "bob:" + GROUP, "", db_path=db)
    assert missing["success"] is False
    assert "理由" in missing["message"]

    done = ap.decide_request(rid, False, "bob:" + GROUP, "字段定义与整机结构不符", db_path=db)
    assert done["success"] is True
    assert done["request"]["status"] == "rejected"
    assert done["request"]["reason"] == "字段定义与整机结构不符"
    assert _model_row(db) is None


def test_decided_request_cannot_be_decided_again(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    again = ap.decide_request(rid, False, "bob:" + GROUP, "再驳一次", db_path=db)
    assert again["success"] is False
    assert "pending" in again["message"]
    assert ap.list_requests("approved", db_path=db)["count"] == 1
    assert ap.list_requests("rejected", db_path=db)["count"] == 0


def test_withdraw_only_allows_pending(db):
    rid = _submit(db)
    res = ap.withdraw_request(rid, "alice", "需求取消", db_path=db)
    assert res["success"] is True
    assert res["request"]["status"] == "withdrawn"

    again = ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)
    assert again["success"] is False


# ---------------------------------------------------------------- 可见性


def test_approved_create_visible_to_runner(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    visible = ap.visible_models(PN, db_path=db)
    assert visible["success"] is True
    assert [m["model_key"] for m in visible["models"]] == [KEY]
    assert visible["models"][0]["field_count"] == 2
    assert ap.model_visible(PN, KEY, db_path=db) is True


def test_pending_create_invisible_to_runner(db):
    _submit(db)
    assert ap.visible_models(PN, db_path=db)["models"] == []
    assert ap.model_visible(PN, KEY, db_path=db) is False


def test_pending_update_hides_live_model_and_reject_restores(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]
    assert ap.model_visible(PN, KEY, db_path=db) is True

    pending_id = _submit(db, payload=_payload(version="2026.11.02"))
    assert ap.model_visible(PN, KEY, db_path=db) is False

    rejected = ap.decide_request(pending_id, False, "bob:" + GROUP, "版本口径待确认", db_path=db)
    assert rejected["success"] is True
    assert ap.model_visible(PN, KEY, db_path=db) is True


def test_deprecate_approved_hides_model_and_keeps_tombstone(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    # 废弃是另一类审批权：未授权时应明确拒绝
    no_perm = ap.create_request(
        "deprecate", PN, KEY, {"reason": "结构并入新模型"}, "alice", db_path=db
    )
    assert no_perm["success"] is True
    denied = ap.decide_request(no_perm["id"], True, "bob:" + GROUP, db_path=db)
    assert denied["success"] is False
    assert PRODUCT in denied["message"] and "废弃" in denied["message"]

    _grant(db, kind="deprecate")
    done = ap.decide_request(no_perm["id"], True, "bob:" + GROUP, db_path=db)
    assert done["success"] is True
    assert ap.model_visible(PN, KEY, db_path=db) is False

    # 墓碑记录：payload.deprecate=true，模型行仍保留（不物理删除）
    requests = ap.list_requests("approved", db_path=db)["requests"]
    dep = next(r for r in requests if r["id"] == no_perm["id"])
    assert dep["payload"]["deprecate"] is True
    assert _model_row(db) is not None


def test_recreate_after_deprecate_makes_model_visible_again(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    _grant(db, kind="deprecate")
    dep = ap.create_request("deprecate", PN, KEY, {}, "alice", db_path=db)
    assert dep["success"] is True
    assert ap.decide_request(dep["id"], True, "bob:" + GROUP, db_path=db)["success"]
    assert ap.model_visible(PN, KEY, db_path=db) is False

    revive = _submit(db, payload=_payload(version="2026.11.03"))
    assert ap.decide_request(revive, True, "bob:" + GROUP, db_path=db)["success"]
    assert ap.model_visible(PN, KEY, db_path=db) is True


# ---------------------------------------------------------------- 推送日志


def test_push_result_dry_run_writes_push_log(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    res = ap.push_result(rid, db_path=db)
    assert res["success"] is True
    assert res["logged"] is True
    assert res["status"] == "dry-run"

    with ate_db.connect(db) as conn:
        log = conn.execute(
            "SELECT * FROM const_push_log WHERE request_id = ?", (rid,)
        ).fetchone()
    assert log is not None
    assert log["status"] == "dry-run"
    assert log["channel"] == "dry-run"
    assert log["target"] == "alice"
    summary = json.loads(log["payload"])
    assert summary["request_id"] == rid
    assert summary["status"] == "approved"
    assert summary["pn"] == PN


def test_push_result_non_dry_run_channel_is_skipped(db):
    _grant(db)
    rid = _submit(db)
    assert ap.decide_request(rid, True, "bob:" + GROUP, db_path=db)["success"]

    res = ap.push_result(rid, channel="webhook", db_path=db)
    assert res["success"] is True
    assert res["logged"] is True
    assert res["status"] == "skipped"
    assert "未配置" in res["message"]

    with ate_db.connect(db) as conn:
        log = conn.execute(
            "SELECT * FROM const_push_log WHERE request_id = ?", (rid,)
        ).fetchone()
    assert log["status"] == "skipped"
    assert log["channel"] == "webhook"


def test_push_pending_request_is_refused(db):
    rid = _submit(db)
    res = ap.push_result(rid, db_path=db)
    assert res["success"] is False
    assert "pending" in res["message"]


# ---------------------------------------------------------------- 用户组申请


def test_duplicate_pending_group_apply_rejected(db):
    first = ap.apply_group("alice", GROUP, "需要新建审批权", db_path=db)
    assert first["success"] is True

    second = ap.apply_group("alice", GROUP, "再申请一次", db_path=db)
    assert second["success"] is False
    assert "待审批" in second["message"]
    assert ap.list_group_applies("pending", db_path=db)["count"] == 1


def test_group_apply_decision_flow_does_not_write_role(db):
    apply_id = ap.apply_group("alice", GROUP, "需要新建审批权", db_path=db)["id"]

    done = ap.decide_group_apply(apply_id, True, "admin", db_path=db)
    assert done["success"] is True
    assert done["apply"]["status"] == "approved"
    assert done["apply"]["decided_by"] == "admin"

    # 组申请只记状态，不倒写审批权限
    assert ap.list_approval_roles(db_path=db)["count"] == 0

    again = ap.decide_group_apply(apply_id, True, "admin", db_path=db)
    assert again["success"] is False

    reject_missing = ap.apply_group("bob", "qa-group", db_path=db)["id"]
    missing = ap.decide_group_apply(reject_missing, False, "admin", "", db_path=db)
    assert missing["success"] is False and "理由" in missing["message"]


# ---------------------------------------------------------------- 参数与结构校验


def test_create_rejects_invalid_structure(db):
    bad = _payload()
    bad["fields"] = [{"key": "bad key", "type": "float"}]
    res = ap.create_request("create", PN, KEY, bad, "alice", db_path=db)
    assert res["success"] is False
    assert "结构" in res["message"]


def test_deprecate_requires_existing_model(db):
    res = ap.create_request("deprecate", PN, KEY, {}, "alice", db_path=db)
    assert res["success"] is False
    assert "不存在" in res["message"]


# ---------------------------------------------------------------- HTTP 路由


def _client(db, base_dir) -> TestClient:
    app = FastAPI()
    ap.register_routes(app, base_dir, db)
    return TestClient(app)


def test_routes_end_to_end(db, tmp_path):
    client = _client(db, tmp_path)

    res = client.put(
        "/api/v1/constants/approval-roles",
        json={"product": PRODUCT, "kind": "create", "group_id": GROUP, "enabled": True},
    ).json()
    assert res["success"] is True

    roles = client.get(
        "/api/v1/constants/approval-roles", params={"product": PRODUCT}
    ).json()
    assert roles["success"] is True and roles["count"] == 1

    created = client.post(
        "/api/v1/constants/requests",
        json={
            "kind": "create",
            "pn": PN,
            "model_key": KEY,
            "payload": _payload(),
            "applicant": "alice",
        },
    ).json()
    assert created["success"] is True
    rid = created["id"]

    listed = client.get("/api/v1/constants/requests", params={"status": "pending"}).json()
    assert listed["count"] == 1

    decided = client.post(
        f"/api/v1/constants/requests/{rid}/decide",
        json={"approve": True, "approver": "bob:" + GROUP, "reason": ""},
    ).json()
    assert decided["success"] is True

    pushed = client.post(
        f"/api/v1/constants/requests/{rid}/push", json={"channel": "dry-run"}
    ).json()
    assert pushed["success"] is True and pushed["logged"] is True

    applied = client.post(
        "/api/v1/constants/groups/apply",
        json={"username": "carol", "group_id": GROUP, "reason": "加入组"},
    ).json()
    assert applied["success"] is True
    apply_id = applied["id"]

    applies = client.get(
        "/api/v1/constants/groups/applies", params={"status": "pending"}
    ).json()
    assert applies["count"] == 1

    decided_apply = client.post(
        f"/api/v1/constants/groups/applies/{apply_id}/decide",
        json={"approve": True, "decided_by": "admin", "reason": ""},
    ).json()
    assert decided_apply["success"] is True


def test_routes_wrap_errors_as_chinese_messages(db, tmp_path):
    client = _client(db, tmp_path)

    bad = client.post(
        "/api/v1/constants/requests",
        json={
            "kind": "unknown",
            "pn": PN,
            "model_key": KEY,
            "payload": {},
            "applicant": "alice",
        },
    ).json()
    assert bad["success"] is False and bad["message"]

    bad_status = client.get(
        "/api/v1/constants/requests", params={"status": "bogus"}
    ).json()
    assert bad_status["success"] is False

    missing = client.post(
        "/api/v1/constants/requests/9999/decide",
        json={"approve": True, "approver": "bob"},
    ).json()
    assert missing["success"] is False and "不存在" in missing["message"]

    bad_role = client.put(
        "/api/v1/constants/approval-roles",
        json={"product": "", "kind": "create", "group_id": GROUP},
    ).json()
    assert bad_role["success"] is False and bad_role["message"]
