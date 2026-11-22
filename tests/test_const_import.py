# -*- coding: utf-8 -*-
"""const_import（SR01-AR01 全量导入 / SR01-AR02 部分导入与备份）单元测试。

全部用 tmp_path 独立库与临时树目录，不碰 repo/ 与 controller.db。
运行：
    cd D:\\ATE\\MockController
    & 'D:\\ATE\\MockRunner\\backend\\.venv\\Scripts\\python.exe' -m pytest tests\\test_const_import.py -q --basetemp=.pytest_tmp
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ate_db
import const_import
import constants

VERSION = "2026.11.01"
PRODUCT = "1000-A"
SIGNATURE = "ate-controller-export-v1"
_OMIT = object()


# ---------------------------------------------------------------- 造包工具


def _field(
    key: str,
    ftype: str = "float",
    default: str = "0.0",
    low: str = "-1.0",
    high: str = "1.0",
    *,
    unit: str = "",
    values: str = "",
    value: str | None = None,
    min_attr: str | None = None,
    max_attr: str | None = None,
) -> str:
    attrs = [f'key="{key}"', f'label="{key}"', f'type="{ftype}"']
    if unit:
        attrs.append(f'unit="{unit}"')
    if ftype in ("float", "int"):
        if min_attr is not None or low is not None:
            if min_attr != "":
                attrs.append(f'min="{min_attr if min_attr is not None else low}"')
        if max_attr is not None or high is not None:
            if max_attr != "":
                attrs.append(f'max="{max_attr if max_attr is not None else high}"')
    if ftype == "enum":
        attrs.append(f'values="{values}"')
    if default is not None:
        attrs.append(f'default="{default}"')
    if value is not None:
        attrs.append(f'value="{value}"')
    return "<Field " + " ".join(attrs) + " />"


def _xml(
    module: str,
    pn: str,
    fields: list[str],
    *,
    version: str = VERSION,
    product: str = PRODUCT,
    subsystem: str = "SPM",
    name: str | None = None,
    calibrated: bool = False,
    sn: str = "",
) -> bytes:
    head = (
        '<MachineConstants schema="ate.constants.v2" '
        f'module="{module}" pn="{pn}" version="{version}" '
        f'calibrated="{"true" if calibrated else "false"}"'
    )
    if sn:
        head += f' sn="{sn}"'
    head += ">"
    meta = (
        f'<Meta product="{product}" subsystem="{subsystem}" '
        f'name="{name or module}" />'
    )
    body = "".join(fields)
    return (head + meta + "<Fields>" + body + "</Fields></MachineConstants>").encode("utf-8")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def _manifest_bytes(
    files: dict[str, bytes],
    version: str,
    signature: object = SIGNATURE,
    extra: dict | None = None,
) -> bytes:
    entries = [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for path, data in files.items()
    ]
    manifest: dict = {
        "schema": "ate.constants.v2",
        "version": version,
        "exported_at": "2026-11-22T10:00:00+08:00",
        "files": entries,
    }
    if signature is not _OMIT:
        manifest["signature"] = signature
    if extra:
        manifest.update(extra)
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def _package(
    files: dict[str, bytes],
    version: str = VERSION,
    signature: object = SIGNATURE,
    extra: dict | None = None,
) -> bytes:
    bundle = {"manifest.json": _manifest_bytes(files, version, signature, extra)}
    bundle.update(files)
    return _zip_bytes(bundle)


def _tampered_package(files: dict[str, bytes], version: str = VERSION) -> bytes:
    """保留原始清单（哈希不变），但改掉包内 xml 的一个字节区间 → 校验必然失败。"""
    manifest = _manifest_bytes(files, version)
    evil: dict[str, bytes] = {}
    for path, data in files.items():
        if path.endswith(".xml"):
            for needle, replacement in (
                (b"gain_offset", b"gain_offsat"),
                (b"threshold", b"thresholE"),
                (b"head_grade", b"head_gradE"),
            ):
                if needle in data:
                    data = data.replace(needle, replacement)
                    break
        evil[path] = data
    return _zip_bytes({"manifest.json": manifest, **evil})


# ---------------------------------------------------------------- 查库工具


def _rows(db, sql: str, args: tuple = ()):
    conn = ate_db.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _models(db):
    return _rows(
        db,
        "SELECT version, pn, model_key, name, fields_json, field_count "
        "FROM constant_model ORDER BY version, pn, model_key",
    )


def _model(db, model_key: str):
    rows = _rows(
        db,
        "SELECT * FROM constant_model WHERE model_key = ? ORDER BY id DESC",
        (model_key,),
    )
    return rows[0] if rows else None


def _snapshot(db) -> tuple:
    conn = ate_db.connect(db)
    try:
        models = [
            tuple(r)
            for r in conn.execute(
                "SELECT version, pn, model_key, name, fields_json, field_count, updated_at "
                "FROM constant_model ORDER BY version, pn, model_key"
            )
        ]
        versions = [
            tuple(r)
            for r in conn.execute(
                "SELECT version, model_count, field_count, is_active "
                "FROM constant_version ORDER BY version"
            )
        ]
        backups = conn.execute("SELECT COUNT(*) FROM const_backup").fetchone()[0]
        logs = conn.execute("SELECT COUNT(*) FROM const_import_log").fetchone()[0]
    finally:
        conn.close()
    return models, versions, backups, logs


@pytest.fixture()
def db(tmp_path, monkeypatch):
    # 装备树指向空目录，避免 PN 前缀被真实 tree.json 推断成别的产品/子系统
    monkeypatch.setenv("ATE_TREE_DIR", str(tmp_path / "tree"))
    path = tmp_path / "controller.db"
    ate_db.init_db(path)
    return path


def _three_model_files(version: str = VERSION) -> dict[str, bytes]:
    return {
        "SPM/SPM_RH.xml": _xml(
            "SPM_RH",
            "SPM-RH-01",
            [_field("gain_offset"), _field("bias_voltage", unit="V", default="1.85",
                                           low="1.5", high="2.2")],
            version=version,
            name="SPM 读头机常数",
        ),
        "SPM/SPM_TP.xml": _xml(
            "SPM_TP",
            "SPM-RH-01",
            [_field("threshold", "int", "3", "0", "10")],
            version=version,
        ),
        "OPS/OPS_LD.xml": _xml(
            "OPS_LD",
            "OPS-LD-02",
            [_field("head_grade", "enum", "B", values="A,B,C")],
            version=version,
            subsystem="OPS",
        ),
    }


# ---------------------------------------------------------------- 全量导入


def test_full_import_writes_models_and_version(db):
    files = _three_model_files()
    result = const_import.import_full(_package(files), "bundle.zip", "alice", db_path=db)

    assert result["success"] is True, result
    assert result["already_imported"] is False
    assert result["stats"] == {
        "pn_count": 2,
        "model_count": 3,
        "field_count": 4,
        "value_count": 0,
    }
    assert result["file_count"] == 3
    assert len(result["package_hash"]) == 64

    models = _models(db)
    assert len(models) == 3
    assert {(m["pn"], m["model_key"]) for m in models} == {
        ("SPM-RH-01", "SPM_RH"),
        ("SPM-RH-01", "SPM_TP"),
        ("OPS-LD-02", "OPS_LD"),
    }
    assert all(m["version"] == VERSION for m in models)

    versions = _rows(db, "SELECT * FROM constant_version")
    assert len(versions) == 1
    assert versions[0]["version"] == VERSION
    assert versions[0]["is_active"] == 1
    assert versions[0]["model_count"] == 3
    assert versions[0]["field_count"] == 4
    assert versions[0]["imported_by"] == "alice"

    # 结构里 enum 的取值必须落到 fields_json（Runner 解析要用）
    head_grade = json.loads(_model(db, "OPS_LD")["fields_json"])
    assert head_grade[0]["type"] == "enum"
    assert head_grade[0]["options"] == ["A", "B", "C"]


def test_full_import_rejects_tampered_file_and_keeps_db(db):
    good = _three_model_files()
    first = const_import.import_full(_package(good), "good.zip", "alice", db_path=db)
    assert first["success"] is True, first
    before = _snapshot(db)

    changed = _three_model_files("2026.12.01")
    bad = const_import.import_full(_tampered_package(changed, "2026.12.01"), "bad.zip",
                                   "mallory", db_path=db)

    assert bad["success"] is False
    assert "SHA-256" in bad["message"]
    assert _snapshot(db) == before
    assert _rows(db, "SELECT version FROM constant_version")[0]["version"] == VERSION


def test_full_import_rejects_missing_manifest(db):
    blob = _zip_bytes({"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field("gain_offset")])})
    result = const_import.import_full(blob, "no-manifest.zip", "alice", db_path=db)

    assert result["success"] is False
    assert "manifest.json" in result["message"]
    assert _snapshot(db) == ([], [], 0, 0)


def test_full_import_rejects_unlisted_extra_file(db):
    files = _three_model_files()
    blob = _package(files)
    # 追加一个清单里没有的文件
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    entries["SPM/backdoor.xml"] = _xml("SPM_XX", "SPM-RH-01", [_field("x")])
    result = const_import.import_full(_zip_bytes(entries), "extra.zip", "alice", db_path=db)

    assert result["success"] is False
    assert "未声明" in result["message"]
    assert _snapshot(db) == ([], [], 0, 0)


def test_package_limits_rejected(db, monkeypatch):
    """复用 constants 的 20MB / 200 文件上限（这里把阈值调小来验证分支）。"""
    blob = _package(_three_model_files())
    before = _snapshot(db)

    with monkeypatch.context() as patch:
        patch.setattr(constants, "IMPORT_MAX_ZIP_BYTES", len(blob) - 1)
        result = const_import.import_full(blob, "too-big.zip", "alice", db_path=db)
    assert result["success"] is False
    assert "上限" in result["message"]

    with monkeypatch.context() as patch:
        patch.setattr(constants, "IMPORT_MAX_ZIP_FILES", 2)
        result = const_import.import_full(blob, "too-many.zip", "alice", db_path=db)
    assert result["success"] is False
    assert "上限" in result["message"]

    assert _snapshot(db) == before


def test_full_import_same_package_is_idempotent(db):
    blob = _package(_three_model_files())
    first = const_import.import_full(blob, "bundle.zip", "alice", db_path=db)
    assert first["success"] is True, first
    before = _snapshot(db)

    second = const_import.import_full(blob, "bundle.zip", "alice", db_path=db)
    assert second["success"] is True
    assert second["already_imported"] is True
    assert _snapshot(db) == before
    logs = _rows(db, "SELECT kind FROM const_import_log")
    assert len(logs) == 1 and logs[0]["kind"] == "full"


@pytest.mark.parametrize(
    "fields,keyword",
    [
        ([_field("gain_offset", "float", "0.0", min_attr="")], "min"),
        ([_field("head_grade", "enum", "B", values="")], "values"),
        ([_field("dup"), _field("dup")], "重复"),
        ([_field("threshold", "int", "99", "0", "10")], "范围"),
        ([_field("gain_offset", "money")], "type"),
    ],
)
def test_full_import_rejects_invalid_field_structure(db, fields, keyword):
    files = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", fields)}
    result = const_import.import_full(_package(files), "bad-struct.zip", "alice", db_path=db)

    assert result["success"] is False, result
    assert keyword in result["message"], result["message"]
    assert _snapshot(db) == ([], [], 0, 0)


# ---------------------------------------------------------------- 部分导入


def _import_baseline(db) -> bytes:
    base = {
        "SPM/SPM_RH.xml": _xml(
            "SPM_RH",
            "SPM-RH-01",
            [_field("gain_offset"), _field("bias_voltage")],
            name="SPM 读头机常数",
        )
    }
    result = const_import.import_full(_package(base), "baseline.zip", "alice", db_path=db)
    assert result["success"] is True, result
    return _package(base)


def _partial_files() -> dict[str, bytes]:
    return {
        "SPM/SPM_RH.xml": _xml(
            "SPM_RH",
            "SPM-RH-01",
            [
                _field("gain_offset", value="1.0"),
                _field("bias_voltage"),
                _field("head_grade", "enum", "B", values="A,B,C"),
            ],
            name="SPM 读头机常数 v2",
        ),
        "SPM/SPM_TP.xml": _xml(
            "SPM_TP", "SPM-RH-01", [_field("threshold", "int", "3", "0", "10")]
        ),
    }


def test_partial_import_overwrites_and_backs_up(db):
    _import_baseline(db)
    result = const_import.import_partial(_package(_partial_files()), "partial.zip", "bob",
                                         db_path=db)

    assert result["success"] is True, result
    assert result["version"] == VERSION
    assert result["replaced"] == 1
    assert result["added"] == 1

    model = _model(db, "SPM_RH")
    fields = json.loads(model["fields_json"])
    assert [f["key"] for f in fields] == ["gain_offset", "bias_voltage", "head_grade"]
    assert model["name"] == "SPM 读头机常数 v2"
    assert len(_models(db)) == 2

    backups = const_import.list_backups(db_path=db)
    assert backups["success"] is True
    assert backups["count"] == 1 and backups["total"] == 1
    item = backups["items"][0]
    assert item["pn"] == "SPM-RH-01"
    assert item["model_key"] == "SPM_RH"
    assert item["version"] == VERSION
    assert item["replaced_by"] == VERSION
    assert item["reason"]
    assert item["operator"] == "bob"
    assert item["field_count"] == 2
    assert [f["key"] for f in item["fields"]] == ["gain_offset", "bias_voltage"]

    # 包内 value 生效
    values = _rows(
        db,
        "SELECT field_key, value FROM constant_value WHERE model_key = 'SPM_RH' "
        "ORDER BY field_key",
    )
    assert {r["field_key"]: r["value"] for r in values} == {"gain_offset": "1.0"}


def test_partial_import_requires_signature(db):
    _import_baseline(db)
    before = _snapshot(db)
    files = _partial_files()

    for signature in (_OMIT, "", "   "):
        result = const_import.import_partial(
            _package(files, signature=signature), "unsigned.zip", "bob", db_path=db
        )
        assert result["success"] is False, result
        assert "signature" in result["message"] or "签名" in result["message"]

    assert _snapshot(db) == before


def test_partial_import_same_package_is_idempotent(db):
    _import_baseline(db)
    blob = _package(_partial_files())
    first = const_import.import_partial(blob, "partial.zip", "bob", db_path=db)
    assert first["success"] is True, first
    after_first = _snapshot(db)

    second = const_import.import_partial(blob, "partial.zip", "bob", db_path=db)
    assert second["success"] is True
    assert second["already_imported"] is True
    assert _snapshot(db) == after_first
    assert const_import.list_backups(db_path=db)["count"] == 1


def test_partial_import_rolls_back_on_error(db):
    """包结构不合规时整包拒绝：既不覆盖旧模型，也不写备份。"""
    _import_baseline(db)
    before = _snapshot(db)
    files = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field("gain_offset", "enum", "B",
                                                                    values="")])}
    result = const_import.import_partial(_package(files), "broken.zip", "bob", db_path=db)

    assert result["success"] is False
    assert _snapshot(db) == before
    assert const_import.list_backups(db_path=db)["count"] == 0


# ---------------------------------------------------------------- 回滚


def test_partial_import_keeps_values_of_surviving_fields(db):
    """只改结构时，旧标定值不应被覆盖导入抹掉（仅保留仍存在的字段）。"""
    base = {
        "SPM/SPM_RH.xml": _xml(
            "SPM_RH", "SPM-RH-01", [_field("gain_offset", value="1.5"), _field("bias_voltage")]
        )
    }
    assert const_import.import_full(_package(base), "v1.zip", "alice", db_path=db)["success"]

    part = {
        "SPM/SPM_RH.xml": _xml(
            "SPM_RH", "SPM-RH-01",
            [_field("gain_offset"), _field("bias_voltage"),
             _field("head_grade", "enum", "B", values="A,B,C")],
        )
    }
    assert const_import.import_partial(_package(part), "p.zip", "bob", db_path=db)["success"]

    values = {
        row["field_key"]: row["value"]
        for row in _rows(
            db, "SELECT field_key, value FROM constant_value WHERE model_key = 'SPM_RH'"
        )
    }
    assert values == {"gain_offset": "1.5"}


def test_restore_backup_restores_structure(db):
    _import_baseline(db)
    assert const_import.import_partial(_package(_partial_files()), "partial.zip", "bob",
                                       db_path=db)["success"]
    item = const_import.list_backups(db_path=db)["items"][0]

    result = const_import.restore_backup(item["id"], "carol", db_path=db)

    assert result["success"] is True, result
    assert result["pn"] == "SPM-RH-01" and result["model_key"] == "SPM_RH"
    assert result["field_count"] == 2

    model = _model(db, "SPM_RH")
    assert model["name"] == "SPM 读头机常数"
    assert [f["key"] for f in json.loads(model["fields_json"])] == [
        "gain_offset",
        "bias_voltage",
    ]
    assert model["field_count"] == 2

    restored = _rows(db, "SELECT restored_at FROM const_backup WHERE id = ?", (item["id"],))
    assert restored[0]["restored_at"]

    missing = const_import.restore_backup(9999, "carol", db_path=db)
    assert missing["success"] is False and "不存在" in missing["message"]


def test_backup_list_filters_and_limit(db):
    _import_baseline(db)
    for bump in ("v2", "v3"):
        files = {
            "SPM/SPM_RH.xml": _xml(
                "SPM_RH", "SPM-RH-01",
                [_field("gain_offset"), _field("bias_voltage")],
                name=f"SPM 读头机常数 {bump}",
            ),
            "SPM/SPM_TP.xml": _xml(
                "SPM_TP", "SPM-RH-01", [_field("threshold", "int", "3", "0", "10")]
            ),
        }
        assert const_import.import_partial(_package(files), f"{bump}.zip", "bob",
                                           db_path=db)["success"]

    all_items = const_import.list_backups(db_path=db)
    # 第一次部分导入覆盖 SPM_RH（1 条），第二次覆盖 SPM_RH 与 SPM_TP（2 条）
    assert all_items["total"] == 3

    only_tp = const_import.list_backups(pn="SPM-RH-01", model_key="SPM_TP", db_path=db)
    assert only_tp["total"] == 1
    assert only_tp["items"][0]["model_key"] == "SPM_TP"

    assert const_import.list_backups(pn="NOPE-01", db_path=db)["count"] == 0
    limited = const_import.list_backups(limit=1, db_path=db)
    assert limited["count"] == 1 and limited["limit"] == 1


# ---------------------------------------------------------------- 导入日志


def test_import_log_records_two_kinds(db):
    full_blob = _package(_three_model_files())
    assert const_import.import_full(full_blob, "bundle.zip", "alice", db_path=db)["success"]
    assert const_import.import_partial(_package(_partial_files()), "partial.zip", "bob",
                                       db_path=db)["success"]

    logs = _rows(db, "SELECT * FROM const_import_log ORDER BY id")
    assert [row["kind"] for row in logs] == ["full", "partial"]

    assert logs[0]["prev_version"] == ""
    assert logs[0]["new_version"] == VERSION
    assert logs[0]["operator"] == "alice"
    assert logs[0]["file_count"] == 3
    assert len(logs[0]["package_hash"]) == 64

    assert logs[1]["prev_version"] == VERSION
    assert logs[1]["new_version"] == VERSION
    assert logs[1]["operator"] == "bob"
    assert len(logs[1]["package_hash"]) == 64
    assert logs[1]["note"]


# ---------------------------------------------------------------- 路由


def test_routes_import_backup_and_restore(db, tmp_path):
    app = FastAPI()
    const_import.register_routes(app, tmp_path, db)
    client = TestClient(app)

    full_body = {
        "content_base64": base64.b64encode(_package(_three_model_files())).decode(),
        "filename": "bundle.zip",
        "operator": "web",
    }
    resp = client.post("/api/v1/constants/import/full", json=full_body)
    data = resp.json()
    assert resp.status_code == 200 and data["success"] is True, data
    assert data["stats"]["model_count"] == 3

    partial_body = {
        "content_base64": base64.b64encode(_package(_partial_files())).decode(),
        "filename": "partial.zip",
        "operator": "web",
    }
    resp = client.post("/api/v1/constants/import/partial", json=partial_body)
    data = resp.json()
    assert data["success"] is True, data
    assert data["replaced"] == 2  # 全量导入过 SPM_RH 与 SPM_TP，两者都被覆盖
    assert data["added"] == 0

    resp = client.get(
        "/api/v1/constants/backups", params={"pn": "SPM-RH-01", "model_key": "SPM_RH"}
    )
    data = resp.json()
    assert data["success"] is True and data["count"] == 1
    backup_id = data["items"][0]["id"]

    resp = client.post(f"/api/v1/constants/backups/{backup_id}/restore",
                       json={"operator": "web"})
    data = resp.json()
    assert data["success"] is True, data
    assert data["field_count"] == 2

    # 异常路径：坏 Base64 / 空包 都要返回 success=false 的中文消息，而不是 500
    bad = client.post("/api/v1/constants/import/full",
                      json={"content_base64": "!!!not-base64!!!", "filename": "x.zip"}).json()
    assert bad["success"] is False and "Base64" in bad["message"]

    empty = client.post("/api/v1/constants/import/partial", json={}).json()
    assert empty["success"] is False and "content_base64" in empty["message"]

    missing = client.post("/api/v1/constants/backups/9999/restore", json={}).json()
    assert missing["success"] is False and "不存在" in missing["message"]

    not_object = client.post("/api/v1/constants/import/full", json=[]).json()
    assert not_object["success"] is False and "JSON 对象" in not_object["message"]

    weird_limit = client.get("/api/v1/constants/backups", params={"limit": "abc"}).json()
    assert weird_limit["success"] is True and weird_limit["limit"] == 100
