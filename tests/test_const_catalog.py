# -*- coding: utf-8 -*-
"""const_catalog（SR01-AR05 ~ AR08、SR02-AR09）测试

全部用 tmp_path 隔离：独立 sqlite 库 + 独立数据根（<tmp>/data），
不读写工程的 repo/ 与 controller.db。

运行：
    cd D:\\ATE\\MockController
    & 'D:\\ATE\\MockRunner\\backend\\.venv\\Scripts\\python.exe' -m pytest tests\\test_const_catalog.py -q --basetemp=.pytest_tmp
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ate_db  # noqa: E402
import const_catalog  # noqa: E402

FIELDS_RH = [
    {"key": "gain_offset", "label": "增益偏移", "type": "float", "unit": "mV", "default": 0.0},
    {"key": "bias_voltage", "label": "偏置电压", "type": "float", "unit": "V", "default": 1.85},
]
FIELDS_ONE = [{"key": "level", "label": "档位", "type": "enum", "unit": "", "default": "B"}]


# ---------------------------------------------------------------- 夹具与造数


@pytest.fixture()
def env(tmp_path):
    """一套独立环境：临时 base_dir + 临时 db + 独立 FastAPI app。"""
    base = tmp_path / "data"
    base.mkdir()
    (base / "tps").mkdir()
    db = tmp_path / "controller.db"
    app = FastAPI()
    const_catalog.register_routes(app, base, db)
    return {"base": base, "db": db, "client": TestClient(app), "tmp": tmp_path}


def _exec(db: Path, sql: str, rows: list[tuple] | None = None) -> None:
    ate_db.init_db(db)
    with ate_db.connect(db) as conn:
        if rows is None:
            conn.execute(sql)
        else:
            conn.executemany(sql, rows)
        conn.commit()


def _version(db: Path, version: str, imported_at: str, is_active: int = 0, who: str = "导入员") -> None:
    _exec(
        db,
        "INSERT OR REPLACE INTO constant_version "
        "(version, label, source, note, model_count, field_count, imported_at, imported_by, is_active, checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(version, version, "test", "", 0, 0, imported_at, who, is_active, "")],
    )


def _model(
    db: Path,
    version: str,
    pn: str,
    product: str,
    subsystem: str,
    model_key: str,
    fields: list[dict],
    name: str = "",
    updated_at: str = "2026-11-01T10:00:00+08:00",
) -> None:
    _exec(
        db,
        "INSERT INTO constant_model "
        "(version, pn, product, subsystem, model_key, name, category, description, unit, "
        " fields_json, field_count, struct_checksum, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                version,
                pn,
                product,
                subsystem,
                model_key,
                name or f"{model_key} 机常数",
                "",
                "",
                "",
                json.dumps(fields, ensure_ascii=False),
                len(fields),
                "struct-" + model_key,
                updated_at,
            )
        ],
    )


def _tps(base: Path, tps_id: str = "demo-tps", **extra) -> Path:
    payload = {
        "schema": "tps.v2",
        "id": tps_id,
        "name": "演示 TPS",
        "version": "1.0.0",
        "constant_bindings": [],
        **extra,
    }
    path = base / "tps" / tps_id / "tps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _tps_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _base_models(env) -> None:
    """3 个型号 / 2 个产品 / 2 个子系统；另有一条旧版本行用于验证生效版本过滤。"""
    db = env["db"]
    _version(db, "2026.10.01", "2026-10-01T09:00:00+08:00")
    _version(db, "2026.11.01", "2026-11-01T09:00:00+08:00", is_active=1)
    _model(db, "2026.11.01", "SPM-RH-01", "1000-A", "SPM", "SPM_RH", FIELDS_RH)
    _model(db, "2026.11.01", "SPM-RH-01", "1000-A", "SPM", "SPM_TEMP", FIELDS_ONE)
    _model(db, "2026.11.01", "WA-RF-01", "1000-A", "WA", "WA_RF", FIELDS_ONE)
    _model(db, "2026.11.01", "SD-SV-01", "2000-B", "SD", "SD_SV", FIELDS_ONE)
    _model(db, "2026.10.01", "SPM-RH-01", "1000-A", "SPM", "SPM_RH", FIELDS_RH)


# ---------------------------------------------------------------- SR01-AR05 列表


def test_model_list_filters_by_product_subsystem_pn(env):
    _base_models(env)
    client = env["client"]

    all_rows = client.get("/api/v1/constants/models/detail").json()
    assert all_rows["success"] is True
    assert all_rows["version"] == "2026.11.01"  # 默认只看生效版本
    assert all_rows["count"] == 4
    assert {m["model_key"] for m in all_rows["models"]} == {"SPM_RH", "SPM_TEMP", "WA_RF", "SD_SV"}
    first = all_rows["models"][0]
    assert set(["model_key", "name", "field_count", "version", "updated_at", "is_calibration"]).issubset(first)
    assert isinstance(first["is_calibration"], bool)

    by_product = client.get(
        "/api/v1/constants/models/detail", params={"product": "2000-B"}
    ).json()
    assert by_product["count"] == 1
    assert by_product["models"][0]["pn"] == "SD-SV-01"

    by_subsystem = client.get(
        "/api/v1/constants/models/detail", params={"subsystem": "spm"}
    ).json()
    assert by_subsystem["count"] == 2
    assert {m["model_key"] for m in by_subsystem["models"]} == {"SPM_RH", "SPM_TEMP"}

    by_pn = client.get(
        "/api/v1/constants/models/detail", params={"pn": "wa-rf-01"}
    ).json()
    assert by_pn["count"] == 1 and by_pn["models"][0]["model_key"] == "WA_RF"

    combo = client.get(
        "/api/v1/constants/models/detail", params={"product": "1000-A", "subsystem": "SPM"}
    ).json()
    assert combo["count"] == 2

    empty = client.get(
        "/api/v1/constants/models/detail", params={"product": "9999-Z"}
    ).json()
    assert empty["success"] is True and empty["count"] == 0 and empty["models"] == []


def test_model_list_calibration_flag_from_field_and_calib_tps(env):
    _base_models(env)
    db = env["db"]
    _model(
        db,
        "2026.11.01",
        "SPM-RH-01",
        "1000-A",
        "SPM",
        "SPM_CAL",
        [{"key": "gain", "label": "增益", "type": "float", "calibration": True}],
    )
    _tps(
        env["base"],
        "calib-tps",
        setup=[{"id": "S1", "name": "标定准备", "case": "testcase::setup"}],
        cmd_suit=[{"id": "C1", "name": "标定", "case": "testcase::calib", "type": "calib"}],
        constant_bindings=[
            {"pn": "SD-SV-01", "model_key": "SD_SV", "models": ["SD_SV"], "alias": "伺服", "source": "default"}
        ],
    )

    rows = env["client"].get("/api/v1/constants/models/detail").json()["models"]
    by_key = {m["model_key"]: m for m in rows}
    assert by_key["SPM_CAL"]["is_calibration"] is True
    assert by_key["SPM_CAL"]["calibration_fields"] == ["gain"]
    assert by_key["SD_SV"]["is_calibration"] is True  # 被含标定步骤的 TPS 引用
    assert by_key["SPM_RH"]["is_calibration"] is False

    assert const_catalog.is_calibration_model(
        "SD-SV-01", "SD_SV", db_path=db, base_dir=env["base"]
    ) is True
    assert const_catalog.is_calibration_model(
        "SPM-RH-01", "SPM_CAL", db_path=db, base_dir=env["base"]
    ) is True
    assert const_catalog.is_calibration_model(
        "SPM-RH-01", "SPM_RH", db_path=db, base_dir=env["base"]
    ) is False


# ---------------------------------------------------------------- SR01-AR05 历史


def test_model_history_desc_with_diff_summary(env):
    db = env["db"]
    _version(db, "2026.10.01", "2026-10-01T09:00:00+08:00", who="张三")
    _version(db, "2026.11.01", "2026-11-01T09:00:00+08:00", is_active=1, who="李四")
    _model(
        db,
        "2026.10.01",
        "SPM-RH-01",
        "1000-A",
        "SPM",
        "SPM_RH",
        [
            {"key": "gain_offset", "type": "float"},
            {"key": "head_grade", "type": "enum"},
        ],
        updated_at="2026-10-01T09:00:00+08:00",
    )
    _model(
        db,
        "2026.11.01",
        "SPM-RH-01",
        "1000-A",
        "SPM",
        "SPM_RH",
        [
            {"key": "gain_offset", "type": "int"},
            {"key": "bias_voltage", "type": "float"},
        ],
        updated_at="2026-11-01T09:00:00+08:00",
    )

    data = env["client"].get("/api/v1/constants/models/SPM-RH-01/SPM_RH/history").json()
    assert data["success"] is True
    assert data["count"] == 2
    assert [v["version"] for v in data["versions"]] == ["2026.11.01", "2026.10.01"]  # imported_at 倒序

    newest, oldest = data["versions"]
    assert newest["imported_at"] > oldest["imported_at"]
    assert newest["change_type"] == "modified"
    assert newest["change_type_label"] == "结构变更"
    assert newest["is_active"] is True and oldest["is_active"] is False
    assert newest["diff"]["added"] == ["bias_voltage"]
    assert newest["diff"]["removed"] == ["head_grade"]
    assert newest["diff"]["type_changed"] == [{"key": "gain_offset", "from": "float", "to": "int"}]
    assert "新增 1 个字段" in newest["diff_summary"]
    assert "删除 1 个字段" in newest["diff_summary"]
    assert "类型变化 1 个" in newest["diff_summary"]

    assert oldest["change_type"] == "initial"
    assert "首次导入" in oldest["diff_summary"]

    missing = env["client"].get("/api/v1/constants/models/SPM-RH-01/NOPE/history").json()
    assert missing["success"] is False and "未找到常数模型" in missing["message"]


# ---------------------------------------------------------------- SR01-AR07 徽标


def test_badge_three_states_and_reject_invalid(env):
    client = env["client"]
    badge_file = env["base"] / "badge.json"

    default = client.get("/api/v1/constants/calib-badge").json()
    assert default["success"] is True and default["position"] == "before"
    assert badge_file.is_file() is False

    for position in ("before", "after", "icon"):
        saved = client.put("/api/v1/constants/calib-badge", json={"position": position}).json()
        assert saved["success"] is True, saved
        assert saved["position"] == position
        read_back = client.get("/api/v1/constants/calib-badge").json()
        assert read_back["position"] == position
        assert _tps_payload(badge_file)["position"] == position

    bad = client.put("/api/v1/constants/calib-badge", json={"position": "middle"}).json()
    assert bad["success"] is False
    assert "before / after / icon" in bad["message"]
    assert client.get("/api/v1/constants/calib-badge").json()["position"] == "icon"  # 非法值不落盘

    # 契约里的旧路径同一份配置
    alias = client.get("/api/v1/settings/tps-badge").json()
    assert alias["position"] == "icon"
    alias_put = client.put("/api/v1/settings/tps-badge", json={"position": "before"}).json()
    assert alias_put["success"] is True
    assert client.get("/api/v1/constants/calib-badge").json()["position"] == "before"


# ---------------------------------------------------------------- SR01-AR08 菜单


def test_metadata_menu_counts(env):
    base, db = env["base"], env["db"]
    (base / "tree").mkdir()
    (base / "tree" / "tree.json").write_text(
        json.dumps(
            {"version": 1, "products": [{"id": "1000-A"}, {"id": "2000-B"}], "subsystems": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "processes.json").write_text(
        json.dumps(
            {"version": 1, "processes": [{"id": "P1"}, {"id": "P2"}, {"id": "P3"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 3 个 (pn, model_key)，跨两个版本重复一条 -> 去重后仍是 3
    _version(db, "2026.10.01", "2026-10-01T09:00:00+08:00")
    _version(db, "2026.11.01", "2026-11-01T09:00:00+08:00", is_active=1)
    _model(db, "2026.11.01", "SPM-RH-01", "1000-A", "SPM", "SPM_RH", FIELDS_RH)
    _model(db, "2026.10.01", "SPM-RH-01", "1000-A", "SPM", "SPM_RH", FIELDS_RH)
    _model(db, "2026.11.01", "WA-RF-01", "1000-A", "WA", "WA_RF", FIELDS_ONE)
    _model(db, "2026.11.01", "SD-SV-01", "2000-B", "SD", "SD_SV", FIELDS_ONE)

    data = env["client"].get("/api/v1/metadata/menu").json()
    assert data["success"] is True
    assert data["count"] == 3
    assert [i["key"] for i in data["items"]] == ["tree", "constants", "processes"]
    counts = {i["key"]: i["count"] for i in data["items"]}
    assert counts == {"tree": 2, "constants": 3, "processes": 3}
    for item in data["items"]:
        assert item["path"].startswith("/metadata/")
        assert item["label"]


def test_metadata_menu_defaults_to_zero_without_files(env):
    data = env["client"].get("/api/v1/metadata/menu").json()
    assert data["success"] is True
    counts = {i["key"]: i["count"] for i in data["items"]}
    assert counts == {"tree": 0, "constants": 0, "processes": 0}


# ---------------------------------------------------------------- SR01-AR06 TPS 关联


def _bindings_for_repeat():
    return [
        {"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "读头1", "source": "default"},
        {"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "读头2", "source": "sn"},
    ]


def test_tps_bindings_allow_same_model_twice(env):
    _base_models(env)
    path = _tps(env["base"])
    client = env["client"]

    saved = client.put(
        "/api/v1/tps/demo-tps/constants", json={"bindings": _bindings_for_repeat()}
    ).json()
    assert saved["success"] is True, saved
    assert saved["count"] == 2
    assert "warning" in saved  # 审批模块可用时为 None，不可用时为降级说明

    data = client.get("/api/v1/tps/demo-tps/constants").json()
    assert data["success"] is True and data["count"] == 2
    assert [(b["model_key"], b["alias"], b["source"]) for b in data["bindings"]] == [
        ("SPM_RH", "读头1", "default"),
        ("SPM_RH", "读头2", "sn"),
    ]

    payload = _tps_payload(path)
    assert len(payload["constant_bindings"]) == 2
    assert payload["constant_bindings"][0]["models"] == ["SPM_RH"]  # tps_store 摘要仍能计数
    assert payload["constant_model"]["pn"] == "SPM-RH-01"  # 旧字段同步

    # 清空
    cleared = client.put("/api/v1/tps/demo-tps/constants", json={"bindings": []}).json()
    assert cleared["success"] is True and cleared["count"] == 0
    payload = _tps_payload(path)
    assert "constant_bindings" not in payload and "constant_model" not in payload


def test_tps_bindings_read_back_via_tps_store(env, monkeypatch):
    """TPS_ROOT 与 base_dir/tps 一致时走 tps_store.load()，写回仍是同一份清单。"""
    import tps_store

    _base_models(env)
    path = _tps(env["base"])
    monkeypatch.setattr(tps_store, "TPS_ROOT", env["base"] / "tps", raising=False)
    client = env["client"]

    saved = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "读头"}]},
    ).json()
    assert saved["success"] is True, saved

    data = client.get("/api/v1/tps/demo-tps/constants").json()
    assert data["success"] is True and data["count"] == 1
    assert data["bindings"][0]["alias"] == "读头"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["constant_bindings"][0]["alias"] == "读头"


def test_tps_binding_alias_must_be_unique(env):
    _base_models(env)
    _tps(env["base"])
    client = env["client"]
    body = {
        "bindings": [
            {"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "同一别名", "source": "default"},
            {"pn": "SPM-RH-01", "model_key": "SPM_TEMP", "alias": "同一别名", "source": "default"},
        ]
    }
    result = client.put("/api/v1/tps/demo-tps/constants", json=body).json()
    assert result["success"] is False
    assert "别名重复" in result["message"]
    assert client.get("/api/v1/tps/demo-tps/constants").json()["count"] == 0

    empty_alias = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "  "}]},
    ).json()
    assert empty_alias["success"] is False and "alias" in empty_alias["message"]


def test_tps_binding_source_version_requires_version(env):
    _base_models(env)
    _tps(env["base"])
    client = env["client"]

    rejected = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={
            "bindings": [
                {"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "旧版", "source": "version"}
            ]
        },
    ).json()
    assert rejected["success"] is False
    assert "source=version" in rejected["message"] and "version" in rejected["message"]

    ok = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={
            "bindings": [
                {
                    "pn": "SPM-RH-01",
                    "model_key": "SPM_RH",
                    "alias": "旧版",
                    "source": "version",
                    "version": "2026.10.01",
                }
            ]
        },
    ).json()
    assert ok["success"] is True, ok
    assert ok["bindings"][0]["version"] == "2026.10.01"

    bad_source = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "x", "source": "mes"}]},
    ).json()
    assert bad_source["success"] is False and "source 只能是" in bad_source["message"]


def test_tps_binding_rejects_unknown_model_and_missing_tps(env):
    _base_models(env)
    _tps(env["base"])
    client = env["client"]

    unknown = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "NOT_EXIST", "alias": "x"}]},
    ).json()
    assert unknown["success"] is False and "常数模型不存在" in unknown["message"]

    no_tps = client.get("/api/v1/tps/ghost-tps/constants").json()
    assert no_tps["success"] is False and "TPS 不存在" in no_tps["message"]

    no_tps_put = client.put(
        "/api/v1/tps/ghost-tps/constants", json={"bindings": []}
    ).json()
    assert no_tps_put["success"] is False and "TPS 不存在" in no_tps_put["message"]

    bad_body = client.put("/api/v1/tps/demo-tps/constants", json={}).json()
    assert bad_body["success"] is False and "bindings" in bad_body["message"]


def test_tps_binding_degrades_when_approval_module_missing(env, monkeypatch):
    _base_models(env)
    _tps(env["base"])
    monkeypatch.setitem(sys.modules, "const_approval", None)  # 模拟模块未实现

    result = env["client"].put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "读头"}]},
    ).json()
    assert result["success"] is True
    assert "跳过待审批校验" in result["warning"]


def test_tps_binding_blocked_by_pending_approval(env, monkeypatch):
    _base_models(env)
    _tps(env["base"])

    fake = types.ModuleType("const_approval")
    fake.model_visible = lambda pn, model_key, db_path=None: model_key == "SPM_RH"
    monkeypatch.setitem(sys.modules, "const_approval", fake)
    client = env["client"]

    blocked = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_TEMP", "alias": "温度"}]},
    ).json()
    assert blocked["success"] is False
    assert "待审批" in blocked["message"] and "SPM_TEMP" in blocked["message"]

    allowed = client.put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_RH", "alias": "读头"}]},
    ).json()
    assert allowed["success"] is True
    assert allowed["warning"] is None


def test_tps_binding_falls_back_to_visible_models(env, monkeypatch):
    """审批模块只有 visible_models 时也能拦住待审批模型（返回 {models:[...]}）。"""
    _base_models(env)
    _tps(env["base"])

    fake = types.ModuleType("const_approval")
    fake.visible_models = lambda pn="", db_path=None: {
        "success": True,
        "models": [{"pn": "SPM-RH-01", "model_key": "SPM_RH"}],
    }
    monkeypatch.setitem(sys.modules, "const_approval", fake)

    blocked = env["client"].put(
        "/api/v1/tps/demo-tps/constants",
        json={"bindings": [{"pn": "SPM-RH-01", "model_key": "SPM_TEMP", "alias": "温度"}]},
    ).json()
    assert blocked["success"] is False
    assert "待审批" in blocked["message"] and "SPM_TEMP" in blocked["message"]


# ---------------------------------------------------------------- SR02-AR09 字段映射

MAPPINGS = [
    {
        "source_field": "temp_raw",
        "target_field": "temperature",
        "scale": 2,
        "offset": 1,
        "unit": "℃",
        "required": True,
    },
    {
        "source_field": "volt_raw",
        "target_field": "voltage",
        "scale": 0.5,
        "offset": -1,
        "unit": "V",
        "required": False,
    },
]


def test_field_mapping_full_replace(env):
    client = env["client"]
    first = client.put(
        "/api/v1/constants/field-mapping",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "product": "1000-A",
            "subsystem": "SPM",
            "mappings": MAPPINGS,
        },
    ).json()
    assert first["success"] is True, first
    assert first["count"] == 2

    loaded = client.get(
        "/api/v1/constants/field-mapping", params={"pn": "spm-rh-01", "model_key": "SPM_RH"}
    ).json()
    assert loaded["success"] is True
    assert loaded["product"] == "1000-A" and loaded["subsystem"] == "SPM"
    assert [m["source_field"] for m in loaded["mappings"]] == ["temp_raw", "volt_raw"]
    assert loaded["mappings"][0]["scale"] == 2.0 and loaded["mappings"][0]["required"] is True
    assert loaded["mappings"][1]["required"] is False

    # 全量替换：只剩一条，旧行不残留
    second = client.put(
        "/api/v1/constants/field-mapping",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "mappings": [
                {"source_field": "gain_raw", "target_field": "gain_offset", "scale": 0.1, "offset": 0}
            ],
        },
    ).json()
    assert second["success"] is True and second["count"] == 1
    replaced = client.get(
        "/api/v1/constants/field-mapping", params={"pn": "SPM-RH-01", "model_key": "SPM_RH"}
    ).json()
    assert replaced["count"] == 1
    assert replaced["mappings"][0]["source_field"] == "gain_raw"
    assert replaced["mappings"][0]["target_field"] == "gain_offset"

    duplicate = client.put(
        "/api/v1/constants/field-mapping",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "mappings": [
                {"source_field": "a", "target_field": "x"},
                {"source_field": "a", "target_field": "y"},
            ],
        },
    ).json()
    assert duplicate["success"] is False and "重复映射" in duplicate["message"]

    missing_pn = client.get("/api/v1/constants/field-mapping", params={"pn": "SPM-RH-01"}).json()
    assert missing_pn["success"] is False and "model_key" in missing_pn["message"]


def test_field_mapping_apply_conversion(env):
    client = env["client"]
    client.put(
        "/api/v1/constants/field-mapping",
        json={"pn": "SPM-RH-01", "model_key": "SPM_RH", "mappings": MAPPINGS},
    )

    result = client.post(
        "/api/v1/constants/field-mapping/apply",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "rows": [
                {"source_field": "temp_raw", "value": 3.5},   # 3.5 * 2 + 1 = 8.0
                {"source_field": "volt_raw", "value": 10},    # 10 * 0.5 - 1 = 4.0
            ],
        },
    ).json()
    assert result["success"] is True, result
    assert result["values"] == {"temperature": 8.0, "voltage": 4.0}
    assert result["temperature"] == 8.0  # 契约的扁平读法
    assert result["count"] == 2


def test_field_mapping_apply_missing_required(env):
    client = env["client"]
    client.put(
        "/api/v1/constants/field-mapping",
        json={"pn": "SPM-RH-01", "model_key": "SPM_RH", "mappings": MAPPINGS},
    )

    missing = client.post(
        "/api/v1/constants/field-mapping/apply",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "rows": [{"source_field": "volt_raw", "value": 10}],
        },
    ).json()
    assert missing["success"] is False
    assert "缺少必填字段" in missing["message"] and "temp_raw" in missing["message"]

    # 非必填缺省不影响换算
    ok = client.post(
        "/api/v1/constants/field-mapping/apply",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "rows": [{"source_field": "temp_raw", "value": 0}],
        },
    ).json()
    assert ok["success"] is True
    assert ok["values"] == {"temperature": 1.0}


def test_field_mapping_apply_reports_unmapped_fields(env):
    client = env["client"]
    client.put(
        "/api/v1/constants/field-mapping",
        json={"pn": "SPM-RH-01", "model_key": "SPM_RH", "mappings": MAPPINGS},
    )
    result = client.post(
        "/api/v1/constants/field-mapping/apply",
        json={
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "rows": [{"source_field": "ghost_field", "value": 1}],
        },
    ).json()
    assert result["success"] is False
    assert "未配置映射" in result["message"] and "ghost_field" in result["message"]

    no_mapping = client.post(
        "/api/v1/constants/field-mapping/apply",
        json={"pn": "SPM-RH-01", "model_key": "SPM_TEMP", "rows": [{"source_field": "a", "value": 1}]},
    ).json()
    assert no_mapping["success"] is False and "尚未配置字段映射" in no_mapping["message"]
