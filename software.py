# -*- coding: utf-8 -*-
"""装备软件归档与 TPS 部署编排（SR01-AR01 ~ AR08）。

归档维度：产品 + 子系统 + 类型 + 名称 + 版本（不绑测试台类型）。
类型：assistant（装备助手）/ driver（驱动软件）/ thirdparty（三方软件）。
TPS 本身仍由 tps_store 归档；本模块只提供清单视图与 deploy 校验。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import threading
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import ate_db
import tps_store

KINDS = ("assistant", "driver", "tps", "thirdparty")
KIND_LABELS = {"assistant": "装备助手", "driver": "驱动软件", "tps": "TPS", "thirdparty": "三方软件"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,47}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9._+-]{0,47}$")
MAX_BYTES = 512 * 1024 * 1024

_LOCK = threading.RLock()
_BASE: Optional[Path] = None
_DB: Optional[Path] = None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _base(base_dir: Optional[Path] = None) -> Path:
    return Path(base_dir) if base_dir else Path(_BASE or Path.cwd())


def _db_path(db_path: Optional[Path] = None) -> Path:
    return Path(db_path) if db_path else Path(_DB or ate_db.DB_PATH)


def _connect(db_path: Optional[Path] = None):
    path = _db_path(db_path)
    ate_db.init_db(path)
    return ate_db.connect(path)


def _fail(message: str, **extra) -> dict:
    return {"success": False, "message": message, **extra}


def _ok(message: str = "", **extra) -> dict:
    return {"success": True, "message": message or None, **extra}


# ---------------------------------------------------------------- 校验


def _kind(value: Any) -> str:
    kind = _text(value, 20).lower()
    if kind not in KINDS:
        raise ValueError(f"软件类型只能是 {' / '.join(KINDS)}")
    return kind


def _scope(product: Any, subsystem: Any) -> tuple[str, str]:
    prod = _text(product, 32)
    sub = _text(subsystem, 32)
    if not prod or not sub:
        raise ValueError("归档必须指定产品与子系统")
    return prod, sub


def _scripts(kind: str, raw: Any, names: set[str]) -> dict:
    """校验包内脚本：驱动需 probe+install，助手需 probe+install+uninstall。"""
    scripts = raw if isinstance(raw, dict) else {}
    need = {"driver": {"probe", "install"}, "assistant": {"probe", "install", "uninstall"}}.get(kind, set())
    out = {}
    for key in need:
        rel = _text(scripts.get(key), 200).replace("\\", "/").lstrip("./")
        if not rel:
            raise ValueError(f"{KIND_LABELS[kind]}必须提供 {key} 脚本（manifest.scripts.{key}）")
        if rel not in names:
            raise ValueError(f"脚本不存在于包内：{rel}（manifest.scripts.{key}）")
        out[key] = rel
    for key, rel in (scripts or {}).items():
        if key not in out and rel:
            out[key] = _text(rel, 200).replace("\\", "/").lstrip("./")
    return out


def archive_software(
    payload: dict,
    *,
    base_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    operator: str = "",
) -> dict:
    """归档一个软件包；zip 内必须带 manifest.json 或由入参给出元数据。"""
    try:
        kind = _kind(payload.get("kind"))
        if kind == "tps":
            raise ValueError("TPS 请通过 TPS 编辑器归档，不进入装备软件归档通道")
        product, subsystem = _scope(payload.get("product"), payload.get("subsystem"))
        name = _text(payload.get("name"), 64)
        version = _text(payload.get("version"), 48)
        if not NAME_RE.match(name):
            raise ValueError(f"软件名称不合法（字母/数字/._-，最长 64）：{name}")
        if not VERSION_RE.match(version):
            raise ValueError(f"版本号不合法（字母/数字/._+-，最长 48）：{version}")

        blob = _decode(payload.get("content_base64"))
        if len(blob) > MAX_BYTES:
            raise ValueError(f"安装包超过 {MAX_BYTES // (1024 * 1024)}MB 上限")
        try:
            zf = zipfile.ZipFile(BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"不是有效的 zip 包：{exc}") from exc
        with zf:
            names = {n.replace("\\", "/").lstrip("./") for n in zf.namelist() if not n.endswith("/")}
            # manifest 必须在 zip 关闭前读取，否则 zf.open 会抛 ValueError
            manifest = _read_manifest(zf, names) if "manifest.json" in names else {}

        scripts = _scripts(kind, manifest.get("scripts") or payload.get("scripts"), names)
        needs_deploy = payload.get("needs_deploy", manifest.get("needs_deploy"))
        needs_deploy = bool(needs_deploy) if needs_deploy is not None else (kind == "assistant")
        auto_deploy = bool(payload.get("auto_deploy", manifest.get("auto_deploy", False)))
        vendor = _text(payload.get("vendor") or manifest.get("vendor"), 100)
        license_type = _text(payload.get("license") or manifest.get("license"), 100)

        digest = hashlib.sha256(blob).hexdigest()
        root = _base(base_dir) / "software" / product / subsystem / kind / name / version
        root.mkdir(parents=True, exist_ok=True)
        package_path = root / "package.zip"
        package_path.write_bytes(blob)
        record = {
            "kind": kind,
            "product": product,
            "subsystem": subsystem,
            "name": name,
            "version": version,
            "path": str(package_path),
            "sha256": digest,
            "size": len(blob),
            "scripts_json": json.dumps(scripts, ensure_ascii=False),
            "needs_deploy": 1 if needs_deploy else 0,
            "auto_deploy": 1 if auto_deploy else 0,
            "vendor": vendor,
            "license": license_type,
            "uploaded_by": _text(operator, 64),
            "created_at": _now(),
        }
        (root / "manifest.json").write_text(
            json.dumps({**record, "scripts": scripts, "file_count": len(names)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with _LOCK, _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO software_archive (product, subsystem, kind, name, version, path, sha256, size,"
                " scripts_json, needs_deploy, auto_deploy, vendor, license, uploaded_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(product, subsystem, kind, name, version) DO UPDATE SET"
                " path=excluded.path, sha256=excluded.sha256, size=excluded.size,"
                " scripts_json=excluded.scripts_json, needs_deploy=excluded.needs_deploy,"
                " auto_deploy=excluded.auto_deploy, vendor=excluded.vendor, license=excluded.license,"
                " uploaded_by=excluded.uploaded_by, created_at=excluded.created_at",
                (product, subsystem, kind, name, version, str(package_path), digest, len(blob),
                 json.dumps(scripts, ensure_ascii=False), 1 if needs_deploy else 0,
                 1 if auto_deploy else 0, vendor, license_type, _text(operator, 64), record["created_at"]),
            )
        return _ok(
            f"已归档 {KIND_LABELS[kind]} {name} {version}",
            id=f"{product}/{subsystem}/{kind}/{name}/{version}",
            sha256=digest, size=len(blob), scripts=scripts,
            needs_deploy=needs_deploy, auto_deploy=auto_deploy,
        )
    except ValueError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"归档失败：{exc}")


def _decode(raw: Any) -> bytes:
    text = _text(raw, 10 * 1024 * 1024)
    if not text:
        raise ValueError("缺少 content_base64（安装包内容）")
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"安装包不是有效 base64：{exc}") from exc


def _read_manifest(zf: zipfile.ZipFile, names: set[str]) -> dict:
    try:
        with zf.open("manifest.json") as fh:
            return json.loads(fh.read(1024 * 1024).decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def list_software(kind: str = "", product: str = "", subsystem: str = "", db_path=None) -> dict:
    sql = "SELECT * FROM software_archive WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind = ?"
        args.append(_kind(kind))
    if product:
        sql += " AND UPPER(product) = ?"
        args.append(_text(product, 32).upper())
    if subsystem:
        sql += " AND UPPER(subsystem) = ?"
        args.append(_text(subsystem, 32).upper())
    sql += " ORDER BY product, subsystem, kind, name, version"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    items = [_row(r) for r in rows]
    counts = {k: 0 for k in KINDS}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return _ok("", items=items, count=len(items), counts=counts)


def _row(row: sqlite3.Row) -> dict:
    item = {k: row[k] for k in row.keys()}
    try:
        item["scripts"] = json.loads(item.pop("scripts_json", "{}") or "{}")
    except json.JSONDecodeError:
        item["scripts"] = {}
    item["needs_deploy"] = bool(item.get("needs_deploy"))
    item["auto_deploy"] = bool(item.get("auto_deploy"))
    item["package"] = f"{item['name']}@{item['version']}"
    return item


def get_manifest(software_id: str, db_path=None) -> dict:
    parts = [p for p in str(software_id or "").split("/") if p]
    if len(parts) != 5:
        return _fail("软件标识格式应为 产品/子系统/类型/名称/版本")
    product, subsystem, kind, name, version = parts
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM software_archive WHERE product=? AND subsystem=? AND kind=? AND name=? AND version=?",
            (product, subsystem, kind, name, version),
        ).fetchone()
    if row is None:
        return _fail(f"未归档该软件：{software_id}")
    return _ok("", software=_row(row))


def delete_software(software_id: str, db_path=None, base_dir=None) -> dict:
    parts = [p for p in str(software_id or "").split("/") if p]
    if len(parts) != 5:
        return _fail("软件标识格式应为 产品/子系统/类型/名称/版本")
    refs = references(software_id, db_path=db_path)
    if refs.get("items"):
        return _fail("该软件仍被 TPS 引用，不能删除：" + "、".join(r["tps_id"] for r in refs["items"][:5]))
    product, subsystem, kind, name, version = parts
    with _LOCK, _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM software_archive WHERE product=? AND subsystem=? AND kind=? AND name=? AND version=?",
            (product, subsystem, kind, name, version),
        )
        if cur.rowcount == 0:
            return _fail(f"未归档该软件：{software_id}")
    path = _base(base_dir) / "software" / product / subsystem / kind / name / version
    try:
        if path.is_dir():
            for p in sorted(path.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            path.rmdir()
    except OSError:
        pass
    return _ok(f"已删除 {software_id}")


def references(software_id: str, db_path=None) -> dict:
    items = []
    for summary in tps_store.list_tps():
        try:
            raw = tps_store.load(summary["id"])
        except Exception:  # noqa: BLE001
            continue
        for idx, dep in enumerate(raw.get("deploy") or [], 1):
            pkg = str(dep.get("package") or "")
            if software_id.endswith(f"/{pkg.split('@')[0]}/{pkg.split('@')[-1]}"):
                items.append({"tps_id": summary["id"], "tps_name": summary.get("name", ""),
                              "key": dep.get("key", ""), "order": idx})
    return _ok("", items=items, count=len(items))


# ---------------------------------------------------------------- TPS deploy 编排


def _device_aliases(tps: dict) -> set[str]:
    return {str(k) for k in (tps.get("device_config") or {}).keys()}


def get_tps_deploy(tps_id: str) -> dict:
    try:
        raw = tps_store.load(tps_id)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"TPS 不存在：{tps_id}（{exc}）")
    return _ok("", tps_id=tps_id, deploy=raw.get("deploy") or [], device_aliases=sorted(_device_aliases(raw)))


def set_tps_deploy(tps_id: str, deploy: Any, db_path=None) -> dict:
    try:
        raw = tps_store.load(tps_id)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"TPS 不存在：{tps_id}（{exc}）")
    if not isinstance(deploy, list):
        return _fail("deploy 必须是数组")
    aliases = _device_aliases(raw)
    product = _text(raw.get("product"), 32)
    subsystem = _text(raw.get("subsystem"), 32)
    keys: set[str] = set()
    rows: list[dict] = []
    for index, item in enumerate(deploy, 1):
        where = f"第 {index} 项"
        if not isinstance(item, dict):
            return _fail(f"{where} 不是对象")
        pkg = _text(item.get("package"), 120)
        if not PKG_RE.match(pkg):
            return _fail(f"{where} 的 package 应为 名称@版本：{pkg}")
        name, version = pkg.split("@", 1)
        key = _text(item.get("key"), 32) or name.lower()
        if not KEY_RE.match(key):
            return _fail(f"{where} 的 key 不合法（小写字母/数字/_/-，最长 32）：{key}")
        if key in keys:
            return _fail(f"deploy 的 key 重复：{key}")
        keys.add(key)
        found = _find_software(product, subsystem, name, version, db_path)
        if found is None:
            return _fail(f"{where} 引用的软件未归档或不属于本产品/子系统：{pkg}")
        if found["kind"] == "assistant":
            return _fail(f"{where} 引用了装备助手（{pkg}），助手不在 TPS 部署清单中")
        target = item.get("target")
        if isinstance(target, dict):
            target_id = _text(target.get("id"), 64) or "local"
        else:
            target_id = _text(target, 64) or "local"
        if target_id != "local" and target_id not in aliases:
            return _fail(f"{where} 的目标设备不在本 TPS 设备清单中：{target_id}")
        depends = item.get("depends_on") or []
        if not isinstance(depends, list) or any(_text(d, 32) not in keys - {key} for d in depends):
            # 允许前向引用，最终统一校验
            depends = [_text(d, 32) for d in depends]
        row = {
            "key": key,
            "package": pkg,
            "mode": _text(item.get("mode"), 16) or "ensure",
            "target": {"id": target_id},
            "depends_on": depends,
        }
        if item.get("order") is not None:
            try:
                row["order"] = int(item["order"])
            except (TypeError, ValueError):
                return _fail(f"{where} 的 order 必须是整数")
        rows.append(row)
    # depends_on 必须指向本次清单里的 key
    for row in rows:
        missing = [d for d in row["depends_on"] if d not in keys]
        if missing:
            return _fail(f"deploy 依赖不存在：{'、'.join(missing)}")
    raw["deploy"] = rows
    try:
        tps_store.save(raw, overwrite=True)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"保存 TPS 失败：{exc}")
    return _ok(f"已保存 {len(rows)} 项部署", deploy=rows)


def _find_software(product: str, subsystem: str, name: str, version: str, db_path=None) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM software_archive WHERE UPPER(product)=? AND UPPER(subsystem)=?"
            " AND name=? AND version=?",
            (product.upper(), subsystem.upper(), name, version),
        ).fetchone()
    return _row(row) if row else None


# ---------------------------------------------------------------- 归档助手（服务端复核）


def verify_archive(payload: dict) -> dict:
    """只校验不入库，供归档助手与服务端同源复核（SR01-AR08）。"""
    try:
        kind = _kind(payload.get("kind"))
        if kind == "tps":
            raise ValueError("TPS 不进入装备软件归档通道")
        _scope(payload.get("product"), payload.get("subsystem"))
        name = _text(payload.get("name"), 64)
        version = _text(payload.get("version"), 48)
        if not NAME_RE.match(name) or not VERSION_RE.match(version):
            raise ValueError("名称或版本号不合法")
        blob = _decode(payload.get("content_base64"))
        with zipfile.ZipFile(BytesIO(blob)) as zf:
            names = {n.replace("\\", "/").lstrip("./") for n in zf.namelist() if not n.endswith("/")}
            manifest = _read_manifest(zf, names) if "manifest.json" in names else {}
        scripts = _scripts(kind, manifest.get("scripts") or payload.get("scripts"), names)
        return _ok(
            "校验通过",
            kind=kind, name=name, version=version, scripts=scripts,
            file_count=len(names), sha256=hashlib.sha256(blob).hexdigest(),
            needs_deploy=bool(payload.get("needs_deploy", manifest.get("needs_deploy", kind == "assistant"))),
            auto_deploy=bool(payload.get("auto_deploy", manifest.get("auto_deploy", False))),
        )
    except ValueError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"校验失败：{exc}")


# ---------------------------------------------------------------- 路由


def register_routes(app, base_dir: Path, db_path: Optional[Path] = None) -> None:
    global _BASE, _DB
    _BASE = Path(base_dir)
    _DB = Path(db_path) if db_path else ate_db.DB_PATH
    ate_db.init_db(_DB)

    from fastapi import Body

    @app.get("/api/v1/software")
    def software_list(kind: str = "", product: str = "", subsystem: str = ""):
        try:
            return list_software(kind, product, subsystem, db_path=_DB)
        except ValueError as exc:
            return _fail(str(exc), items=[], count=0, counts={})
        except Exception as exc:  # noqa: BLE001
            return _fail(f"读取软件清单失败：{exc}", items=[], count=0, counts={})

    @app.post("/api/v1/software")
    def software_archive(payload: Any = Body(default=None)):
        data = payload if isinstance(payload, dict) else {}
        return archive_software(data, base_dir=_BASE, db_path=_DB,
                                operator=_text(data.get("operator"), 64))

    @app.post("/api/v1/software/verify")
    def software_verify(payload: Any = Body(default=None)):
        return verify_archive(payload if isinstance(payload, dict) else {})

    @app.get("/api/v1/software/{software_id:path}/manifest")
    def software_manifest(software_id: str):
        return get_manifest(software_id, db_path=_DB)

    @app.get("/api/v1/software/{software_id:path}/refs")
    def software_refs(software_id: str):
        return references(software_id, db_path=_DB)

    @app.delete("/api/v1/software/{software_id:path}")
    def software_delete(software_id: str):
        return delete_software(software_id, db_path=_DB, base_dir=_BASE)

    @app.get("/api/v1/tps/{tps_id}/deploy")
    def tps_deploy_get(tps_id: str):
        return get_tps_deploy(tps_id)

    @app.put("/api/v1/tps/{tps_id}/deploy")
    def tps_deploy_put(tps_id: str, payload: Any = Body(default=None)):
        data = payload if isinstance(payload, dict) else {}
        return set_tps_deploy(tps_id, data.get("deploy") or [], db_path=_DB)
