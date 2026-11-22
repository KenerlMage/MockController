"""装备软件归档与 TPS deploy 编排（SR01-AR01 ~ AR08）。"""
import base64
import io
import json
import sqlite3
import zipfile

import pytest

import ate_db
import software
import tps_store


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _pkg(files: dict[str, bytes]) -> str:
    return base64.b64encode(_zip(files)).decode()


DRIVER_FILES = {
    "manifest.json": json.dumps({"scripts": {"probe": "scripts/probe.bat", "install": "scripts/install.bat"}}).encode(),
    "scripts/probe.bat": b"@echo off\r\nexit /b 10\r\n",
    "scripts/install.bat": b"@echo off\r\nexit /b 0\r\n",
}

ASSISTANT_FILES = {
    "manifest.json": json.dumps({"scripts": {"probe": "scripts/probe.ps1", "install": "scripts/install.ps1",
                                             "uninstall": "scripts/uninstall.ps1"}}).encode(),
    "scripts/probe.ps1": b"exit 10\n",
    "scripts/install.ps1": b"exit 0\n",
    "scripts/uninstall.ps1": b"exit 0\n",
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "tps").mkdir(parents=True)
    db = tmp_path / "controller.db"
    ate_db.init_db(db)
    monkeypatch.setattr(tps_store, "TPS_ROOT", root / "tps")
    return {"root": root, "db": db}


def _archive(env, kind="driver", name="spm_driver", version="1.0.0", files=None, **extra):
    payload = {
        "kind": kind, "product": "1000-A", "subsystem": "SPM",
        "name": name, "version": version,
        "content_base64": _pkg(files if files is not None else (
            DRIVER_FILES if kind == "driver" else ASSISTANT_FILES)),
        **extra,
    }
    return software.archive_software(payload, base_dir=env["root"], db_path=env["db"])


def _tps(env, tps_id="t1", product="1000-A", subsystem="SPM"):
    raw = {
        "schema": "tps.v2", "id": tps_id, "name": tps_id, "version": "1.0.0",
        "product": product, "subsystem": subsystem,
        "testconfig": {"T": {"min": 0, "max": 1, "unit": "", "label": "T"}},
        "device_config": {"psu": {"role": "psu"}, "dmm": {"role": "dmm"}},
        "setup": [],
        "cmd_suit": [{"id": "C1", "no": 1, "name": "用例", "case": "testcase.basic::run",
                      "description": "", "checks": {}}],
        "teardown": [],
    }
    tps_store.save(raw, overwrite=True)
    return raw


def test_driver_requires_probe_and_install(env):
    files = {"manifest.json": json.dumps({"scripts": {"probe": "scripts/probe.bat"}}).encode(),
             "scripts/probe.bat": b"exit 10\n"}
    result = _archive(env, files=files)
    assert result["success"] is False
    assert "install" in result["message"]


def test_driver_archive_and_list(env):
    result = _archive(env)
    assert result["success"], result
    listing = software.list_software(db_path=env["db"])
    assert listing["count"] == 1
    item = listing["items"][0]
    assert item["kind"] == "driver" and item["package"] == "spm_driver@1.0.0"
    assert item["scripts"]["install"] == "scripts/install.bat"
    assert (env["root"] / "software" / "1000-A" / "SPM" / "driver" / "spm_driver" / "1.0.0" / "package.zip").is_file()


def test_assistant_needs_deploy_default_true_and_three_scripts(env):
    ok = _archive(env, kind="assistant", name="scope_assist")
    assert ok["success"], ok
    assert ok["needs_deploy"] is True
    bad = _archive(env, kind="assistant", name="bad_assist",
                   files={k: v for k, v in ASSISTANT_FILES.items() if "uninstall" not in k})
    assert bad["success"] is False and "uninstall" in bad["message"]


def test_tps_kind_rejected(env):
    result = _archive(env, kind="tps", name="x")
    assert result["success"] is False
    assert "TPS" in result["message"]


def test_verify_matches_server_rules(env):
    good = software.verify_archive({"kind": "driver", "product": "1000-A", "subsystem": "SPM",
                                    "name": "d", "version": "1", "content_base64": _pkg(DRIVER_FILES)})
    assert good["success"] is True and good["scripts"]["probe"]
    bad = software.verify_archive({"kind": "driver", "product": "1000-A", "subsystem": "SPM",
                                   "name": "d", "version": "1", "content_base64": _pkg({"a.txt": b"x"})})
    assert bad["success"] is False


def test_set_deploy_valid_and_order(env):
    assert _archive(env)["success"]
    _tps(env)
    result = software.set_tps_deploy("t1", [
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": "local"}, "order": 1},
        {"key": "drv_dmm", "package": "spm_driver@1.0.0", "target": {"id": "dmm"}, "order": 2,
         "depends_on": ["drv"]},
    ], db_path=env["db"])
    assert result["success"], result
    saved = software.get_tps_deploy("t1")
    assert [d["key"] for d in saved["deploy"]] == ["drv", "drv_dmm"]
    assert saved["deploy"][1]["target"]["id"] == "dmm"
    assert saved["deploy"][0]["order"] == 1


def test_set_deploy_rejects_unknown_package(env):
    _tps(env)
    result = software.set_tps_deploy("t1", [
        {"key": "drv", "package": "not_there@9.9", "target": {"id": "local"}}], db_path=env["db"])
    assert result["success"] is False
    assert "未归档" in result["message"]


def test_set_deploy_rejects_cross_subsystem(env):
    assert _archive(env)["success"]
    _tps(env, subsystem="WA")
    dev = software.get_tps_deploy("t1")["device_aliases"]
    result = software.set_tps_deploy("t1", [
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": dev[0]}}], db_path=env["db"])
    assert result["success"] is False
    assert "不属于" in result["message"]


def test_set_deploy_rejects_bad_target_and_duplicate_key(env):
    assert _archive(env)["success"]
    _tps(env)
    bad_target = software.set_tps_deploy("t1", [
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": "ghost"}}], db_path=env["db"])
    assert bad_target["success"] is False and "设备清单" in bad_target["message"]

    dup = software.set_tps_deploy("t1", [
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": "local"}},
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": "local"}}], db_path=env["db"])
    assert dup["success"] is False and "重复" in dup["message"]


def test_refs_and_delete_guard(env):
    assert _archive(env)["success"]
    _tps(env)
    assert software.set_tps_deploy("t1", [
        {"key": "drv", "package": "spm_driver@1.0.0", "target": {"id": "local"}}], db_path=env["db"])["success"]

    sid = "1000-A/SPM/driver/spm_driver/1.0.0"
    refs = software.references(sid, db_path=env["db"])
    assert refs["count"] == 1 and refs["items"][0]["tps_id"] == "t1"

    blocked = software.delete_software(sid, db_path=env["db"], base_dir=env["root"])
    assert blocked["success"] is False and "引用" in blocked["message"]
