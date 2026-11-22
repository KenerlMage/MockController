# -*- coding: utf-8 -*-
"""MockController 机器常数审批（SR01-AR03 新建/废弃审批、SR01-AR04 审批权限与用户组）

业务约定
--------
* 审批单落 ``const_request``，状态机 ``pending -> approved / rejected / withdrawn``；
  已决单不可再决，驳回必须填理由。
* 审批人资格来自 ``const_approval_role`` 的 ``(product, kind, group_id, enabled=1)`` 映射。
  ``approver`` 允许 ``"alice:groupA,groupB"`` 形式，也可用关键字参数 ``groups=[...]`` 显式给出；
  未给 ``groups`` 时把 ``approver`` 本身当作组名匹配（接口简化的约定，见契约第五节）。
* ``kind=create`` 通过且 payload 含 ``fields`` 时写/更新 ``constant_model``：版本取
  ``payload.version``，缺省 ``pending-<id>``；同时补一条 ``constant_version`` 版本头
  （``is_active=0``，不抢占当前生效版本）。
* **废弃的落地方式**：``constant_model`` 没有 status 列，且本任务不允许改 ``ate_db.SCHEMA_SQL``；
  因此废弃不物理删除模型行，而是把本条 ``const_request``（``status=approved`` 且
  ``payload.deprecate=true``）当作"墓碑"记录，``visible_models`` / 模型列表按该记录排除
  对应的 ``(pn, model_key)``。结构与历史因此完整保留，Runner 侧也看不到已废弃模型。
* 待审批期间对 Runner 不可见：``visible_models`` 排除任何存在 pending 申请（create 或
  deprecate）的 ``(pn, model_key)``；catalog 侧应调用该函数，而不是直接读 ``constant_model``。
* 推送本轮 dry-run：只写 ``const_push_log``，不真实外发；非 dry-run 通道记 ``status=skipped``。
* 用户组申请只维护 ``const_group_apply`` 状态，**不**倒写 ``const_approval_role``；
  审批权限仍由管理员在 ``approval-roles`` 显式配置。

对外函数（契约第五节）
----------------------
``create_request`` / ``decide_request`` / ``list_requests`` / ``push_result`` /
``visible_models`` / ``register_routes``，另有 ``withdraw_request``、审批角色与用户组申请
的读写函数。
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import ate_db
import constants as constants_mod

_LOCK = threading.RLock()

# 运行期由 register_routes 注入
_BASE_DIR: Optional[Path] = None
_DB_PATH: Optional[Path] = None

KINDS = ("create", "deprecate")
KIND_LABELS = {"create": "新建", "deprecate": "废弃"}

REQUEST_STATUSES = ("pending", "approved", "rejected", "withdrawn")
REQUEST_STATUS_LABELS = {
    "pending": "待审批",
    "approved": "已通过",
    "rejected": "已驳回",
    "withdrawn": "已撤回",
}

APPLY_STATUSES = ("pending", "approved", "rejected")
APPLY_STATUS_LABELS = {"pending": "待审批", "approved": "已通过", "rejected": "已驳回"}


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    """统一时间口径（契约一.4）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _bbox(pn: Any) -> str:
    """PN 统一大写，与 constants.parse_bundle 的入库口径一致。"""
    return _text(pn, 64).upper()


def _json_loads(text: Any, default: Any) -> Any:
    if not text:
        return default
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return default
    return value


def _fail(message: str, **extra: Any) -> dict:
    payload: dict[str, Any] = {"success": False, "message": message}
    payload.update(extra)
    return payload


def _as_bool(value: Any) -> Optional[bool]:
    """把 JSON / 表单里的布尔值归一化；无法判定时返回 None（不静默当 False）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in ("true", "1", "yes", "y"):
            return True
        if raw in ("false", "0", "no", "n"):
            return False
    return None


def _db_path(db_path: Any) -> Path:
    if db_path:
        return Path(db_path)
    return Path(_DB_PATH) if _DB_PATH else Path(ate_db.DB_PATH)


@contextmanager
def _db(db_path: Any = None):
    """打开库并保证建表；正常退出提交，异常回滚，最后关闭连接。"""
    path = _db_path(db_path)
    ate_db.init_db(path)
    conn = ate_db.connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- 行 -> 字典


def _request_dict(row: dict) -> dict:
    kind = row.get("kind") or ""
    status = row.get("status") or "pending"
    payload = _json_loads(row.get("payload_json"), {})
    diff = _json_loads(row.get("diff_json"), {})
    return {
        "id": row.get("id"),
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "pn": row.get("pn") or "",
        "model_key": row.get("model_key") or "",
        "product": row.get("product") or "",
        "subsystem": row.get("subsystem") or "",
        "payload": payload if isinstance(payload, dict) else {},
        "diff": diff if isinstance(diff, dict) else {},
        "status": status,
        "status_label": REQUEST_STATUS_LABELS.get(status, status),
        "applicant": row.get("applicant") or "",
        "approver": row.get("approver") or "",
        "reason": row.get("reason") or "",
        "created_at": row.get("created_at") or "",
        "decided_at": row.get("decided_at") or "",
    }


def _apply_dict(row: dict) -> dict:
    status = row.get("status") or "pending"
    return {
        "id": row.get("id"),
        "username": row.get("username") or "",
        "group_id": row.get("group_id") or "",
        "status": status,
        "status_label": APPLY_STATUS_LABELS.get(status, status),
        "reason": row.get("reason") or "",
        "decided_by": row.get("decided_by") or "",
        "created_at": row.get("created_at") or "",
        "decided_at": row.get("decided_at") or "",
    }


def _role_dict(row: dict) -> dict:
    kind = row.get("kind") or ""
    return {
        "id": row.get("id"),
        "product": row.get("product") or "",
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "group_id": row.get("group_id") or "",
        "enabled": bool(row.get("enabled")),
        "updated_at": row.get("updated_at") or "",
    }


def _model_dict(row: dict) -> dict:
    data = _json_loads(row.get("fields_json"), [])
    fields = data if isinstance(data, list) else []
    return {
        "id": row.get("id"),
        "pn": row.get("pn") or "",
        "model_key": row.get("model_key") or "",
        "name": row.get("name") or "",
        "product": row.get("product") or "",
        "subsystem": row.get("subsystem") or "",
        "version": row.get("version") or "",
        "category": row.get("category") or "",
        "description": row.get("description") or "",
        "unit": row.get("unit") or "",
        "field_count": row.get("field_count") if row.get("field_count") is not None else len(fields),
        "fields": fields,
        "updated_at": row.get("updated_at") or "",
    }


def _fields_of(row: Optional[dict]) -> list[dict]:
    if not row:
        return []
    data = _json_loads(row.get("fields_json"), [])
    if not isinstance(data, list):
        return []
    return [f for f in data if isinstance(f, dict)]


# ---------------------------------------------------------------- 结构 / 差异


def _validate_structure(kind: str, pn: str, model_key: str, payload: dict) -> Optional[dict]:
    """校验 create 申请的 fields 结构；未携带 fields 时返回 None。

    复用 constants._norm_model（合同允许 import constants 复用校验，不改该文件），
    保证审批通过写入的结构与导入路径同口径。
    """
    if kind != "create":
        return None
    fields = payload.get("fields")
    if fields is None:
        return None
    version = _text(payload.get("version"), 48)
    if version and not constants_mod.VERSION_RE.match(version):
        raise ValueError(f"版本号不合法（字母/数字/._+-，最长 48）：{version}")
    raw = {
        "model_key": model_key,
        "name": payload.get("name") or model_key,
        "category": payload.get("category") or "",
        "description": payload.get("description") or payload.get("desc") or "",
        "unit": payload.get("unit") or "",
        "fields": fields,
        "values": payload.get("values") or {},
    }
    return constants_mod._norm_model(raw, pn=pn, index=0)


def _diff_of(kind: str, before_fields: list[dict], after_fields: list[dict]) -> dict:
    before = {str(f.get("key")): f for f in before_fields if f.get("key")}
    after = {str(f.get("key")): f for f in after_fields if f.get("key")}
    return {
        "kind": kind,
        "before_field_count": len(before),
        "after_field_count": len(after),
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
        "deprecated": kind == "deprecate",
    }


def _resolve_scope(payload: dict, model_row: Optional[dict]) -> tuple[str, str]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    product = _text(payload.get("product") or meta.get("product"), 64)
    subsystem = _text(payload.get("subsystem") or meta.get("subsystem"), 64)
    if model_row:
        product = product or _text(model_row.get("product"), 64)
        subsystem = subsystem or _text(model_row.get("subsystem"), 64)
    return product, subsystem


def _latest_model_row(conn, pn: str, model_key: str) -> Optional[dict]:
    """取该模型最近一行（优先当前生效版本），用于 diff 与产品领域回填。"""
    row = conn.execute(
        """
        SELECT m.*, COALESCE(v.is_active, 0) AS is_active
        FROM constant_model m
        LEFT JOIN constant_version v ON v.version = m.version
        WHERE m.pn = ? AND m.model_key = ?
        ORDER BY is_active DESC, m.id DESC
        LIMIT 1
        """,
        (pn, model_key),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- 审批人资格


def _split_approver(approver: Any, groups: Any = None) -> tuple[str, list[str]]:
    """解析审批人身份：``alice:g1,g2`` 或 ``approver`` + ``groups``。

    未给 groups 且无冒号时，按契约把 approver 本身当作组名匹配。
    """
    raw = _text(approver, 200)
    name, sep, inline = raw.partition(":")
    if groups is not None:
        if not isinstance(groups, (list, tuple, set)):
            groups = [groups]
        glist = [_text(g, 64) for g in groups if _text(g, 64)]
        return _text(name if sep else raw, 64), glist
    if sep:
        return _text(name, 64), [g.strip() for g in inline.split(",") if g.strip()]
    return raw, [raw] if raw else []


def _granted_groups(conn, product: str, kind: str) -> list[str]:
    rows = conn.execute(
        "SELECT group_id FROM const_approval_role "
        "WHERE product = ? AND kind = ? AND enabled = 1 ORDER BY group_id",
        (product, kind),
    ).fetchall()
    return [row["group_id"] for row in rows]


# ---------------------------------------------------------------- 审批单：提交


def create_request(
    kind: str,
    pn: str,
    model_key: str,
    payload: dict,
    applicant: str,
    db_path=None,
) -> dict:
    """提交新建（create）或废弃（deprecate）审批单，返回 ``{success, id, request}``。"""
    kind = _text(kind, 16).lower()
    pn = _bbox(pn)
    model_key = _text(model_key, 64)
    applicant = _text(applicant, 64)

    if kind not in KINDS:
        return _fail("申请类型 kind 只能是 create（新建）或 deprecate（废弃）")
    if not pn:
        return _fail("PN 不能为空")
    if not constants_mod.PN_RE.match(pn):
        return _fail(f"PN 不合法（字母/数字/._-，最长 64）：{pn}")
    if not model_key:
        return _fail("模型键 model_key 不能为空")
    if not constants_mod.KEY_RE.match(model_key):
        return _fail(f"模型键不合法（字母/数字/_.-，最长 64）：{model_key}")
    if not applicant:
        return _fail("申请人 applicant 不能为空")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _fail("payload 必须是 JSON 对象")
    payload = dict(payload)
    # 统一键口径：申请单与模型库必须能对上
    payload.setdefault("pn", pn)
    payload.setdefault("model_key", model_key)

    try:
        model = _validate_structure(kind, pn, model_key, payload)
    except ValueError as exc:
        return _fail(f"{KIND_LABELS[kind]}申请结构校验失败：{exc}")

    with _LOCK, _db(db_path) as conn:
        latest = _latest_model_row(conn, pn, model_key)
        product, subsystem = _resolve_scope(payload, latest)

        pending = conn.execute(
            "SELECT id, kind FROM const_request WHERE pn = ? AND model_key = ? "
            "AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (pn, model_key),
        ).fetchone()
        if pending:
            return _fail(
                f"模型 {pn}/{model_key} 已有待审批申请 #{pending['id']}"
                f"（{KIND_LABELS.get(pending['kind'], pending['kind'])}），请先处理后再提交",
                request_id=pending["id"],
            )

        if kind == "deprecate":
            if not latest:
                return _fail(f"废弃申请指向的模型不存在：PN={pn}，模型键={model_key}")
            deprecated = conn.execute(
                "SELECT id FROM const_request WHERE pn = ? AND model_key = ? "
                "AND kind = 'deprecate' AND status = 'approved' ORDER BY id DESC LIMIT 1",
                (pn, model_key),
            ).fetchone()
            if deprecated:
                return _fail(
                    f"模型 {pn}/{model_key} 已由审批单 #{deprecated['id']} 废弃，无需重复申请"
                )

        before_fields = _fields_of(latest)
        after_fields = model["fields"] if model else []
        diff = _diff_of(kind, before_fields, after_fields)
        now = _now()
        cur = conn.execute(
            "INSERT INTO const_request "
            "(kind, pn, model_key, product, subsystem, payload_json, diff_json, status, "
            " applicant, approver, reason, created_at, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, '', '', ?, '')",
            (
                kind,
                pn,
                model_key,
                product,
                subsystem,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                json.dumps(diff, ensure_ascii=False, sort_keys=True),
                applicant,
                now,
            ),
        )
        request_id = cur.lastrowid
        row = conn.execute("SELECT * FROM const_request WHERE id = ?", (request_id,)).fetchone()

    return {
        "success": True,
        "id": request_id,
        "request": _request_dict(dict(row)),
        "message": (
            f"已提交{KIND_LABELS[kind]}申请 #{request_id}（{pn}/{model_key}），等待审批"
        ),
    }


# ---------------------------------------------------------------- 审批单：决策


def _write_model(conn, req: dict, artifact: dict, approver: str) -> dict:
    """把通过审批的结构写入 constant_model，并维护版本头计数。"""
    model = artifact["model"]
    version = artifact["version"]
    pn = req["pn"]
    model_key = req["model_key"]
    now = _now()
    fields_json = json.dumps(model["fields"], ensure_ascii=False)

    conn.execute(
        "DELETE FROM constant_model WHERE version = ? AND pn = ? AND model_key = ?",
        (version, pn, model_key),
    )
    conn.execute(
        "INSERT INTO constant_model "
        "(version, pn, product, subsystem, model_key, name, category, description, unit, "
        " fields_json, field_count, struct_checksum, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            version,
            pn,
            req.get("product") or "",
            req.get("subsystem") or "",
            model_key,
            model["name"],
            model["category"],
            model["description"],
            model["unit"],
            fields_json,
            len(model["fields"]),
            constants_mod._checksum(model["fields"]),
            now,
        ),
    )

    header = conn.execute(
        "SELECT version FROM constant_version WHERE version = ?", (version,)
    ).fetchone()
    if not header:
        # 审批产生的新版本不抢占当前生效版本，is_active 固定 0
        conn.execute(
            "INSERT INTO constant_version "
            "(version, label, source, note, model_count, field_count, imported_at, "
            " imported_by, is_active, checksum) VALUES (?, ?, 'approval', ?, 0, 0, ?, ?, 0, ?)",
            (
                version,
                f"审批单 #{req['id']}",
                f"{KIND_LABELS.get(req['kind'], req['kind'])}审批通过",
                now,
                approver,
                constants_mod._checksum({"pn": pn, "model_key": model_key}),
            ),
        )
    counts = conn.execute(
        "SELECT COUNT(*) AS model_count, COALESCE(SUM(field_count), 0) AS field_count "
        "FROM constant_model WHERE version = ?",
        (version,),
    ).fetchone()
    conn.execute(
        "UPDATE constant_version SET model_count = ?, field_count = ? WHERE version = ?",
        (counts["model_count"], counts["field_count"], version),
    )
    return {
        "applied": True,
        "version": version,
        "model_key": model_key,
        "field_count": len(model["fields"]),
    }


def decide_request(
    request_id: int,
    approve: bool,
    approver: str,
    reason: str = "",
    db_path=None,
    groups: Optional[list] = None,
) -> dict:
    """审批通过/驳回。

    * 仅 ``pending`` 可决策，已决单再次决策返回 ``success=false``；
    * 驳回必须填 ``reason``；
    * 审批人须属于该 ``product`` + ``kind`` 的授权用户组，否则拒绝并指明缺什么权限；
    * ``approver`` 可写成 ``alice:groupA,groupB``，也可用 ``groups`` 显式给出组列表。
    """
    decision = _as_bool(approve)
    if decision is None:
        return _fail("approve 必须是布尔值（true 表示通过，false 表示驳回）")
    reason = _text(reason, 400)
    if not decision and not reason:
        return _fail("驳回必须填写理由（reason）")

    name, approver_groups = _split_approver(approver, groups)
    if not name:
        return _fail("审批人 approver 不能为空")

    with _LOCK, _db(db_path) as conn:
        row = conn.execute("SELECT * FROM const_request WHERE id = ?", (request_id,)).fetchone()
        if not row:
            return _fail(f"审批单 #{request_id} 不存在")
        req = _request_dict(dict(row))

        if req["status"] != "pending":
            return _fail(
                f"审批单 #{req['id']} 当前状态为「{req['status_label']}」，"
                "只有待审批（pending）的申请可以决策",
                status=req["status"],
            )

        product = req["product"]
        if not product:
            latest = _latest_model_row(conn, req["pn"], req["model_key"])
            product, subsystem = _resolve_scope(req["payload"], latest)
            req["product"] = product
            req["subsystem"] = req["subsystem"] or subsystem
        if not product:
            return _fail(
                f"审批被拒：审批单 #{req['id']} 缺少产品领域（product），"
                f"无法校验「{req['kind_label']}」审批权限"
            )

        granted = _granted_groups(conn, product, req["kind"])
        if not set(approver_groups) & set(granted):
            return _fail(
                f"审批被拒：{name} 不具备产品 {product} 的「{req['kind_label']}」审批权限"
                f"（该产品/类型的授权用户组：{'、'.join(granted) or '无'}；"
                f"{name} 当前组：{'、'.join(approver_groups) or '无'}）",
                product=product,
                kind=req["kind"],
                required_groups=granted,
                approver_groups=approver_groups,
            )

        # 结构校验放在落库/改状态之前：校验失败时审批单保持 pending
        artifact: Optional[dict] = None
        if decision and req["kind"] == "create":
            try:
                model = _validate_structure(
                    "create", req["pn"], req["model_key"], req["payload"]
                )
            except ValueError as exc:
                return _fail(
                    f"审批未生效：新建申请结构校验失败（{exc}）", request_id=req["id"]
                )
            if model is not None:
                version = _text(req["payload"].get("version"), 48) or f"pending-{req['id']}"
                if not constants_mod.VERSION_RE.match(version):
                    return _fail(
                        f"审批未生效：版本号不合法（字母/数字/._+-，最长 48）：{version}",
                        request_id=req["id"],
                    )
                artifact = {"model": model, "version": version}

        now = _now()
        payload = dict(req["payload"])
        if decision and req["kind"] == "deprecate":
            # constant_model 没有 status 列：用本审批单作为"墓碑"记录，见模块头部说明
            payload["deprecate"] = True
            payload["deprecated_at"] = now

        conn.execute(
            "UPDATE const_request SET status = ?, approver = ?, reason = ?, decided_at = ?, "
            " product = ?, subsystem = ?, payload_json = ? WHERE id = ?",
            (
                "approved" if decision else "rejected",
                name,
                reason,
                now,
                req["product"],
                req["subsystem"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                req["id"],
            ),
        )

        applied: Optional[dict] = None
        if decision and req["kind"] == "create":
            if artifact:
                applied = _write_model(conn, req, artifact, name)
            else:
                applied = {
                    "applied": False,
                    "reason": "payload 未携带 fields 结构，模型库未写入",
                }
        elif decision and req["kind"] == "deprecate":
            applied = {
                "applied": True,
                "deprecated": True,
                "tombstone": f"const_request#{req['id']}",
            }

        updated = conn.execute(
            "SELECT * FROM const_request WHERE id = ?", (req["id"],)
        ).fetchone()

    result = {
        "success": True,
        "request": _request_dict(dict(updated)),
    }
    if applied is not None:
        result["applied"] = applied

    if decision and req["kind"] == "create" and artifact:
        result["message"] = (
            f"已通过审批单 #{req['id']}：模型 {req['pn']}/{req['model_key']} "
            f"已写入版本 {artifact['version']}（{applied['field_count']} 个字段），Runner 可见"
        )
    elif decision and req["kind"] == "create":
        result["message"] = (
            f"已通过审批单 #{req['id']}；payload 未携带 fields 结构，模型库未变更"
        )
    elif decision and req["kind"] == "deprecate":
        result["message"] = (
            f"已通过审批单 #{req['id']}：模型 {req['pn']}/{req['model_key']} 标记为已废弃，"
            "Runner 不可见"
        )
    else:
        result["message"] = f"已驳回审批单 #{req['id']}：{reason}"
    return result


def withdraw_request(
    request_id: int, applicant: str = "", reason: str = "", db_path=None
) -> dict:
    """申请人撤回待审批申请；只有 pending 可撤回。"""
    applicant = _text(applicant, 64)
    with _LOCK, _db(db_path) as conn:
        row = conn.execute("SELECT * FROM const_request WHERE id = ?", (request_id,)).fetchone()
        if not row:
            return _fail(f"审批单 #{request_id} 不存在")
        req = _request_dict(dict(row))
        if req["status"] != "pending":
            return _fail(
                f"审批单 #{req['id']} 当前状态为「{req['status_label']}」，"
                "只有待审批（pending）的申请可以撤回",
                status=req["status"],
            )
        if applicant and req["applicant"] and applicant != req["applicant"]:
            return _fail(f"只有申请人 {req['applicant']} 本人可以撤回审批单 #{req['id']}")
        conn.execute(
            "UPDATE const_request SET status = 'withdrawn', reason = ?, decided_at = ? WHERE id = ?",
            (_text(reason, 400), _now(), req["id"]),
        )
        updated = conn.execute(
            "SELECT * FROM const_request WHERE id = ?", (req["id"],)
        ).fetchone()
    return {
        "success": True,
        "request": _request_dict(dict(updated)),
        "message": f"已撤回审批单 #{req['id']}",
    }


# ---------------------------------------------------------------- 审批单：查询 / 推送


def list_requests(status: str = "", db_path=None) -> dict:
    """按状态列审批单（空表示全部），附各状态计数。"""
    status = _text(status, 16)
    if status and status not in REQUEST_STATUSES:
        return _fail(f"status 只能是 {'/'.join(REQUEST_STATUSES)} 或留空")
    with _db(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM const_request WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM const_request ORDER BY id DESC").fetchall()
        stats = {key: 0 for key in REQUEST_STATUSES}
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM const_request GROUP BY status"
        ):
            stats[row["status"]] = row["count"]
    items = [_request_dict(dict(row)) for row in rows]
    return {
        "success": True,
        "count": len(items),
        "requests": items,
        "stats": stats,
        "message": None,
    }


def push_result(request_id: int, channel: str = "dry-run", db_path=None) -> dict:
    """记录审批结果推送。

    本轮只写 ``const_push_log``：``dry-run`` 记 status=dry-run；其他通道记 status=skipped
    并说明未配置真实通道，不真实外发。
    """
    channel = _text(channel, 32) or "dry-run"
    with _LOCK, _db(db_path) as conn:
        row = conn.execute("SELECT * FROM const_request WHERE id = ?", (request_id,)).fetchone()
        if not row:
            return _fail(f"审批单 #{request_id} 不存在")
        req = _request_dict(dict(row))
        if req["status"] == "pending":
            return _fail(
                f"审批单 #{req['id']} 仍为待审批（pending），尚无审批结果可推送",
                status=req["status"],
            )

        status = "dry-run" if channel == "dry-run" else "skipped"
        error = "" if status == "dry-run" else f"通道 {channel} 未配置真实外发"
        summary = {
            "request_id": req["id"],
            "kind": req["kind"],
            "kind_label": req["kind_label"],
            "pn": req["pn"],
            "model_key": req["model_key"],
            "status": req["status"],
            "status_label": req["status_label"],
            "applicant": req["applicant"],
            "approver": req["approver"],
            "reason": req["reason"],
            "decided_at": req["decided_at"],
        }
        cur = conn.execute(
            "INSERT INTO const_push_log "
            "(request_id, channel, target, payload, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                req["id"],
                channel,
                req["applicant"],
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                status,
                error,
                _now(),
            ),
        )
        log_id = cur.lastrowid

    if status == "dry-run":
        message = f"dry-run：审批结果已记录到推送日志 #{log_id}，未真实外发"
    else:
        message = (
            f"通道 {channel} 未配置真实外发通道，仅记录推送日志 #{log_id}（status=skipped）"
        )
    return {
        "success": True,
        "logged": True,
        "status": status,
        "channel": channel,
        "log_id": log_id,
        "request_id": req["id"],
        "message": message,
    }


# ---------------------------------------------------------------- Runner 可见性


def _hidden_state(conn) -> set[tuple[str, str]]:
    """对 Runner 隐藏的模型键：有待审批申请，或最后一次已决申请是废弃。

    废弃用 ``const_request``（approved + payload.deprecate=true）做墓碑，
    因为 ``constant_model`` 没有 status 列（表结构由 ate_db 固定，本模块不改）。
    用审批单编号而非时间戳判定先后，避免同一秒内决策导致排序不确定；
    同一模型键废弃后又审批通过新建时，新模型重新可见。
    """
    hidden: set[tuple[str, str]] = set()
    last_deprecate: dict[tuple[str, str], int] = {}
    last_create: dict[tuple[str, str], int] = {}
    rows = conn.execute(
        "SELECT id, pn, model_key, kind, status FROM const_request "
        "WHERE status IN ('pending', 'approved')"
    ).fetchall()
    for row in rows:
        key = (row["pn"], row["model_key"])
        if row["status"] == "pending":
            hidden.add(key)
        elif row["kind"] == "deprecate":
            last_deprecate[key] = max(last_deprecate.get(key, 0), row["id"])
        elif row["kind"] == "create":
            last_create[key] = max(last_create.get(key, 0), row["id"])
    for key, dep_id in last_deprecate.items():
        if last_create.get(key, 0) < dep_id:
            hidden.add(key)
    return hidden


def visible_models(pn: str = "", db_path=None) -> dict:
    """Runner 可见模型：排除存在 pending 申请或已批准废弃的 ``(pn, model_key)``。

    同一模型在多版本出现时只返回一行，优先当前生效版本。
    """
    pn = _bbox(pn)
    with _db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.*, COALESCE(v.is_active, 0) AS is_active
            FROM constant_model m
            LEFT JOIN constant_version v ON v.version = m.version
            WHERE (? = '' OR m.pn = ?)
            ORDER BY m.pn, m.model_key, is_active DESC, m.id DESC
            """,
            (pn, pn),
        ).fetchall()
        hidden = _hidden_state(conn)

    models: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["pn"], row["model_key"])
        if key in hidden or key in seen:
            continue
        seen.add(key)
        models.append(_model_dict(dict(row)))
    return {
        "success": True,
        "pn": pn,
        "count": len(models),
        "models": models,
        "message": None,
    }


def model_visible(pn: str, model_key: str, db_path=None) -> bool:
    """单模型可见性判断（catalog / TPS 关联校验可直接调用）。"""
    data = visible_models(pn, db_path)
    key = _text(model_key, 64)
    return any(item["model_key"] == key for item in data["models"])


# ---------------------------------------------------------------- 审批权限（角色）


def list_approval_roles(product: str = "", db_path=None) -> dict:
    """列审批权限映射（可按产品过滤）。"""
    product = _text(product, 64)
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM const_approval_role WHERE (? = '' OR product = ?) "
            "ORDER BY product, kind, group_id",
            (product, product),
        ).fetchall()
    roles = [_role_dict(dict(row)) for row in rows]
    return {"success": True, "count": len(roles), "roles": roles, "message": None}


def save_approval_role(
    product: str, kind: str, group_id: str, enabled: bool = True, db_path=None
) -> dict:
    """新增/更新一条审批权限映射（product + kind + group_id 唯一）。"""
    product = _text(product, 64)
    kind = _text(kind, 16).lower()
    group_id = _text(group_id, 64)
    if not product:
        return _fail("产品领域 product 不能为空")
    if kind not in KINDS:
        return _fail("审批类型 kind 只能是 create（新建）或 deprecate（废弃）")
    if not group_id:
        return _fail("用户组 group_id 不能为空")
    flag = _as_bool(enabled)
    if flag is None:
        return _fail("enabled 必须是布尔值")

    now = _now()
    with _LOCK, _db(db_path) as conn:
        conn.execute(
            "INSERT INTO const_approval_role (product, kind, group_id, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(product, kind, group_id) DO UPDATE SET "
            "enabled = excluded.enabled, updated_at = excluded.updated_at",
            (product, kind, group_id, 1 if flag else 0, now),
        )
        row = conn.execute(
            "SELECT * FROM const_approval_role "
            "WHERE product = ? AND kind = ? AND group_id = ?",
            (product, kind, group_id),
        ).fetchone()

    return {
        "success": True,
        "role": _role_dict(dict(row)),
        "message": (
            f"已{'启用' if flag else '停用'} {product} 的「{KIND_LABELS[kind]}」审批权限：{group_id}"
        ),
    }


# ---------------------------------------------------------------- 用户组申请


def apply_group(username: str, group_id: str, reason: str = "", db_path=None) -> dict:
    """提交加入用户组申请；同一用户对同一组已有 pending 申请时拒绝。"""
    username = _text(username, 64)
    group_id = _text(group_id, 64)
    reason = _text(reason, 400)
    if not username:
        return _fail("用户名 username 不能为空")
    if not group_id:
        return _fail("用户组 group_id 不能为空")

    with _LOCK, _db(db_path) as conn:
        pending = conn.execute(
            "SELECT id FROM const_group_apply WHERE username = ? AND group_id = ? "
            "AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (username, group_id),
        ).fetchone()
        if pending:
            return _fail(
                f"用户 {username} 已有加入用户组 {group_id} 的待审批申请 "
                f"#{pending['id']}，请勿重复提交",
                apply_id=pending["id"],
            )
        cur = conn.execute(
            "INSERT INTO const_group_apply "
            "(username, group_id, status, reason, decided_by, created_at, decided_at) "
            "VALUES (?, ?, 'pending', ?, '', ?, '')",
            (username, group_id, reason, _now()),
        )
        apply_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM const_group_apply WHERE id = ?", (apply_id,)
        ).fetchone()

    return {
        "success": True,
        "id": apply_id,
        "apply": _apply_dict(dict(row)),
        "message": f"已提交加入用户组 {group_id} 的申请 #{apply_id}，等待组管理员审批",
    }


def list_group_applies(status: str = "", db_path=None) -> dict:
    """列用户组申请（空表示全部）。"""
    status = _text(status, 16)
    if status and status not in APPLY_STATUSES:
        return _fail(f"status 只能是 {'/'.join(APPLY_STATUSES)} 或留空")
    with _db(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM const_group_apply WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM const_group_apply ORDER BY id DESC").fetchall()
    items = [_apply_dict(dict(row)) for row in rows]
    return {"success": True, "count": len(items), "applies": items, "message": None}


def decide_group_apply(
    apply_id: int,
    approve: bool,
    decided_by: str,
    reason: str = "",
    db_path=None,
) -> dict:
    """组管理员审批加入申请。

    只改 ``const_group_apply.status``，**不**写 ``const_approval_role``：组申请仅记录状态，
    具体审批权限仍由管理员在 approval-roles 中显式授权（需求 SR01-AR04 的职责分离）。
    """
    decision = _as_bool(approve)
    if decision is None:
        return _fail("approve 必须是布尔值（true 表示通过，false 表示驳回）")
    decided_by = _text(decided_by, 64)
    reason = _text(reason, 400)
    if not decided_by:
        return _fail("审批人 decided_by 不能为空")
    if not decision and not reason:
        return _fail("驳回必须填写理由（reason）")

    with _LOCK, _db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM const_group_apply WHERE id = ?", (apply_id,)
        ).fetchone()
        if not row:
            return _fail(f"用户组申请 #{apply_id} 不存在")
        apply_row = _apply_dict(dict(row))
        if apply_row["status"] != "pending":
            return _fail(
                f"用户组申请 #{apply_row['id']} 当前状态为「{apply_row['status_label']}」，"
                "只有待审批（pending）的申请可以决策",
                status=apply_row["status"],
            )
        conn.execute(
            "UPDATE const_group_apply SET status = ?, decided_by = ?, reason = ?, "
            "decided_at = ? WHERE id = ?",
            (
                "approved" if decision else "rejected",
                decided_by,
                reason,
                _now(),
                apply_row["id"],
            ),
        )
        updated = conn.execute(
            "SELECT * FROM const_group_apply WHERE id = ?", (apply_row["id"],)
        ).fetchone()

    result = {
        "success": True,
        "apply": _apply_dict(dict(updated)),
    }
    result["message"] = (
        f"已通过用户组申请 #{apply_row['id']}：{apply_row['username']} → {apply_row['group_id']}"
        if decision
        else f"已驳回用户组申请 #{apply_row['id']}：{reason}"
    )
    return result


# ---------------------------------------------------------------- HTTP 路由


def register_routes(app, base_dir: Path, db_path=None) -> None:
    """挂载 /api/v1/constants/* 审批相关路由（异常统一包成中文 message）。"""
    global _BASE_DIR, _DB_PATH
    _BASE_DIR = Path(base_dir)
    _DB_PATH = Path(db_path) if db_path else Path(ate_db.DB_PATH)
    ate_db.init_db(_DB_PATH)

    from fastapi import Body

    @app.post("/api/v1/constants/requests")
    def api_create_request(payload: dict = Body(...)):
        try:
            body = payload or {}
            return create_request(
                body.get("kind", ""),
                body.get("pn", ""),
                body.get("model_key", ""),
                body.get("payload") if body.get("payload") is not None else {},
                body.get("applicant", ""),
                db_path=_DB_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"提交审批申请失败：{exc}"}

    @app.get("/api/v1/constants/requests")
    def api_list_requests(status: str = ""):
        try:
            return list_requests(status, db_path=_DB_PATH)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"读取审批单失败：{exc}"}

    @app.post("/api/v1/constants/requests/{request_id}/decide")
    def api_decide_request(request_id: int, payload: dict = Body(...)):
        try:
            body = payload or {}
            return decide_request(
                request_id,
                body.get("approve"),
                body.get("approver", ""),
                body.get("reason", ""),
                db_path=_DB_PATH,
                groups=body.get("groups"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"审批操作失败：{exc}"}

    @app.post("/api/v1/constants/requests/{request_id}/push")
    def api_push_result(request_id: int, payload: Optional[dict] = Body(default=None)):
        try:
            body = payload or {}
            return push_result(request_id, body.get("channel", "dry-run"), db_path=_DB_PATH)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"推送审批结果失败：{exc}"}

    @app.get("/api/v1/constants/approval-roles")
    def api_list_roles(product: str = ""):
        try:
            return list_approval_roles(product, db_path=_DB_PATH)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"读取审批权限失败：{exc}"}

    @app.put("/api/v1/constants/approval-roles")
    def api_save_role(payload: dict = Body(...)):
        try:
            body = payload or {}
            return save_approval_role(
                body.get("product", ""),
                body.get("kind", ""),
                body.get("group_id", ""),
                body.get("enabled", True),
                db_path=_DB_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"保存审批权限失败：{exc}"}

    @app.post("/api/v1/constants/groups/apply")
    def api_apply_group(payload: dict = Body(...)):
        try:
            body = payload or {}
            return apply_group(
                body.get("username", ""),
                body.get("group_id", ""),
                body.get("reason", ""),
                db_path=_DB_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"提交用户组申请失败：{exc}"}

    @app.get("/api/v1/constants/groups/applies")
    def api_list_group_applies(status: str = ""):
        try:
            return list_group_applies(status, db_path=_DB_PATH)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"读取用户组申请失败：{exc}"}

    @app.post("/api/v1/constants/groups/applies/{apply_id}/decide")
    def api_decide_group_apply(apply_id: int, payload: dict = Body(...)):
        try:
            body = payload or {}
            return decide_group_apply(
                apply_id,
                body.get("approve"),
                body.get("decided_by", ""),
                body.get("reason", ""),
                db_path=_DB_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"用户组申请审批失败：{exc}"}
