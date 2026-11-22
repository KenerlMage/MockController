"""外部包契约与 SN 实例归档的验收用例（SR01-AR09 / AR10）。"""
import json
import sqlite3

import pytest

import ate_db
import const_import
import const_package


def _field(key="gain_offset", **kw):
    attrs = f'key="{key}" label="{kw.get("label", key)}" type="{kw.get("type", "float")}"'
    attrs += f' unit="{kw.get("unit", "mV")}" default="{kw.get("default", "0.0")}"'
    attrs += f' min="{kw.get("min", "-50")}" max="{kw.get("max", "50")}"'
    if kw.get("value") is not None:
        attrs += f' value="{kw["value"]}"'
    return f"<Field {attrs} />"


def _xml(module, pn, fields, *, calibrated=False, sn="", version="V1", subsystem="SPM"):
    head = (f'<MachineConstants schema="ate.constants.v2" module="{module}" pn="{pn}" '
            f'version="{version}" calibrated="{"true" if calibrated else "false"}"')
    if sn:
        head += f' sn="{sn}"'
    head += ">"
    meta = f'<Meta product="1000-A" subsystem="{subsystem}" name="{module}" />'
    return (head + meta + "<Fields>" + "".join(fields) + "</Fields></MachineConstants>").encode("utf-8")


def _digest(entries):
    import hashlib
    lines = sorted(f"{e['path']}:{e['sha256']}" for e in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _package(files, *, version="V1", batch="MCS-B1", source="MCS", signature=None):
    import hashlib
    import io
    import zipfile

    entries = [{"path": p, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)} for p, d in files.items()]
    manifest = {
        "contract": "mcs.constants.export/v1",
        "source": {"system": source, "system_version": "3.2.1", "exported_at": "2026-11-08T10:00:00+08:00"},
        "batch_id": batch,
        "version": version,
        "content_digest": _digest(entries),
        "files": entries,
    }
    if signature:
        manifest["signature"] = signature
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for p, d in files.items():
            zf.writestr(p, d)
    return buf.getvalue()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "c.db"
    ate_db.init_db(path)
    return path


def _rows(db, sql, args=()):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def test_template_and_instance_are_routed_separately(db):
    """模板进 constant_model，实例进 const_calibration，互不覆盖。"""
    files = {
        "SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()]),
        "SPM/SN20260101_SPM_RH.xml": _xml(
            "SPM_RH", "SPM-RH-01", [_field(value="12.5")], calibrated=True, sn="SN20260101"),
    }
    result = const_import.import_full(_package(files), "mcs.zip", "alice", db_path=db)
    assert result["success"], result
    assert result["instance_count"] == 1

    models = _rows(db, "SELECT model_key, fields_json FROM constant_model")
    assert [m["model_key"] for m in models] == ["SPM_RH"]
    # 模板值保持默认，未被实例的 12.5 覆盖
    fields = json.loads(models[0]["fields_json"])
    assert float(fields[0].get("default")) == 0.0
    assert not fields[0].get("value")

    calib = _rows(db, "SELECT sn, model_key, values_json FROM const_calibration")
    assert [(c["sn"], c["model_key"]) for c in calib] == [("SN20260101", "SPM_RH")]
    assert json.loads(calib[0]["values_json"])["gain_offset"] == "12.5"


def test_instance_filename_must_match_calibrated_flag(db):
    """文件名带 SN 但 calibrated=false：拒绝并指出文件名。"""
    files = {"SPM/SN9_SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()])}
    result = const_import.import_full(_package(files), "bad.zip", "alice", db_path=db)
    assert result["success"] is False
    assert "SN9_SPM_RH.xml" in result["message"]


def test_instance_sn_must_match_filename(db):
    """XML 的 sn 与文件名前缀不一致：拒绝。"""
    files = {
        "SPM/SN9_SPM_RH.xml": _xml(
            "SPM_RH", "SPM-RH-01", [_field(value="1")], calibrated=True, sn="SN8"),
    }
    result = const_import.import_full(_package(files), "mismatch.zip", "alice", db_path=db)
    assert result["success"] is False
    assert "不一致" in result["message"]


def test_instance_only_package_is_accepted(db):
    """只有实例、没有模板的包也应能导入（MCS 单独补标定数据）。"""
    files = {
        "SPM/SN20260102_SPM_RH.xml": _xml(
            "SPM_RH", "SPM-RH-01", [_field(value="3.3")], calibrated=True, sn="SN20260102"),
    }
    result = const_import.import_full(_package(files), "inst-only.zip", "alice", db_path=db)
    assert result["success"], result
    assert result["instance_count"] == 1
    assert _rows(db, "SELECT COUNT(*) AS n FROM const_calibration")[0]["n"] == 1


def test_full_import_keeps_instances_by_default_and_purges_on_demand(db):
    first = {
        "SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()]),
        "SPM/SN1_SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field(value="1")], calibrated=True, sn="SN1"),
    }
    assert const_import.import_full(_package(first, version="V1"), "a.zip", "alice", db_path=db)["success"]

    second = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()], version="V2")}
    kept = const_import.import_full(_package(second, version="V2", batch="MCS-B2"), "b.zip", "alice", db_path=db)
    assert kept["success"], kept
    assert _rows(db, "SELECT COUNT(*) AS n FROM const_calibration")[0]["n"] == 1

    purged = const_import.import_full(
        _package(second, version="V2", batch="MCS-B3"), "c.zip", "alice",
        db_path=db, purge_calibrations=True)
    assert purged["success"], purged
    assert _rows(db, "SELECT COUNT(*) AS n FROM const_calibration")[0]["n"] == 0


def test_partial_import_subset_only_touches_selected_models(db):
    base = {
        "SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()]),
        "SPM/SPM_TP.xml": _xml("SPM_TP", "SPM-RH-01", [_field("threshold", type="int", default="3", min="0", max="10")]),
    }
    assert const_import.import_full(_package(base), "base.zip", "alice", db_path=db)["success"]

    changed = {
        "SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field(label="增益偏移-改")]),
        "SPM/SPM_TP.xml": _xml("SPM_TP", "SPM-RH-01", [_field("threshold", type="int", default="7", min="0", max="10")]),
    }
    result = const_import.import_partial(
        _package(changed, batch="MCS-B9"), "subset.zip", "alice", db_path=db, models=["SPM_RH"])
    assert result["success"], result
    assert result["replaced"] == 1

    rows = {r["model_key"]: r for r in _rows(db, "SELECT model_key, fields_json FROM constant_model")}
    assert "改" in rows["SPM_RH"]["fields_json"]
    assert '"7"' not in rows["SPM_TP"]["fields_json"]  # 未选中，不动


def test_verify_endpoint_reports_classification_without_writing(db):
    files = {
        "SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()]),
        "SPM/SN7_SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field(value="2")], calibrated=True, sn="SN7"),
    }
    report = const_import.verify_package(_package(files), "look.zip")
    assert report["success"] is True
    assert report["source_system"] == "MCS"
    assert report["batch_id"] == "MCS-B1"
    assert len(report["templates"]) == 1 and len(report["instances"]) == 1
    assert report["signature_status"] == "absent"
    assert _rows(db, "SELECT COUNT(*) AS n FROM constant_model")[0]["n"] == 0


def test_package_batch_is_recorded(db):
    files = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()])}
    result = const_import.import_full(_package(files, batch="MCS-20261108-7"), "z.zip", "alice", db_path=db)
    assert result["success"], result
    rows = _rows(db, "SELECT batch_id, source_system, template_count FROM const_package")
    assert rows and rows[0]["batch_id"] == "MCS-20261108-7"
    assert rows[0]["source_system"] == "MCS"


def test_signature_verified_when_key_configured(db, monkeypatch):
    import hashlib
    import hmac

    files = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()])}
    entries_digest = None
    # 先算摘要，再签
    import hashlib as h
    import io
    import zipfile

    entries = []
    for path, data in files.items():
        entries.append({"path": path, "sha256": h.sha256(data).hexdigest(), "size": len(data)})
    entries_digest = _digest(entries)
    key = "test-key"
    value = hmac.new(key.encode(), entries_digest.encode(), hashlib.sha256).hexdigest()

    monkeypatch.setenv("ATE_CONSTANT_SIGNING_KEY", key)
    blob = _package(files, signature={"alg": "hmac-sha256", "value": value})
    result = const_import.import_full(blob, "signed.zip", "alice", db_path=db)
    assert result["success"], result
    assert result["signature_status"] == "verified"

    bad = _package(files, signature={"alg": "hmac-sha256", "value": "0" * 64})
    rejected = const_import.import_full(bad, "badsig.zip", "alice", db_path=db)
    assert rejected["success"] is False
    assert "签名" in rejected["message"]


def test_signature_present_without_key_is_unverified_not_blocked(db, monkeypatch):
    monkeypatch.delenv("ATE_CONSTANT_SIGNING_KEY", raising=False)
    files = {"SPM/SPM_RH.xml": _xml("SPM_RH", "SPM-RH-01", [_field()])}
    blob = _package(files, signature={"alg": "hmac-sha256", "value": "a" * 64})
    result = const_import.import_full(blob, "unsigned-key.zip", "alice", db_path=db)
    assert result["success"], result
    assert result["signature_status"] == "unverified"
