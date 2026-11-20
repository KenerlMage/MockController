"""装备助手货架：Controller 侧的助手清单与文件分发。

职责：
1. 维护货架清单（每个助手一条元数据 + 文件列表），供 Runner 拉取；
2. 按清单下发助手包内单个文件的原始字节，Runner 侧按 sha256 核对；
3. 接收发布（元数据 + 文件 base64 全量提交）与下架。

目录结构：
    <ASSIST_ROOT>/<id>/manifest.json        # 元数据 + files 列表（唯一事实源）
    <ASSIST_ROOT>/<id>/files/<相对路径>      # 助手包全量文件
    <ASSIST_ROOT>/shelf.json                # 货架级信息：来源 / 渠道 / 更新时间 / 备注

设计要点：
- 元数据只落原始字段，size_bytes / file_count / icon 由 files 列表实时统计，
  避免清单里出现两处会互相打架的体积与文件数；
- 读取文件一律 resolve + relative_to 校验，且必须出现在 files 列表中，
  既防路径穿越，也避免把 manifest.json 之类非包内文件漏出去；
- 发布先写临时目录、算完 sha256 再整体替换，货架上不会出现半成品助手；
- 助手 id 与包内路径都按白名单校验，与 Runner 侧货架规则保持一致。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

_HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(
    os.environ.get("MOCK_CONTROLLER_REPO")
    or os.environ.get("ATE_CONTROLLER_REPO")
    or (_HERE / "repo")
).resolve()
ASSIST_ROOT = Path(
    os.environ.get("MOCK_CONTROLLER_ASSISTANTS_DIR")
    or os.environ.get("ATE_CONTROLLER_ASSISTANTS_DIR")
    or (REPO_ROOT / "assistants")
).resolve()

# 货架渠道：Controller 是正式发布通道，Runner 迁移前的本地清单渠道为 demo
DEFAULT_CHANNEL = "stable"
DEFAULT_ICON = "icon.png"

ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")

# 按后缀给 Content-Type；未列出的统一按二进制流
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".zip": "application/zip",
}

# 发布时允许写入清单的元数据字段（其余键不落盘，避免脏数据扩散）
META_FIELDS = (
    "name",
    "version",
    "vendor",
    "summary",
    "description",
    "tags",
    "requires",
    "release_note",
    "published_at",
    "entry",
    "size_hint",
    "self_contained",
    "icon_file",
)


class AssistError(Exception):
    """可读的货架存储错误。"""


class AssistConflict(AssistError):
    """同 id 已存在且不允许覆盖。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fmt_size(size_bytes: int) -> str:
    """人读体积：与 Runner 货架的 9 KB / 86 KB / 1.2 MB 写法一致。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    mb = size_bytes / (1024 * 1024)
    if mb < 1:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{mb:.1f} MB"


def ensure_root() -> None:
    ASSIST_ROOT.mkdir(parents=True, exist_ok=True)


def safe_id(raw: str) -> str:
    """供迁移脚本复用：与接口内部同一套 id 规则。"""
    return _safe_id(raw)


def fmt_size(size_bytes: int) -> str:
    """供迁移脚本复用：与接口内部同一套体积写法。"""
    return _fmt_size(size_bytes)


def _safe_id(raw: str) -> str:
    text = str(raw or "").strip()
    if not ID_RE.match(text) or ".." in text:
        raise AssistError(f"助手标识只允许字母、数字、下划线、点与短横线：{raw}")
    return text


def _safe_rel(raw: str) -> str:
    """包内相对路径：不允许盘符、绝对路径与 .. 段。"""
    text = str(raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        raise AssistError("文件路径不能为空")
    if "\x00" in text:
        raise AssistError(f"文件路径含非法字符：{raw}")
    if text.startswith("/") or ":" in text.split("/")[0]:
        raise AssistError(f"文件路径必须是包内相对路径：{raw}")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AssistError(f"文件路径含非法片段：{raw}")
    return text


def _dir(assistant_id: str) -> Path:
    return ASSIST_ROOT / _safe_id(assistant_id)


def _read_manifest(assistant_id: str) -> dict:
    path = _dir(assistant_id) / "manifest.json"
    if not path.is_file():
        raise AssistError(f"助手不存在：{assistant_id}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssistError(f"助手清单损坏：{assistant_id}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise AssistError(f"助手清单格式不对：{assistant_id}")
    return raw


def _read_shelf() -> dict:
    """货架级信息；缺失时按现有清单推导，保证接口字段始终齐全。"""
    path = ASSIST_ROOT / "shelf.json"
    raw: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            raw = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
    updated = str(raw.get("updated_at") or "")
    if not updated:
        stamps = [
            str(item.get("updated_at") or "")
            for item in (_load_all() or [])
            if item.get("updated_at")
        ]
        updated = max(stamps) if stamps else ""
    return {
        "source": "controller",
        "channel": str(raw.get("channel") or DEFAULT_CHANNEL),
        "updated_at": updated,
        "note": str(raw.get("note") or ""),
    }


def write_shelf(channel: str = "", note: str = "") -> dict:
    """由迁移脚本调用：写入货架级信息（渠道 / 备注 / 更新时间）。"""
    ensure_root()
    meta = {
        "source": "controller",
        "channel": str(channel or DEFAULT_CHANNEL),
        "updated_at": _now(),
        "note": str(note or ""),
    }
    (ASSIST_ROOT / "shelf.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def _scan_files(files_dir: Path) -> list[dict]:
    """扫描归一化目录，返回 [{path, size, sha256}]，按路径排序。"""
    result: list[dict] = []
    if not files_dir.is_dir():
        return result
    for path in sorted(files_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(files_dir).as_posix()
        result.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return result


def _normalize_meta(raw: dict) -> dict:
    """只保留契约字段；tags 统一成字符串数组。"""
    meta: dict = {}
    for key in META_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "tags":
            meta[key] = [str(item).strip() for item in (value or []) if str(item).strip()]
        elif key == "self_contained":
            meta[key] = bool(value)
        else:
            meta[key] = "" if value is None else str(value).strip()
    return meta


def _make_manifest(assistant_id: str, meta: dict, files: list[dict]) -> dict:
    """由元数据与文件列表生成清单：体积、文件数、icon 都在这里一次算定。"""
    paths = {item["path"] for item in files}
    size_bytes = sum(int(item["size"]) for item in files)
    entry = str(meta.get("entry") or "").strip()
    if entry:
        entry = _safe_rel(entry)
        if entry not in paths:
            raise AssistError(f"entry 不在文件清单中：{entry}")
    icon_file = str(meta.get("icon_file") or DEFAULT_ICON).strip() or DEFAULT_ICON
    return {
        "id": assistant_id,
        "name": str(meta.get("name") or assistant_id),
        "version": str(meta.get("version") or ""),
        "vendor": str(meta.get("vendor") or ""),
        "summary": str(meta.get("summary") or ""),
        "description": str(meta.get("description") or ""),
        "tags": meta.get("tags") or [],
        "requires": str(meta.get("requires") or ""),
        "release_note": str(meta.get("release_note") or ""),
        "published_at": str(meta.get("published_at") or ""),
        "entry": entry,
        "size_hint": str(meta.get("size_hint") or "") or _fmt_size(size_bytes),
        "self_contained": bool(meta.get("self_contained")),
        "icon_file": icon_file,
        "updated_at": _now(),
        "files": files,
    }


def _summary(manifest: dict) -> dict:
    """单条助手摘要：契约里除 files 之外的全部字段。"""
    files = manifest.get("files") or []
    icon_file = str(manifest.get("icon_file") or DEFAULT_ICON)
    return {
        "id": str(manifest.get("id") or ""),
        "name": str(manifest.get("name") or ""),
        "version": str(manifest.get("version") or ""),
        "vendor": str(manifest.get("vendor") or ""),
        "summary": str(manifest.get("summary") or ""),
        "description": str(manifest.get("description") or ""),
        "tags": manifest.get("tags") or [],
        "requires": str(manifest.get("requires") or ""),
        "release_note": str(manifest.get("release_note") or ""),
        "published_at": str(manifest.get("published_at") or ""),
        "entry": str(manifest.get("entry") or ""),
        "size_hint": str(manifest.get("size_hint") or ""),
        "size_bytes": sum(int(item.get("size") or 0) for item in files),
        "file_count": len(files),
        "self_contained": bool(manifest.get("self_contained")),
        "icon": any(item.get("path") == icon_file for item in files),
        "updated_at": str(manifest.get("updated_at") or ""),
    }


def _load_all() -> list[dict]:
    """扫描货架目录，返回按 id 排序的清单（跳过 _ 前缀的临时与归档目录）。"""
    if not ASSIST_ROOT.is_dir():
        return []
    result: list[dict] = []
    for entry in sorted(ASSIST_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        try:
            manifest = _read_manifest(entry.name)
        except AssistError:
            continue
        result.append(manifest)
    return sorted(result, key=lambda item: str(item.get("id") or ""))


def list_assistants() -> list[dict]:
    return [_summary(manifest) for manifest in _load_all()]


def stats() -> dict:
    return {"count": len(_load_all())}


def detail(assistant_id: str) -> dict:
    manifest = _read_manifest(assistant_id)
    item = _summary(manifest)
    item["files"] = manifest.get("files") or []
    return item


def read_file(assistant_id: str, relative: str) -> tuple[bytes, str]:
    """读取包内文件；必须出现在清单 files 中，且不得越出货架目录。"""
    manifest = _read_manifest(assistant_id)
    rel = _safe_rel(relative)
    known = {str(item.get("path") or "") for item in (manifest.get("files") or [])}
    if rel not in known:
        raise AssistError(f"文件不在清单中：{assistant_id}/{rel}")
    root = _dir(assistant_id).resolve()
    target = (root / "files" / rel).resolve()
    if not str(target).startswith(str(root) + os.sep):
        raise AssistError(f"路径越界：{relative}")
    if not target.is_file():
        raise AssistError(f"文件不存在：{relative}")
    return target.read_bytes(), MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")


def staging_dir(assistant_id: str) -> Path:
    """在货架目录内建临时目录：与目标同盘，便于整体替换。"""
    ensure_root()
    _safe_id(assistant_id)
    return Path(tempfile.mkdtemp(prefix=".staging-", dir=str(ASSIST_ROOT)))


def commit(assistant_id: str, staging: Path, meta: dict) -> dict:
    """把 staging/files 提交为正式助手目录，返回详情。"""
    files = _scan_files(staging / "files")
    if not files:
        shutil.rmtree(staging, ignore_errors=True)
        raise AssistError("助手文件清单为空")
    manifest = _make_manifest(assistant_id, _normalize_meta(meta), files)
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    target = _dir(assistant_id)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    return detail(assistant_id)


def publish(raw_meta: dict, files: list[dict], overwrite: bool = True) -> dict:
    """发布/覆盖一个助手。files 为 [{path, content_base64}]。"""
    if not isinstance(raw_meta, dict):
        raise AssistError("assistant 必须是对象")
    assistant_id = _safe_id(str(raw_meta.get("id") or ""))
    target = _dir(assistant_id)
    if target.exists() and not overwrite:
        raise AssistConflict(f"助手已存在：{assistant_id}")

    staging = staging_dir(assistant_id)
    try:
        files_dir = staging / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        for item in files:
            rel = _safe_rel(item.get("path") or "")
            if rel in seen:
                raise AssistError(f"文件路径重复：{rel}")
            seen.add(rel)
            try:
                cleaned = re.sub(r"\s+", "", item.get("content_base64") or "")
                payload = base64.b64decode(cleaned, validate=True)
            except Exception as exc:  # noqa: BLE001
                raise AssistError(f"文件 base64 解码失败：{rel}（{exc}）") from exc
            dest = files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
        return commit(assistant_id, staging, raw_meta)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def delete(assistant_id: str) -> dict:
    """下架：物理删除助手目录，返回文件数用于回执。"""
    manifest = _read_manifest(assistant_id)
    target = _dir(assistant_id)
    count = len(manifest.get("files") or [])
    shutil.rmtree(target)
    return {
        "ok": True,
        "message": f"已删除助手 {assistant_id}（{count} 个文件）",
    }


# ==================== 路由 ====================


class AssistantFileIn(BaseModel):
    path: str
    content_base64: str = ""


class AssistantPublish(BaseModel):
    assistant: dict[str, Any]
    files: list[AssistantFileIn] = []
    overwrite: bool = True


def register_routes(app: FastAPI, repo_root: Path) -> None:
    global ASSIST_ROOT
    ASSIST_ROOT = (Path(repo_root) / "assistants").resolve()
    ASSIST_ROOT.mkdir(parents=True, exist_ok=True)

    @app.get("/api/v1/assistants")
    def list_assistants_route() -> dict:
        shelf = _read_shelf()
        items = list_assistants()
        return {
            "ok": True,
            "count": len(items),
            **shelf,
            "assistants": items,
        }

    @app.get("/api/v1/assistants/{assistant_id}")
    def detail_route(assistant_id: str) -> dict:
        try:
            return {"ok": True, "assistant": detail(assistant_id)}
        except AssistError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/assistants/{assistant_id}/files/{path:path}")
    def file_route(assistant_id: str, path: str) -> Response:
        try:
            data, media_type = read_file(assistant_id, path)
        except AssistError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=data, media_type=media_type)

    @app.post("/api/v1/assistants")
    def publish_route(req: AssistantPublish) -> dict:
        try:
            result = publish(
                req.assistant,
                [item.model_dump() for item in req.files],
                overwrite=req.overwrite,
            )
        except AssistConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AssistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "assistant": result}

    @app.delete("/api/v1/assistants/{assistant_id}")
    def delete_route(assistant_id: str) -> dict:
        try:
            return delete(assistant_id)
        except AssistError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
