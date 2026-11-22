"""MockController 端到端冒烟测试（起真实服务 + 真实数据目录）

验证目标：
  1. 装备树三级结构可用（产品 → 子系统 → 测试台类型）；
  2. TPS 可按三级归组管理，并能在子系统/测试台类型上创建（scaffold）；
  3. TPS 机器常数支持多行绑定；
  4. 常数按子系统以 zip / 单文件 / bundle 导入；
  5. 用例代码文件可在线读写；
  6. 用例步骤支持 auto / manual，manual 的 SOP 可写文件并回填 resource。

运行：
    python -m pytest tests -q
    （需要 D:\\ATE\\MockRunner\\backend\\.venv 里有 pytest / httpx）
"""

from __future__ import annotations

import base64
import io
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest


def pytest_configure(config):
    """把临时目录固定到工程内，避免系统 Temp 目录权限问题（本机 Temp 对当前用户不可写）。"""
    root = ROOT / ".pytest_tmp"
    root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(root)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "samples"
PY = sys.executable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """在临时数据目录上起一个 Configurator 实例，测试结束自动清理。"""
    repo = tmp_path_factory.mktemp("repo")
    port = _free_port()
    env = dict(os.environ)
    env["MOCK_CONTROLLER_REPO"] = str(repo)
    env["ATE_CONTROLLER_REPO"] = str(repo)
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/v1/health", timeout=1).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail("服务未能在 30 秒内启动")
    yield {"base": base, "repo": repo}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def cli(server):
    with httpx.Client(base_url=server["base"], timeout=20) as client:
        yield client


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _zip_of(paths: list[Path]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in paths:
            zf.write(path, arcname=path.name)
    return base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ 1. 装备树

def test_metadata_tree_thirds(cli):
    data = cli.get("/api/metadata/tree").json()
    assert data["success"] is True
    tree = data["tree"]
    assert tree, "装备树为空"
    product = tree[0]
    assert product.get("product")
    subsystem = product["subsystems"][0]
    assert subsystem.get("subsystem")
    assert isinstance(subsystem.get("preset_types"), list)
    # 每个测试台类型都能落到 product / subsystem 上
    preset = subsystem["preset_types"][0]
    assert preset.get("id")
    assert "bom_count" in preset


# ------------------------------------------------------------------ 2. TPS 三级管理

@pytest.fixture()
def preset_id(cli) -> str:
    data = cli.get("/api/metadata/tree").json()
    for product in data["tree"]:
        for subsystem in product["subsystems"]:
            if subsystem.get("preset_types"):
                return subsystem["preset_types"][0]["id"]
    pytest.skip("装备树里没有测试台类型")


def test_tps_list_exposes_grouping_fields(cli, preset_id):
    created = cli.post(
        "/api/v1/tps/scaffold",
        json={"preset_id": preset_id, "save": True},
    )
    assert created.status_code == 200, created.text
    tps = created.json()["tps"]
    assert tps["preset_id"] == preset_id
    assert tps.get("product") and tps.get("subsystem")

    listed = cli.get("/api/v1/tps").json()["tps"]
    row = next(t for t in listed if t["id"] == tps["id"])
    # 列表必须带三级归组所需字段，前端才能做 产品/子系统/类型/TPS 树
    for field in ("preset_id", "product", "subsystem", "parser", "device_count"):
        assert field in row
    assert row["preset_id"] == preset_id
    cli.put(f"/api/v1/tps/{tps['id']}/files/README.md", json={"content": "tmp", "encoding": "utf-8"})


def test_scaffold_from_subsystem_and_preset(cli, preset_id):
    resp = cli.post(
        "/api/v1/tps/scaffold",
        json={
            "preset_id": preset_id,
            "process_id": "PRC-FCT-04",
            "parser": "tps.v2",
            "save": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tps"]["parser"]["name"] == "tps.v2"
    assert body["tps"]["process"]["id"] == "PRC-FCT-04"
    assert body["files"], "脚手架应返回起始用例文件"
    # 代码文件可读写（在线 IDE 的基础能力）
    code = "# -*- coding: utf-8 -*-\ndef main(ctx, **params):\n    return {}\n"
    path = body["files"][0]["path"]
    put = cli.put(f"/api/v1/tps/{body['tps']['id']}/files/{path}", json={"content": code, "encoding": "utf-8"})
    assert put.status_code == 200, put.text
    got = cli.get(f"/api/v1/tps/{body['tps']['id']}/files/{path}")
    assert got.text == code


# ------------------------------------------------------------------ 3. 常数多行

def test_constant_bindings_multi_rows(cli, preset_id):
    created = cli.post("/api/v1/tps/scaffold", json={"preset_id": preset_id, "save": True}).json()["tps"]
    tps_id = created["id"]

    detail = cli.get(f"/api/v1/tps/{tps_id}").json()["tps"]
    detail["constant_bindings"] = [
        {"version": "V-DEMO-1", "pn": "PN-DEMO-1", "models": []},
        {"version": "V-DEMO-1", "pn": "PN-DEMO-1", "models": []},
        {"version": "VER-TEST-B1", "pn": "PN-TEST-B1A", "models": []},
    ]
    saved = cli.put(f"/api/v1/tps/{tps_id}", json={"tps": detail, "overwrite": True})
    assert saved.status_code == 200, saved.text

    back = cli.get(f"/api/v1/tps/{tps_id}").json()["tps"]
    assert len(back["constant_bindings"]) == 3
    assert back["constant_bindings"][2]["pn"] == "PN-TEST-B1A"

    row = next(t for t in cli.get("/api/v1/tps").json()["tps"] if t["id"] == tps_id)
    assert row.get("constant_binding_count") == 3


# ------------------------------------------------------------------ 4. 常数按子系统导入

def test_constants_import_zip_by_subsystem(cli):
    payload = {
        "product": "1000-A",
        "subsystem": "SPM",
        "mode": "overwrite",
        "filename": "spm-models.zip",
        "content_base64": _zip_of([SAMPLES / "spm_model_1.json", SAMPLES / "spm_model_2.json"]),
    }
    resp = cli.post("/api/v1/constants/import-zip", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["stats"]["model_count"] >= 2
    files = body.get("zip_files") or []
    assert files and all(item.get("ok") for item in files)


def test_constants_import_single_file(cli):
    payload = {
        "filename": "spm_model_3_single.json",
        "content_base64": _b64(SAMPLES / "spm_model_3_single.json"),
        "mode": "overwrite",
    }
    resp = cli.post("/api/v1/constants/import-file", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_constants_import_bundle_rejects_bad_file(cli):
    bad = {"filename": "bad.json", "content_base64": base64.b64encode(b"{not json").decode()}
    good = {"filename": "spm_model_1.json", "content_base64": _b64(SAMPLES / "spm_model_1.json")}
    resp = cli.post(
        "/api/v1/constants/import-bundle",
        json={"product": "1000-A", "subsystem": "SPM", "mode": "overwrite", "files": [good, bad]},
    )
    assert resp.status_code == 400
    assert "不是有效 JSON" in resp.text


def test_constants_subsystems_list(cli):
    data = cli.get("/api/v1/constants/subsystems").json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)


# ------------------------------------------------------------------ 5. 用例步骤（manual + SOP）

def test_tps_steps_manual_sop(cli):
    manifest = {
        "schema": "tps.v2",
        "id": "steps-manual",
        "name": "步骤编辑",
        "testconfig": {"TC_A": {"min": 0, "max": 1}},
        "device_config": {"psu": {"role": "psu"}},
        "cmd_suit": [
            {"id": "C1", "name": "自动条目", "case": "testcase.demo::main", "checks": {"a": "TC_A"}},
            {"id": "M1", "name": "人工条目", "type": "manual", "sop": "# 检查外观", "timeout_s": 300},
        ],
    }

    def put(payload):
        return cli.put("/api/v1/tps/steps-manual", json={"tps": payload, "overwrite": True})

    created = put(manifest)
    assert created.status_code == 200, created.text

    back = cli.get("/api/v1/tps/steps-manual").json()["tps"]
    auto = next(e for e in back["cmd_suit"] if e["id"] == "C1")
    manual = next(e for e in back["cmd_suit"] if e["id"] == "M1")
    # 没有 type 的旧条目按 auto 处理
    assert "type" not in auto and auto["case"] == "testcase.demo::main"
    # 内联 sop 落成 resource 文件，Runner 弹窗才有内容可读
    assert manual["type"] == "manual" and manual["sop"].startswith("# 检查外观")
    assert manual["resource"] == "resource/M1.md"
    assert cli.get("/api/v1/tps/steps-manual/files/resource/M1.md").text == "# 检查外观"

    # manual 与 case 互斥
    conflict = put({**manifest, "cmd_suit": manifest["cmd_suit"] + [
        {"id": "M2", "name": "冲突", "type": "manual", "sop": "x", "case": "testcase.demo::main"},
    ]})
    assert conflict.status_code == 400 and "互斥" in conflict.text

    # manual 必须有 resource 或 sop
    empty = put({**manifest, "cmd_suit": manifest["cmd_suit"] + [
        {"id": "M3", "name": "空条目", "type": "manual"},
    ]})
    assert empty.status_code == 400 and "需要 resource" in empty.text

    # type 只允许 auto / manual
    wrong = put({**manifest, "cmd_suit": [{**manifest["cmd_suit"][0], "type": "robot"}]})
    assert wrong.status_code == 400 and "只能是 auto 或 manual" in wrong.text

    # POST /sop：写文件 + 回填 resource
    markdown = "# 外观 SOP\n\n1. 外壳无划伤\n"
    sop = cli.post(
        "/api/v1/tps/steps-manual/sop",
        json={"step_id": "M1", "title": "人工条目", "markdown": markdown, "filename": ""},
    )
    assert sop.status_code == 200, sop.text
    assert sop.json()["resource"] == "resource/M1.md"
    assert cli.get("/api/v1/tps/steps-manual/files/resource/M1.md").text == markdown
    bad_name = cli.post(
        "/api/v1/tps/steps-manual/sop",
        json={"step_id": "M1", "markdown": "x", "filename": "a.py"},
    )
    assert bad_name.status_code == 400

    # context.steps：三段结构 + 字段齐全
    steps = cli.get("/api/v1/tps/steps-manual/context").json()["steps"]
    assert set(steps) == {"setup", "cmd_suit", "teardown"}
    assert steps["setup"] == [] and steps["teardown"] == []
    assert [e["id"] for e in steps["cmd_suit"]] == ["C1", "M1"]
    for key in ("id", "name", "type", "case", "resource", "sop", "timeout_s", "step", "checks"):
        assert key in steps["cmd_suit"][1], key
    assert steps["cmd_suit"][1]["type"] == "manual" and steps["cmd_suit"][1]["case"] == ""
