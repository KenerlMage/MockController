# -*- coding: utf-8 -*-
"""机器常数导入（需求 SR01-AR01 全量导入 / SR01-AR02 部分导入与备份留存）

包格式
------
标准包是一个 zip，根目录必须有 ``manifest.json``：

    {
      "version": "2026.11.01",
      "signature": "ate-controller-export-v1",   # 本系统导出签名，部分导入必须非空
      "label": "…", "source": "…", "note": "…",   # 可选，写入 constant_version
      "files": [{"path": "SPM/SPM_RH.xml", "sha256": "<64 位小写>", "size": 123}, …]
    }

* 清单外的文件、清单里缺失的文件、大小或 sha256 与清单不符 —— 整包拒绝，库一字节不动。
* 包内模型文件为 ``Module.xml`` / ``<SN>_Module.xml``（XML 契约见
  ``docs/需求设计/机器常数实现契约.md`` 第三节）；也兼容 ``.json`` 常数包。

两种导入
--------
* ``import_full``：校验通过后在**同一事务**内清空 ``constant_version / constant_model /
  constant_value`` 再写新版本；清空前把旧模型结构整体存进 ``const_backup``，导入动作写
  ``const_import_log(kind='full')``。同包重复导入直接返回 ``already_imported=true``。
* ``import_partial``：只接受带 ``signature`` 的标准包；按 (version, pn, model_key) 命中即覆盖，
  未命中追加（本版本没有该模型时回落到当前生效版本取旧结构）；命中的旧 ``constant_model`` 行
  原样写入 ``const_backup``，并记 ``replaced_by``（新版本号）、``reason``、``operator``。

回滚
----
``list_backups`` 列出备份，``restore_backup`` 按备份编号把结构写回 ``constant_model`` 并回填
``restored_at``。

对外
----
``import_full`` / ``import_partial`` / ``list_backups`` / ``restore_backup`` /
``register_routes(app, base_dir, db_path=None)``，路由前缀 ``/api/v1``。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import sqlite3
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

import ate_db
import constants

__all__ = [
    "import_full",
    "import_partial",
    "list_backups",
    "restore_backup",
    "register_routes",
]

MANIFEST_NAME = "manifest.json"
# 系统噪音文件：不进清单也不参与校验
NOISE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

_LOCK = threading.RLock()
_BASE_DIR: Optional[Path] = None
_DB_PATH: Optional[Path] = None
_READY: set[str] = set()

MAX_LIST_LIMIT = 500


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    """契约统一时间格式：本地时区的 ISO 8601（秒精度）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _fail(message: str, **extra: Any) -> dict:
    return {"success": False, "message": message, **extra}


def _db_file(db_path: Any = None) -> Path:
    if db_path:
        return Path(db_path)
    if _DB_PATH:
        return Path(_DB_PATH)
    return Path(ate_db.DB_PATH)


def _ensure_db(path: Path) -> None:
    """首次用到某个库时建表（幂等）；同一路径只建一次。"""
    try:
        key = str(Path(path).resolve())
    except OSError:  # pragma: no cover - 极端路径
        key = str(path)
    if key in _READY:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ate_db.init_db(Path(path))
    _READY.add(key)


def _connect(db_path: Any = None) -> sqlite3.Connection:
    path = _db_file(db_path)
    _ensure_db(path)
    return ate_db.connect(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _active_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT version FROM constant_version WHERE is_active = 1 "
        "ORDER BY imported_at DESC, version DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["version"] or ""
    row = conn.execute(
        "SELECT version FROM constant_version ORDER BY imported_at DESC, version DESC LIMIT 1"
    ).fetchone()
    return (row["version"] if row else "") or ""


def _scope(pn: str, product: str, subsystem: str) -> tuple[str, str]:
    """产品/子系统两端都有就直接用；缺一头才去装备树推断。"""
    product = _text(product, 64)
    subsystem = _text(subsystem, 64)
    if product and subsystem:
        return product, subsystem
    try:
        return constants._resolve_tree_scope(pn, product, subsystem)  # noqa: SLF001
    except Exception:  # pragma: no cover - 装备树缺失时按原样落库
        return product, subsystem


# ---------------------------------------------------------------- 包读取与校验


def _norm_entry(name: str) -> str:
    return str(name or "").replace("\\", "/").strip("/")


def _safe_rel(path: str) -> str:
    """清单内的路径必须是包内相对路径，禁止绝对路径与 .. 越界。"""
    raw = _norm_entry(path)
    if not raw:
        raise ValueError("清单中存在空的文件路径")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"清单路径必须是包内相对路径: {path}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"清单路径越界: {path}")
    if not parts:
        raise ValueError(f"清单路径不合法: {path}")
    return "/".join(parts)


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, name: str) -> bytes:
    """按上限流式读取，超过 limit 立即中止（防伪造 size 的 zip bomb）。"""
    chunks: list[bytes] = []
    got = 0
    with zf.open(info) as fh:
        while True:
            chunk = fh.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > limit:
                raise ValueError(
                    f"文件 {name} 解压后超过允许的 {limit} 字节（清单声明或上限），整包拒绝"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _read_package(blob: bytes, filename: str = "", *, require_signature: bool) -> dict:
    """解包 + 清单校验 + 逐文件 sha256；任何不符抛 ValueError（调用方未写库）。"""
    if isinstance(blob, (bytearray, memoryview)):
        blob = bytes(blob)
    if not isinstance(blob, bytes):
        raise ValueError("导入内容必须是二进制 zip")
    if not blob:
        raise ValueError("导入内容为空")
    limit_mb = constants.IMPORT_MAX_ZIP_BYTES // (1024 * 1024)
    if len(blob) > constants.IMPORT_MAX_ZIP_BYTES:
        raise ValueError(f"压缩包超过 {limit_mb}MB 上限（实际 {len(blob)} 字节）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ValueError("导入内容不是有效的 ZIP 文件")

    with zf:
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _norm_entry(info.filename)
            if not name or name.startswith("__MACOSX/"):
                continue
            if name.rsplit("/", 1)[-1].lower() in NOISE_NAMES:
                continue
            if len(entries) >= constants.IMPORT_MAX_ZIP_FILES:
                raise ValueError(
                    f"压缩包内文件超过 {constants.IMPORT_MAX_ZIP_FILES} 个上限"
                )
            if name in entries:
                raise ValueError(f"压缩包内存在重名文件: {name}")
            entries[name] = info
        if not entries:
            raise ValueError("压缩包内没有文件")

        manifests = [n for n in entries if n.rsplit("/", 1)[-1].lower() == MANIFEST_NAME]
        if not manifests:
            raise ValueError(
                "包内缺少 manifest.json：导入包必须是带清单的标准包（逐文件 SHA-256 校验的唯一依据）"
            )
        if len(manifests) > 1:
            raise ValueError(f"包内存在多个 manifest.json: {'、'.join(sorted(manifests))}")
        manifest_path = manifests[0]
        prefix = manifest_path[: -len(MANIFEST_NAME)]

        try:
            raw_manifest = _read_entry(
                zf, entries[manifest_path], 1024 * 1024, MANIFEST_NAME
            )
        except ValueError as exc:
            raise ValueError(f"manifest.json 读取失败: {exc}")
        except Exception as exc:  # noqa: BLE001 - 损坏条目给出明确原因
            raise ValueError(f"manifest.json 读取失败: {exc}")
        try:
            manifest = json.loads(raw_manifest.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise ValueError("manifest.json 不是 UTF-8 编码")
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"manifest.json 不是有效 JSON（第 {exc.lineno} 行第 {exc.colno} 列）"
            )
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json 顶层必须是对象")

        version = _text(manifest.get("version") or manifest.get("constant_version"), 48)
        if version and not constants.VERSION_RE.match(version):
            raise ValueError(f"manifest.json 的版本号不合法（字母/数字/._+-，最长 48）: {version}")

        files_raw = manifest.get("files")
        if not isinstance(files_raw, list) or not files_raw:
            raise ValueError("manifest.json 缺少 files 清单（每项需 path / sha256 / size）")
        if len(files_raw) > constants.IMPORT_MAX_ZIP_FILES:
            raise ValueError(
                f"清单文件数超过 {constants.IMPORT_MAX_ZIP_FILES} 个上限（{len(files_raw)} 个）"
            )

        declared: dict[str, dict] = {}
        total = 0
        for idx, item in enumerate(files_raw):
            where = f"清单第 {idx + 1} 项"
            if not isinstance(item, dict):
                raise ValueError(f"{where} 不是对象")
            rel = _safe_rel(str(item.get("path") or ""))
            digest = _text(item.get("sha256"), 64).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{where}（{rel}）的 sha256 必须是 64 位十六进制")
            try:
                size = int(item.get("size"))
            except (TypeError, ValueError):
                raise ValueError(f"{where}（{rel}）的 size 不是整数")
            if size < 0:
                raise ValueError(f"{where}（{rel}）的 size 不能为负")
            if size > constants.IMPORT_MAX_FILE_BYTES:
                raise ValueError(
                    f"{where}（{rel}）超过单文件 "
                    f"{constants.IMPORT_MAX_FILE_BYTES // (1024 * 1024)}MB 上限"
                )
            key = prefix + rel
            if key in declared:
                raise ValueError(f"清单重复声明文件: {rel}")
            declared[key] = {"path": rel, "sha256": digest, "size": size}
            total += size
            if total > constants.IMPORT_MAX_ZIP_BYTES:
                raise ValueError(f"清单内文件合计超过 {limit_mb}MB 上限")

        content_names = [n for n in entries if n != manifest_path]
        missing = sorted(n for n in declared if n not in entries)
        if missing:
            raise ValueError(f"清单声明的文件在包内不存在: {'、'.join(missing[:5])}")
        extra = sorted(n for n in content_names if n not in declared)
        if extra:
            raise ValueError(
                f"包内存在清单未声明的文件，整包拒绝: {'、'.join(extra[:5])}"
            )

        payloads: dict[str, bytes] = {}
        for key, meta in declared.items():
            try:
                data = _read_entry(zf, entries[key], meta["size"], meta["path"])
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"文件 {meta['path']} 读取失败: {exc}")
            if len(data) != meta["size"]:
                raise ValueError(
                    f"文件 {meta['path']} 大小与清单不符（清单 {meta['size']}，实际 {len(data)}）"
                )
            actual = _sha256(data)
            if actual != meta["sha256"]:
                raise ValueError(
                    f"文件 {meta['path']} 的 SHA-256 与清单不符"
                    f"（清单 {meta['sha256'][:12]}…，实际 {actual[:12]}…），整包拒绝"
                )
            payloads[key] = data

    signature = _text(manifest.get("signature"), 200)
    if require_signature and not signature:
        raise ValueError(
            "非本系统导出的标准包（manifest.json 缺少 signature 签名），部分导入已拒绝"
        )

    return {
        "filename": _text(filename, 200),
        "package_hash": _sha256(blob),
        "size": len(blob),
        "version": version,
        "label": _text(manifest.get("label"), 120),
        "source": _text(manifest.get("source") or manifest.get("exporter"), 200),
        "note": _text(manifest.get("note"), 400),
        "reason": _text(manifest.get("reason"), 200),
        "signature": signature,
        "file_count": len(declared),
        "files": [declared[k] for k in declared],
        "payloads": payloads,
    }


# ---------------------------------------------------------------- 模型文件解析


def _strip_ns(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _child(elem: ET.Element, tag: str) -> Optional[ET.Element]:
    for node in elem:
        if _strip_ns(node.tag) == tag:
            return node
    return None


def _to_bool(value: Any) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "是", "开"):
        return True
    if text in ("0", "false", "no", "n", "否", "关"):
        return False
    return None


def _split_options(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;|]", raw or "") if x.strip()]


def _xml_field(el: ET.Element, name: str, index: int) -> dict:
    """单个 <Field> → parse_bundle 的字段入参；结构不合规直接抛 ValueError。"""
    where = f"{name}: 第 {index + 1} 个字段"
    key = _text(el.get("key"), 64)
    if not key:
        raise ValueError(f"{where} 缺少 key")
    label = _text(el.get("label") or key, 120)
    ftype = _text(el.get("type"), 16).lower()
    if not ftype:
        raise ValueError(f"{where}（{key}）缺少 type")
    if ftype not in constants.FIELD_TYPES:
        raise ValueError(
            f"{where}（{key}）的 type 必须是 {'/'.join(constants.FIELD_TYPES)}，实际 {ftype}"
        )
    field: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": ftype,
        "unit": _text(el.get("unit"), 32),
        "default": el.get("default"),
        "min": el.get("min"),
        "max": el.get("max"),
        "options": [],
        "desc": _text(el.get("desc"), 300),
    }
    if ftype in ("float", "int"):
        for edge in ("min", "max"):
            if el.get(edge) in (None, ""):
                raise ValueError(f"{where}（{key}）type={ftype} 必须给出 {edge}")
        try:
            low = float(el.get("min"))
            high = float(el.get("max"))
        except (TypeError, ValueError):
            raise ValueError(f"{where}（{key}）的 min/max 必须是数值")
        if low > high:
            raise ValueError(f"{where}（{key}）的 min={low:g} 大于 max={high:g}")
        if el.get("default") in (None, ""):
            raise ValueError(f"{where}（{key}）type={ftype} 必须给出 default")
        try:
            default = float(el.get("default"))
        except (TypeError, ValueError):
            raise ValueError(f"{where}（{key}）的 default 与 type={ftype} 不匹配")
        if not (low <= default <= high):
            raise ValueError(
                f"{where}（{key}）的 default={default:g} 不在 [{low:g}, {high:g}] 范围内"
            )
        if ftype == "int":
            for val in (low, high, default):
                if abs(val - round(val)) > 1e-9:
                    raise ValueError(f"{where}（{key}）type=int 但 min/max/default 含小数")
    elif ftype == "enum":
        options = _split_options(_text(el.get("values") or el.get("options"), 400))
        if not options:
            raise ValueError(f"{where}（{key}）type=enum 必须给出 values（逗号分隔的取值）")
        field["options"] = options
        default = el.get("default")
        if default not in (None, "") and str(default) not in options:
            raise ValueError(
                f"{where}（{key}）的 default={default} 不在枚举 values（{'、'.join(options)}）内"
            )
    elif ftype == "bool":
        if el.get("default") not in (None, ""):
            parsed = _to_bool(el.get("default"))
            if parsed is None:
                raise ValueError(f"{where}（{key}）的 default 不是布尔值: {el.get('default')}")
            field["default"] = parsed
    return field


def _doc_from_xml(name: str, data: bytes, manifest_version: str) -> dict:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{name}: 不是 UTF-8 编码")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{name}: XML 解析失败（{exc}）")
    if _strip_ns(root.tag) != "MachineConstants":
        raise ValueError(f"{name}: 根元素必须是 MachineConstants，实际 {root.tag}")

    module = _text(root.get("module") or root.get("model") or root.get("modelKey"), 64)
    if not module:
        raise ValueError(f"{name}: 缺少 module（模型键）")
    if not constants.KEY_RE.match(module):
        raise ValueError(f"{name}: module 不合法（字母/数字/下划线/点/短横，最长 64）: {module}")
    pn = _text(root.get("pn") or root.get("partNo"), 64)
    if not pn:
        raise ValueError(f"{name}: 缺少 pn")
    if not constants.PN_RE.match(pn):
        raise ValueError(f"{name}: pn 不合法: {pn}")

    declared_version = _text(root.get("version"), 48)
    if declared_version and not constants.VERSION_RE.match(declared_version):
        raise ValueError(f"{name}: version 不合法: {declared_version}")

    calibrated = _text(root.get("calibrated"), 8).lower()
    if calibrated not in ("", "true", "false", "1", "0", "yes", "no"):
        raise ValueError(f"{name}: calibrated 只能是 true/false，实际 {calibrated}")
    is_instance = calibrated in ("true", "1", "yes")
    sn = _text(root.get("sn"), 64)
    if is_instance and not sn:
        raise ValueError(f"{name}: calibrated=true 的标定实例必须带 sn")

    meta = _child(root, "Meta")
    product = _text(meta.get("product"), 64) if meta is not None else ""
    subsystem = _text(meta.get("subsystem"), 64) if meta is not None else ""
    model_name = _text(meta.get("name"), 120) if meta is not None else ""
    category = _text(meta.get("category"), 48) if meta is not None else ""
    description = _text(meta.get("description"), 400) if meta is not None else ""
    unit = _text(meta.get("unit"), 32) if meta is not None else ""
    model_name = model_name or _text(root.get("name"), 120) or module

    fields_el = _child(root, "Fields")
    if fields_el is None:
        raise ValueError(f"{name}: 缺少 Fields 节点")
    field_nodes = [el for el in fields_el if _strip_ns(el.tag) == "Field"]
    if not field_nodes:
        raise ValueError(f"{name}: Fields 下没有字段定义")

    fields: list[dict] = []
    values: dict[str, str] = {}
    seen: set[str] = set()
    for index, el in enumerate(field_nodes):
        field = _xml_field(el, name, index)
        if field["key"] in seen:
            raise ValueError(f"{name}: 字段 key 重复: {field['key']}")
        seen.add(field["key"])
        fields.append(field)
        raw_value = el.get("value")
        if raw_value is not None and str(raw_value).strip() != "":
            values[field["key"]] = str(raw_value).strip()

    if is_instance:
        missing = [f["key"] for f in fields if f["key"] not in values]
        if missing:
            raise ValueError(
                f"{name}: calibrated=true 的标定实例每个字段都必须带 value，缺: "
                f"{'、'.join(missing[:5])}"
            )

    return {
        "file": name,
        "declared_version": declared_version,
        "payloads": [
            {
                "version": declared_version or manifest_version or "default",
                "pn": pn,
                "product": product,
                "subsystem": subsystem,
                "models": [
                    {
                        "model_key": module,
                        "name": model_name,
                        "category": category,
                        "description": description,
                        "unit": unit,
                        "fields": fields,
                        "values": values,
                    }
                ],
            }
        ],
    }


def _doc_from_json(name: str, data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{name}: 不是 UTF-8 编码")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name}: 不是有效 JSON（第 {exc.lineno} 行第 {exc.colno} 列）")
    if isinstance(raw, list):
        raw = {"models": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"{name}: JSON 顶层必须是对象或数组")
    declared = _text(raw.get("version") or raw.get("constant_version") or raw.get("ver"), 48)
    try:
        parts = constants._split_single_model(raw)  # noqa: SLF001 复用既有归一逻辑
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}")
    payloads = []
    for part in parts:
        item = dict(part)
        item.setdefault("version", declared or "default")
        payloads.append(item)
    return {"file": name, "declared_version": declared, "payloads": payloads}


def _parse_documents(payloads: dict[str, bytes], manifest_version: str) -> list[dict]:
    docs: list[dict] = []
    for name in sorted(payloads):
        data = payloads[name]
        lower = name.lower()
        if lower.endswith(".xml"):
            docs.append(_doc_from_xml(name, data, manifest_version))
        elif lower.endswith(".json"):
            docs.append(_doc_from_json(name, data))
        else:
            raise ValueError(f"{name}: 包内只接受 .xml 模型文件或 .json 常数包")
    if not docs:
        raise ValueError("包内没有可导入的模型文件")
    return docs


def _collect(docs: list[dict], manifest_version: str) -> tuple[str, list[dict]]:
    """逐文件 parse_bundle 归一，并合并成一份 groups（同 PN 同 model_key 视为冲突）。"""
    declared = {d["declared_version"] for d in docs if d.get("declared_version")}
    if manifest_version:
        mismatch = sorted(v for v in declared if v != manifest_version)
        if mismatch:
            raise ValueError(
                f"包内文件版本 {mismatch[0]} 与 manifest.json 的版本 {manifest_version} 不一致"
            )
        version = manifest_version
    else:
        if len(declared) > 1:
            raise ValueError(f"包内文件版本不一致: {'、'.join(sorted(declared))}")
        version = next(iter(declared), "") or "default"

    groups: list[dict] = []
    origin: dict[tuple[str, str], str] = {}
    for doc in docs:
        for payload in doc["payloads"]:
            item = dict(payload)
            item["version"] = version
            try:
                bundle = constants.parse_bundle(item)
            except ValueError as exc:
                raise ValueError(f"{doc['file']}: {exc}")
            for group in bundle["groups"]:
                slot = next((g for g in groups if g["pn"] == group["pn"]), None)
                if slot is None:
                    slot = {
                        "pn": group["pn"],
                        "product": group["product"],
                        "subsystem": group["subsystem"],
                        "models": [],
                    }
                    groups.append(slot)
                else:
                    slot["product"] = slot["product"] or group["product"]
                    slot["subsystem"] = slot["subsystem"] or group["subsystem"]
                for model in group["models"]:
                    holder = origin.get((group["pn"], model["model_key"]))
                    if holder:
                        raise ValueError(
                            f"PN={group['pn']} 的模型 {model['model_key']} 在 "
                            f"{holder} 与 {doc['file']} 中重复定义"
                        )
                    origin[(group["pn"], model["model_key"])] = doc["file"]
                    slot["models"].append(model)
    if not groups:
        raise ValueError("包内没有可导入的常数模型")
    return version, groups


def _prepare(blob: bytes, filename: str, *, require_signature: bool) -> dict:
    """读取 + 校验 + 解析，全部成功才返回；失败只抛 ValueError，不碰数据库。"""
    pkg = _read_package(blob, filename, require_signature=require_signature)
    docs = _parse_documents(pkg["payloads"], pkg["version"])
    version, groups = _collect(docs, pkg["version"])
    stats = _stats(groups)
    return {**pkg, "version": version, "groups": groups, "stats": stats}


def _stats(groups: list[dict]) -> dict:
    return {
        "pn_count": len(groups),
        "model_count": sum(len(g["models"]) for g in groups),
        "field_count": sum(len(m["fields"]) for g in groups for m in g["models"]),
        "value_count": sum(
            len(m.get("values") or {}) for g in groups for m in g["models"]
        ),
    }


# ---------------------------------------------------------------- 写库


def _insert_backup(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    replaced_by: str,
    reason: str,
    operator: str,
    now: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO const_backup "
        "(pn, model_key, version, product, subsystem, name, fields_json, "
        " replaced_by, reason, operator, restored_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row["pn"],
            row["model_key"],
            row["version"],
            row["product"] or "",
            row["subsystem"] or "",
            row["name"] or "",
            row["fields_json"] or "[]",
            replaced_by,
            reason,
            operator,
            "",
            now,
        ),
    )
    return int(cur.lastrowid or 0)


def _backup_all(conn: sqlite3.Connection, *, replaced_by: str, reason: str,
                operator: str, now: str) -> list[int]:
    ids: list[int] = []
    for row in conn.execute("SELECT * FROM constant_model").fetchall():
        ids.append(
            _insert_backup(
                conn, row, replaced_by=replaced_by, reason=reason, operator=operator, now=now
            )
        )
    return ids


def _upsert_model(
    conn: sqlite3.Connection,
    version: str,
    pn: str,
    product: str,
    subsystem: str,
    model: dict,
    now: str,
) -> bool:
    """写一行模型结构；返回 True 表示覆盖了已存在的行。"""
    fields_json = json.dumps(model["fields"], ensure_ascii=False)
    struct_checksum = constants._checksum(model["fields"])  # noqa: SLF001
    row = conn.execute(
        "SELECT id FROM constant_model WHERE version = ? AND pn = ? AND model_key = ?",
        (version, pn, model["model_key"]),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE constant_model SET product = ?, subsystem = ?, name = ?, category = ?, "
            "description = ?, unit = ?, fields_json = ?, field_count = ?, "
            "struct_checksum = ?, updated_at = ? WHERE id = ?",
            (
                product,
                subsystem,
                model["name"],
                model["category"],
                model["description"],
                model["unit"],
                fields_json,
                len(model["fields"]),
                struct_checksum,
                now,
                row["id"],
            ),
        )
        return True
    conn.execute(
        "INSERT INTO constant_model "
        "(version, pn, product, subsystem, model_key, name, category, description, unit, "
        " fields_json, field_count, struct_checksum, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version,
            pn,
            product,
            subsystem,
            model["model_key"],
            model["name"],
            model["category"],
            model["description"],
            model["unit"],
            fields_json,
            len(model["fields"]),
            struct_checksum,
            now,
        ),
    )
    return False


def _write_values(
    conn: sqlite3.Connection,
    version: str,
    pn: str,
    model: dict,
    now: str,
    *,
    keep_from: str = "",
) -> None:
    """写标定值：包内 value 优先，其余保留 keep_from 版本里仍存在字段的旧值。"""
    keys = [f["key"] for f in model["fields"]]
    provided = model.get("values") or {}
    kept: dict[str, sqlite3.Row] = {}
    if keep_from:
        for row in conn.execute(
            "SELECT field_key, value, unit, source, updated_at FROM constant_value "
            "WHERE version = ? AND pn = ? AND model_key = ?",
            (keep_from, pn, model["model_key"]),
        ):
            kept[row["field_key"]] = row

    marks = ",".join("?" * len(keys))
    conn.execute(
        f"DELETE FROM constant_value WHERE version = ? AND pn = ? AND model_key = ? "
        f"AND field_key NOT IN ({marks})",
        (version, pn, model["model_key"], *keys),
    )
    units = {f["key"]: f.get("unit", "") for f in model["fields"]}
    for key in keys:
        if key in provided:
            conn.execute(
                "INSERT OR REPLACE INTO constant_value "
                "(version, pn, model_key, field_key, value, unit, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (version, pn, model["model_key"], key, provided[key],
                 units.get(key, ""), "import", now),
            )
        elif key in kept:
            old = kept[key]
            conn.execute(
                "INSERT OR REPLACE INTO constant_value "
                "(version, pn, model_key, field_key, value, unit, source, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    version,
                    pn,
                    model["model_key"],
                    key,
                    old["value"],
                    old["unit"] or units.get(key, ""),
                    old["source"] or "import",
                    old["updated_at"] or now,
                ),
            )


def _ensure_version(
    conn: sqlite3.Connection, version: str, *, label: str = "", source: str = "",
    note: str = "", operator: str = "", now: str = ""
) -> bool:
    row = conn.execute(
        "SELECT version FROM constant_version WHERE version = ?", (version,)
    ).fetchone()
    if row:
        return False
    any_row = conn.execute("SELECT COUNT(*) AS c FROM constant_version").fetchone()
    conn.execute(
        "INSERT INTO constant_version "
        "(version, label, source, note, model_count, field_count, imported_at, "
        " imported_by, is_active, checksum) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            version,
            label or version,
            source,
            note,
            0,
            0,
            now or _now(),
            operator,
            0 if (any_row and any_row["c"]) else 1,
            "",
        ),
    )
    return True


def _refresh_version(conn: sqlite3.Connection, version: str) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(field_count), 0) AS f "
        "FROM constant_model WHERE version = ?",
        (version,),
    ).fetchone()
    conn.execute(
        "UPDATE constant_version SET model_count = ?, field_count = ? WHERE version = ?",
        (int(row["c"] or 0), int(row["f"] or 0), version),
    )


def _log(
    conn: sqlite3.Connection,
    *,
    kind: str,
    package_hash: str,
    file_count: int,
    prev_version: str,
    new_version: str,
    operator: str,
    note: str,
    now: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO const_import_log "
        "(kind, package_hash, file_count, prev_version, new_version, operator, note, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (kind, package_hash, file_count, prev_version, new_version, operator, note, now),
    )
    return int(cur.lastrowid or 0)


def _same_import(
    conn: sqlite3.Connection, kind: str, package_hash: str, version: str, model_count: int
) -> bool:
    if not package_hash or model_count <= 0:
        return False
    row = conn.execute(
        "SELECT new_version FROM const_import_log WHERE kind = ? AND package_hash = ? "
        "ORDER BY id DESC LIMIT 1",
        (kind, package_hash),
    ).fetchone()
    if not row or (row["new_version"] or "") != version:
        return False
    actual = conn.execute(
        "SELECT COUNT(*) AS c FROM constant_model WHERE version = ?", (version,)
    ).fetchone()
    return int(actual["c"] or 0) == model_count


# ---------------------------------------------------------------- 全量导入


def import_full(blob: bytes, filename: str = "", operator: str = "", db_path=None) -> dict:
    """SR01-AR01：整包校验通过后清空重建，写 full 导入日志。"""
    operator = _text(operator, 64)
    filename = _text(filename, 200)
    try:
        pkg = _prepare(blob, filename, require_signature=False)
    except ValueError as exc:
        return _fail(str(exc), filename=filename, version="", stats={})

    version = pkg["version"]
    groups = pkg["groups"]
    stats = pkg["stats"]
    now = _now()
    try:
        conn = _connect(db_path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"打开常数库失败: {exc}", filename=filename)
    try:
        with conn:
            prev_version = _active_version(conn)
            if _same_import(conn, "full", pkg["package_hash"], version, stats["model_count"]):
                return {
                    "success": True,
                    "already_imported": True,
                    "version": version,
                    "prev_version": prev_version,
                    "stats": stats,
                    "package_hash": pkg["package_hash"],
                    "file_count": pkg["file_count"],
                    "message": (
                        f"该包已全量导入过（版本 {version}，包哈希一致），本次未重复写入"
                    ),
                }
            backup_ids = _backup_all(
                conn,
                replaced_by=version,
                reason="全量导入覆盖前备份",
                operator=operator,
                now=now,
            )
            conn.execute("DELETE FROM constant_value")
            conn.execute("DELETE FROM constant_model")
            conn.execute("DELETE FROM constant_version")
            conn.execute(
                "INSERT INTO constant_version "
                "(version, label, source, note, model_count, field_count, imported_at, "
                " imported_by, is_active, checksum) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    version,
                    pkg["label"] or version,
                    pkg["source"],
                    pkg["note"],
                    stats["model_count"],
                    stats["field_count"],
                    now,
                    operator,
                    1,
                    constants._checksum(groups),  # noqa: SLF001
                ),
            )
            for group in groups:
                product, subsystem = _scope(group["pn"], group["product"], group["subsystem"])
                for model in group["models"]:
                    _upsert_model(conn, version, group["pn"], product, subsystem, model, now)
                    _write_values(conn, version, group["pn"], model, now)
            note = (
                f"{filename or '未命名包'}：{pkg['file_count']} 个文件 / "
                f"{stats['model_count']} 个模型 / {stats['field_count']} 个字段"
            )
            log_id = _log(
                conn,
                kind="full",
                package_hash=pkg["package_hash"],
                file_count=pkg["file_count"],
                prev_version=prev_version,
                new_version=version,
                operator=operator,
                note=note,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001 - 事务已回滚，库保持原状
        return _fail(f"全量导入失败（已回滚，库未改动）: {exc}", filename=filename)
    finally:
        conn.close()

    message = (
        f"全量导入成功：版本 {version}，{stats['pn_count']} 个 PN / "
        f"{stats['model_count']} 个模型 / {stats['field_count']} 个字段"
    )
    if prev_version and prev_version != version:
        message += f"；已覆盖前一版本 {prev_version}"
    if backup_ids:
        message += f"；旧结构备份 {len(backup_ids)} 条（可回滚）"
    return {
        "success": True,
        "already_imported": False,
        "version": version,
        "prev_version": prev_version,
        "stats": stats,
        "package_hash": pkg["package_hash"],
        "file_count": pkg["file_count"],
        "files": pkg["files"],
        "backup_ids": backup_ids,
        "log_id": log_id,
        "message": message,
    }


# ---------------------------------------------------------------- 部分导入


def import_partial(blob: bytes, filename: str = "", operator: str = "", db_path=None) -> dict:
    """SR01-AR02：只接受带 signature 的标准包；命中覆盖、未命中追加，旧结构进备份。"""
    operator = _text(operator, 64)
    filename = _text(filename, 200)
    try:
        pkg = _prepare(blob, filename, require_signature=True)
    except ValueError as exc:
        return _fail(str(exc), filename=filename, version="", stats={})

    version = pkg["version"]
    groups = pkg["groups"]
    stats = pkg["stats"]
    reason = pkg["reason"] or "部分导入覆盖"
    now = _now()
    try:
        conn = _connect(db_path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"打开常数库失败: {exc}", filename=filename)
    try:
        with conn:
            prev_version = _active_version(conn)
            if _same_import(conn, "partial", pkg["package_hash"], version,
                            stats["model_count"]):
                return {
                    "success": True,
                    "already_imported": True,
                    "version": version,
                    "prev_version": prev_version,
                    "stats": stats,
                    "package_hash": pkg["package_hash"],
                    "file_count": pkg["file_count"],
                    "message": (
                        f"该部分导入包已导入过（版本 {version}，包哈希一致），本次未重复写入"
                    ),
                }
            _ensure_version(
                conn, version, label=pkg["label"] or version, source=pkg["source"],
                note=pkg["note"], operator=operator, now=now,
            )
            added = 0
            replaced = 0
            backup_ids: list[int] = []
            for group in groups:
                pn = group["pn"]
                product, subsystem = _scope(pn, group["product"], group["subsystem"])
                for model in group["models"]:
                    old = conn.execute(
                        "SELECT * FROM constant_model "
                        "WHERE version = ? AND pn = ? AND model_key = ?",
                        (version, pn, model["model_key"]),
                    ).fetchone()
                    if old is None and prev_version and prev_version != version:
                        old = conn.execute(
                            "SELECT * FROM constant_model "
                            "WHERE version = ? AND pn = ? AND model_key = ?",
                            (prev_version, pn, model["model_key"]),
                        ).fetchone()
                    keep_from = ""
                    if old is not None:
                        backup_ids.append(
                            _insert_backup(
                                conn, old, replaced_by=version, reason=reason,
                                operator=operator, now=now,
                            )
                        )
                        keep_from = old["version"] or ""
                        replaced += 1
                    else:
                        added += 1
                    _upsert_model(conn, version, pn, product, subsystem, model, now)
                    _write_values(conn, version, pn, model, now, keep_from=keep_from)
            _refresh_version(conn, version)
            note = (
                f"{filename or '未命名包'}：{pkg['file_count']} 个文件，覆盖 {replaced} 个模型、"
                f"新增 {added} 个模型"
            )
            log_id = _log(
                conn,
                kind="partial",
                package_hash=pkg["package_hash"],
                file_count=pkg["file_count"],
                prev_version=prev_version,
                new_version=version,
                operator=operator,
                note=note,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"部分导入失败（已回滚，库未改动）: {exc}", filename=filename)
    finally:
        conn.close()

    message = (
        f"部分导入成功：版本 {version}，覆盖 {replaced} 个模型、新增 {added} 个模型"
    )
    if backup_ids:
        message += f"；旧结构已备份 {len(backup_ids)} 条"
    return {
        "success": True,
        "already_imported": False,
        "version": version,
        "prev_version": prev_version,
        "stats": stats,
        "replaced": replaced,
        "added": added,
        "backup_ids": backup_ids,
        "package_hash": pkg["package_hash"],
        "file_count": pkg["file_count"],
        "files": pkg["files"],
        "log_id": log_id,
        "message": message,
    }


# ---------------------------------------------------------------- 备份查询 / 回滚


def _backup_item(row: sqlite3.Row) -> dict:
    try:
        fields = json.loads(row["fields_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        fields = []
    if not isinstance(fields, list):
        fields = []
    return {
        "id": row["id"],
        "pn": row["pn"],
        "model_key": row["model_key"],
        "version": row["version"],
        "product": row["product"] or "",
        "subsystem": row["subsystem"] or "",
        "name": row["name"] or "",
        "fields": fields,
        "field_count": len(fields),
        "replaced_by": row["replaced_by"] or "",
        "reason": row["reason"] or "",
        "operator": row["operator"] or "",
        "restored_at": row["restored_at"] or "",
        "created_at": row["created_at"] or "",
    }


def list_backups(pn: str = "", model_key: str = "", limit: int = 100, db_path=None) -> dict:
    """按 PN / 模型键过滤的备份列表（时间倒序）。"""
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 100
    limit_int = max(1, min(limit_int, MAX_LIST_LIMIT))
    where: list[str] = []
    args: list[Any] = []
    pn_norm = _text(pn, 64).upper()
    if pn_norm:
        where.append("pn = ?")
        args.append(pn_norm)
    key_norm = _text(model_key, 64)
    if key_norm:
        where.append("model_key = ?")
        args.append(key_norm)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = _connect(db_path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"打开常数库失败: {exc}", items=[], count=0, total=0)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM const_backup{clause}", args
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM const_backup{clause} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*args, limit_int),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读取备份列表失败: {exc}", items=[], count=0, total=0)
    finally:
        conn.close()
    items = [_backup_item(row) for row in rows]
    return {
        "success": True,
        "message": f"共 {int(total or 0)} 条备份，返回 {len(items)} 条",
        "items": items,
        "count": len(items),
        "total": int(total or 0),
        "limit": limit_int,
        "pn": pn_norm,
        "model_key": key_norm,
    }


def restore_backup(backup_id: int, operator: str = "", db_path=None) -> dict:
    """按备份编号把模型结构写回 constant_model（同 version/pn/model_key upsert）。"""
    operator = _text(operator, 64)
    try:
        bid = int(backup_id)
    except (TypeError, ValueError):
        return _fail(f"备份编号不合法: {backup_id}")
    try:
        conn = _connect(db_path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"打开常数库失败: {exc}")
    now = _now()
    try:
        with _LOCK, conn:
            row = conn.execute("SELECT * FROM const_backup WHERE id = ?", (bid,)).fetchone()
            if row is None:
                return _fail(f"备份不存在: {bid}")
            try:
                fields = json.loads(row["fields_json"] or "[]")
            except (TypeError, json.JSONDecodeError) as exc:
                return _fail(f"备份 #{bid} 的结构不是有效 JSON: {exc}")
            if not isinstance(fields, list) or not fields:
                return _fail(f"备份 #{bid} 没有可回滚的字段结构")
            version = row["version"] or ""
            pn = row["pn"]
            model_key = row["model_key"]
            if not version or not pn or not model_key:
                return _fail(f"备份 #{bid} 缺少版本/PN/模型键，无法回滚")
            _ensure_version(conn, version, label=version, source="restore",
                            operator=operator, now=now)
            fields_json = json.dumps(fields, ensure_ascii=False)
            struct_checksum = constants._checksum(fields)  # noqa: SLF001
            existing = conn.execute(
                "SELECT id, category, description, unit FROM constant_model "
                "WHERE version = ? AND pn = ? AND model_key = ?",
                (version, pn, model_key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE constant_model SET name = ?, fields_json = ?, field_count = ?, "
                    "struct_checksum = ?, updated_at = ? WHERE id = ?",
                    (
                        row["name"] or model_key,
                        fields_json,
                        len(fields),
                        struct_checksum,
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO constant_model "
                    "(version, pn, product, subsystem, model_key, name, category, "
                    " description, unit, fields_json, field_count, struct_checksum, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version,
                        pn,
                        row["product"] or "",
                        row["subsystem"] or "",
                        model_key,
                        row["name"] or model_key,
                        "",
                        "",
                        "",
                        fields_json,
                        len(fields),
                        struct_checksum,
                        now,
                    ),
                )
            conn.execute("UPDATE const_backup SET restored_at = ? WHERE id = ?", (now, bid))
            _refresh_version(conn, version)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"回滚失败（已回滚事务，库未改动）: {exc}")
    finally:
        conn.close()
    return {
        "success": True,
        "backup_id": bid,
        "version": version,
        "pn": pn,
        "model_key": model_key,
        "field_count": len(fields),
        "restored_at": now,
        "message": (
            f"已把 {pn}/{model_key}（版本 {version}）回滚到备份 #{bid}，"
            f"{len(fields)} 个字段"
            + (f"；操作人 {operator}" if operator else "")
        ),
    }


# ---------------------------------------------------------------- HTTP 路由


def _decode_base64(raw: Any) -> bytes:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("缺少 content_base64（请求体需带 zip 的 Base64 内容）")
    if text.startswith("data:"):
        head, _, text = text.partition(",")
        if ";base64" not in head:
            raise ValueError("content_base64 不是 Base64 数据 URI")
    text = "".join(text.split())  # 容忍换行/空格（例如 encodebytes 的输出）
    if not text:
        raise ValueError("content_base64 为空")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("content_base64 不是有效的 Base64 编码")


def register_routes(app, base_dir, db_path=None) -> None:
    """挂载 /api/v1/constants/{import/full,import/partial,backups,backups/{id}/restore}。"""
    global _BASE_DIR, _DB_PATH
    _BASE_DIR = Path(base_dir) if base_dir else None
    db_file = Path(db_path) if db_path else Path(ate_db.DB_PATH)
    _DB_PATH = db_file
    try:
        _ensure_db(db_file)
    except Exception as exc:  # pragma: no cover - 建库失败不该阻断挂载
        print(f"[warn] 常数导入模块建库失败: {exc}")

    from fastapi import Body

    def _body(payload: Any) -> dict:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    @app.post("/api/v1/constants/import/full")
    def const_import_full(payload: Any = Body(default=None)):
        try:
            body = _body(payload)
            blob = _decode_base64(body.get("content_base64"))
            return import_full(
                blob,
                _text(body.get("filename"), 200),
                _text(body.get("operator"), 64),
                db_path=db_file,
            )
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - 不外抛裸异常
            return _fail(f"全量导入失败: {exc}")

    @app.post("/api/v1/constants/import/partial")
    def const_import_partial(payload: Any = Body(default=None)):
        try:
            body = _body(payload)
            blob = _decode_base64(body.get("content_base64"))
            return import_partial(
                blob,
                _text(body.get("filename"), 200),
                _text(body.get("operator"), 64),
                db_path=db_file,
            )
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _fail(f"部分导入失败: {exc}")

    @app.get("/api/v1/constants/backups")
    def const_backups(pn: str = "", model_key: str = "", limit: str = "100"):
        try:
            return list_backups(pn, model_key, limit, db_path=db_file)
        except Exception as exc:  # noqa: BLE001
            return _fail(f"读取备份列表失败: {exc}", items=[], count=0, total=0)

    @app.post("/api/v1/constants/backups/{backup_id}/restore")
    def const_backup_restore(backup_id: str, payload: Any = Body(default=None)):
        try:
            body = _body(payload)
            return restore_backup(
                backup_id, _text(body.get("operator"), 64), db_path=db_file
            )
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _fail(f"回滚失败: {exc}")
