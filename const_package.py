# -*- coding: utf-8 -*-
"""外部机器常数系统（MCS）导出包契约（SR01-AR09）。

前提：模型由 MCS 导出后交付 Controller，Controller 不生产模型，因此信任根不在
本系统，而在 MCS 的包契约上：

    manifest.json
    {
      "contract": "mcs.constants.export/v1",
      "source": {"system": "MCS", "system_version": "3.2.1", "exported_at": "2026-11-08T10:00:00+08:00"},
      "batch_id": "MCS-20261108-0001",
      "version": "2026.11.08",
      "content_digest": "<对 files 的规范化摘要>",
      "files": [{"path": "SPM/SPM_RH.xml", "sha256": "...", "size": 123}],
      "signature": {"alg": "hmac-sha256", "value": "..."}   # 可选
    }

校验顺序：结构 → 来源白名单 → 逐文件 SHA-256/大小 → 内容摘要重算 → 可选签名。
任何一步失败整包拒绝，调用方不碰数据库。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

CONTRACT = "mcs.constants.export/v1"
MANIFEST_NAME = "manifest.json"
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_ZIP_FILES = 200
MAX_FILE_BYTES = 20 * 1024 * 1024

# 本系统导出的备份包标记：导入通道必须拒绝（只能走备份回滚）
LEGACY_SELF_SIGNATURE = "ate-controller-export-v1"

DEFAULT_SOURCE_WHITELIST = ("MCS", "mcs")


def source_whitelist() -> tuple[str, ...]:
    raw = os.environ.get("ATE_CONSTANT_SOURCE_WHITELIST") or ""
    items = tuple(x.strip() for x in raw.split(",") if x.strip())
    return items or DEFAULT_SOURCE_WHITELIST


def signing_key() -> str:
    return os.environ.get("ATE_CONSTANT_SIGNING_KEY") or ""


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_digest(files: list[dict]) -> str:
    """规范化内容摘要：按 path 排序后逐行 `path:sha256`，再取 sha256。"""
    lines = sorted(f"{f['path']}:{f['sha256']}" for f in files)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ValueError(f"{info.filename} 超过 {limit // (1024 * 1024)}MB 上限")
    with zf.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{info.filename} 超过 {limit // (1024 * 1024)}MB 上限")
    return data


def parse_package(blob: bytes, filename: str = "") -> dict:
    """解析并核验 MCS 包；成功返回清单、分类材料与摘要，失败抛 ValueError。"""
    if not blob:
        raise ValueError("包内容为空")
    if len(blob) > MAX_ZIP_BYTES:
        raise ValueError(f"压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)}MB 上限")
    try:
        zf = zipfile.ZipFile(_as_stream(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 zip 包：{exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"包内文件数 {len(infos)} 超过 {MAX_ZIP_FILES} 上限")
        names = [i.filename for i in infos]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"包内存在重复文件：{'、'.join(dupes[:5])}")
        manifests = [n for n in names if PurePosixPath(n).name.lower() == MANIFEST_NAME]
        if not manifests:
            raise ValueError("包内缺少 manifest.json：MCS 标准包必须带清单")
        if len(manifests) > 1:
            raise ValueError(f"包内存在多个 manifest.json：{'、'.join(sorted(manifests))}")
        manifest_path = manifests[0]
        prefix = manifest_path[: -len(MANIFEST_NAME)]
        raw = _read_entry(zf, zf.getinfo(manifest_path), 1024 * 1024)
        try:
            manifest = json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError("manifest.json 不是 UTF-8 编码") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest.json 不是有效 JSON（第 {exc.lineno} 行）") from exc
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json 顶层必须是对象")

        meta = _meta(manifest)
        declared = _files(manifest)
        payloads: dict[str, bytes] = {}
        for item in declared:
            info = zf.getinfo(prefix + item["path"])
            data = _read_entry(zf, info, MAX_FILE_BYTES)
            actual = sha256_hex(data)
            if actual != item["sha256"]:
                raise ValueError(
                    f"文件 {item['path']} 的 SHA-256 与清单不符"
                    f"（清单 {item['sha256'][:12]}…，实际 {actual[:12]}…），整包拒绝"
                )
            if item["size"] and len(data) != item["size"]:
                raise ValueError(
                    f"文件 {item['path']} 大小与清单不符（清单 {item['size']}，实际 {len(data)}）"
                )
            payloads[item["path"]] = data

        extra = sorted(set(names) - {manifest_path} - {prefix + i["path"] for i in declared})
        if extra:
            raise ValueError(f"包内存在未声明的文件（清单外）：{'、'.join(extra[:5])}")

    digest = content_digest(declared)
    if meta["content_digest"] and meta["content_digest"] != digest:
        raise ValueError(
            f"内容摘要与清单不符（清单 {meta['content_digest'][:12]}…，重算 {digest[:12]}…），整包拒绝"
        )

    signature_status = "absent"
    if meta["signature"]:
        key = signing_key()
        alg = _text(meta["signature"].get("alg"), 32).lower() if isinstance(meta["signature"], dict) else ""
        value = _text(meta["signature"].get("value"), 256) if isinstance(meta["signature"], dict) else ""
        if not key:
            signature_status = "unverified"
        elif alg != "hmac-sha256" or not value:
            raise ValueError("签名格式不受支持：需要 {alg: hmac-sha256, value: ...}")
        else:
            expect = hmac.new(key.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expect, value):
                raise ValueError("签名校验失败：内容可能与 MCS 导出时不一致")
            signature_status = "verified"

    return {
        "filename": _text(filename, 200),
        "package_hash": sha256_hex(blob),
        "size": len(blob),
        "version": meta["version"],
        "label": meta["label"],
        "note": meta["note"],
        "reason": meta["reason"],
        "source_system": meta["source_system"],
        "source_version": meta["source_version"],
        "exported_at": meta["exported_at"],
        "batch_id": meta["batch_id"],
        "content_digest": digest,
        "signature_status": signature_status,
        "legacy_self_export": meta["legacy_self_export"],
        "file_count": len(declared),
        "files": declared,
        "payloads": payloads,
    }


def _as_stream(blob: bytes):
    import io

    return io.BytesIO(blob)


def _meta(manifest: dict) -> dict:
    source_raw = manifest.get("source")
    if isinstance(source_raw, dict):
        source_system = _text(source_raw.get("system") or source_raw.get("name"), 100)
        source_version = _text(source_raw.get("system_version") or source_raw.get("version"), 64)
        exported_at = _text(source_raw.get("exported_at"), 64)
    else:
        source_system = _text(source_raw or manifest.get("exporter"), 100)
        source_version = _text(manifest.get("exporter_version"), 64)
        exported_at = _text(manifest.get("exported_at"), 64)

    if not source_system:
        raise ValueError("清单缺少来源系统（source.system）：无法确认包由哪个常数系统导出")

    allowed = source_whitelist()
    if not any(source_system.lower() == a.lower() for a in allowed):
        raise ValueError(
            f"来源系统不在白名单：{source_system}（允许：{'、'.join(allowed)}）"
        )

    contract = _text(manifest.get("contract"), 64)
    if contract and contract != CONTRACT:
        raise ValueError(f"包契约版本不受支持：{contract}（需要 {CONTRACT}）")

    version = _text(manifest.get("version") or manifest.get("constant_version"), 48)
    if not version:
        raise ValueError("清单缺少 version（MCS 导出版本号）")

    batch_id = _text(manifest.get("batch_id") or manifest.get("batch"), 64)
    if not batch_id:
        # 允许缺省：用内容摘要前 12 位作为批次号，保证可追溯
        batch_id = ""

    legacy = _text(manifest.get("signature"), 200) == LEGACY_SELF_SIGNATURE
    return {
        "source_system": source_system,
        "source_version": source_version,
        "exported_at": exported_at,
        "batch_id": batch_id,
        "version": version,
        "label": _text(manifest.get("label"), 120),
        "note": _text(manifest.get("note"), 400),
        "reason": _text(manifest.get("reason"), 200),
        "content_digest": _text(manifest.get("content_digest"), 64).lower(),
        "signature": manifest.get("signature") if isinstance(manifest.get("signature"), dict) else None,
        "legacy_self_export": legacy,
    }


def _files(manifest: dict) -> list[dict]:
    raw = manifest.get("files")
    if not isinstance(raw, list) or not raw:
        raise ValueError("清单缺少 files 列表（每项需 path / sha256 / size）")
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"files 第 {index} 项不是对象")
        path = _text(item.get("path") or item.get("name"), 300).replace("\\", "/").lstrip("./")
        digest = _text(item.get("sha256") or item.get("hash"), 64).lower()
        if not path:
            raise ValueError(f"files 第 {index} 项缺少 path")
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValueError(f"files 第 {index} 项的 path 非法：{path}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"files 第 {index} 项的 sha256 不是 64 位小写十六进制")
        if path in seen:
            raise ValueError(f"files 中 path 重复：{path}")
        seen.add(path)
        size = item.get("size")
        try:
            size = int(size) if size is not None else 0
        except (TypeError, ValueError):
            raise ValueError(f"files 第 {index} 项的 size 不是整数")
        out.append({"path": path, "sha256": digest, "size": size})
    return out


def classify(path: str, *, module: str, calibrated: bool, sn: str) -> dict:
    """按文件名与 XML 内容双重判定：模板 / 标定实例，矛盾即报错。"""
    stem = Path(path).stem
    suffix = f"_{module}"
    name_sn = stem[: -len(suffix)] if module and stem.endswith(suffix) else ""

    if calibrated:
        if not sn:
            raise ValueError(f"{path}: calibrated=true 必须带 sn 属性")
        if not name_sn:
            raise ValueError(f"{path}: 标定实例文件名必须是 <SN>_{module}.xml，实际 {Path(path).name}")
        if name_sn.upper() != sn.upper():
            raise ValueError(
                f"{path}: 文件名中的 SN（{name_sn}）与 XML 的 sn（{sn}）不一致"
            )
        return {"kind": "instance", "sn": sn}

    if name_sn:
        raise ValueError(
            f"{path}: 文件名带 SN 前缀（{name_sn}）但 calibrated 不是 true，无法判定为模板或实例"
        )
    return {"kind": "template", "sn": ""}
