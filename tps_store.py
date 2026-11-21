"""TPS 存储：Controller 侧的唯一事实源。

目录结构：
    <TPS_ROOT>/<tps_id>/tps.json        # 清单
    <TPS_ROOT>/<tps_id>/testcase/       # 用例实现（zip 导入时携带）
    <TPS_ROOT>/<tps_id>/resource/       # 人工操作提示等附带文件

设计要点：
- 清单以文件形式保存，便于 Runner 侧运行副本直接落盘；
- 校验复用 `tps_schema`（与 Runner 现有校验同源），不接受未通过校验的清单；
- 版本不可覆盖：同一 id 再次保存时若版本不同，需显式 overwrite；
- 列表返回摘要，不返回完整步骤，避免大清单拖慢页面。
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import tps_schema

REPO_ROOT = Path(
    os.environ.get("MOCK_CONTROLLER_REPO")
    or os.environ.get("ATE_CONTROLLER_REPO")
    or (Path(__file__).resolve().parent / "repo")
).resolve()
TPS_ROOT = Path(
    os.environ.get("MOCK_CONTROLLER_TPS_DIR")
    or os.environ.get("ATE_CONTROLLER_TPS_DIR")
    or (REPO_ROOT / "tps")
).resolve()

ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
# 编辑器可写文件的类型（其余一律拒绝，避免把可执行物塞进 TPS 目录）
ALLOWED_FILE_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml"}
MAX_FILE_BYTES = 2 * 1024 * 1024
# 允许删除的目录（tps.json 等清单本体不在此列）
DELETABLE_ROOTS = {"testcase", "resource"}


class TpsError(Exception):
    """可读的 TPS 存储错误。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(tps_id: str) -> str:
    text = (tps_id or "").strip()
    if not ID_RE.match(text):
        raise TpsError(f"TPS 编号只允许字母、数字、下划线、点与短横线：{tps_id}")
    return text


def _dir(tps_id: str) -> Path:
    return TPS_ROOT / _safe_id(tps_id)


def ensure_root() -> None:
    TPS_ROOT.mkdir(parents=True, exist_ok=True)


def _summary(raw: dict) -> dict:
    constant = raw.get("constant_model") or {}
    models = constant.get("models") or []
    deploy = raw.get("deploy") or []
    parser = raw.get("parser") or {}
    parser_name = parser.get("name") if isinstance(parser, dict) else str(parser or "")
    process = raw.get("process") or {}
    device_config = raw.get("device_config") or {}
    return {
        "id": raw.get("id", ""),
        "name": raw.get("name", ""),
        "version": raw.get("version", ""),
        "schema": raw.get("schema") or ("tps.v2" if tps_schema.is_v2(raw) else "tps.v1"),
        "preset_id": raw.get("preset_id", ""),
        "product": raw.get("product", ""),
        "subsystem": raw.get("subsystem", ""),
        "parser": parser_name,
        "process_id": (process or {}).get("id", "") if isinstance(process, dict) else "",
        "process_name": (process or {}).get("name", "") if isinstance(process, dict) else "",
        "device_count": len(device_config) if isinstance(device_config, dict) else 0,
        "project": raw.get("project", ""),
        "dut": raw.get("dut", ""),
        "description": raw.get("description", ""),
        "cmd_suit_count": len(raw.get("cmd_suit") or []),
        "step_count": len(raw.get("steps") or []),
        "has_constants": bool(models),
        "constant_model_count": len(models),
        "deploy_count": len(deploy) if isinstance(deploy, list) else 0,
        "updated_at": raw.get("updated_at", ""),
    }


def list_tps() -> list[dict]:
    ensure_root()
    result: list[dict] = []
    for path in sorted(TPS_ROOT.glob("*/tps.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw.get("id"):
            result.append(_summary(raw))
    return result


def load(tps_id: str) -> dict:
    path = _dir(tps_id) / "tps.json"
    if not path.is_file():
        raise TpsError(f"TPS 不存在：{tps_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(raw: dict) -> list[str]:
    """结构 + 引用校验；通过时返回警告列表，失败抛 TpsError。"""
    if not isinstance(raw, dict):
        raise TpsError("TPS 清单必须是对象")
    if not raw.get("id"):
        raise TpsError("缺少 id")
    _safe_id(str(raw["id"]))
    if not raw.get("name"):
        raise TpsError("缺少 name")
    if tps_schema.is_v2(raw):
        errors = tps_schema.validate_v2(raw)
    else:
        errors = []
        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("v1 清单需要非空 steps 数组")
    if errors:
        raise TpsError("；".join(errors))
    errors, warnings = reference_issues(raw)
    if errors:
        raise TpsError("；".join(errors))
    return warnings


def _tree_root() -> Path:
    """装备树目录：与 metadata 一致（ATE_TREE_DIR 优先，默认 <repo>/tree）。"""
    return Path(os.environ.get("ATE_TREE_DIR") or (REPO_ROOT / "tree"))


def _preset_bom_aliases(preset_id: str) -> Optional[set[str]]:
    """preset 存在时返回 BOM 别名集合，不存在（或装备树不可读）返回 None。

    直接读 tree.json / bom/*.json：存储层不依赖 metadata 的运行期注入路径。
    """
    root = _tree_root()
    try:
        store = json.loads((root / "tree.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    presets = store.get("preset_types") if isinstance(store, dict) else None
    if not any(isinstance(t, dict) and t.get("id") == preset_id for t in presets or []):
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", preset_id[:64]) or "unnamed"
    try:
        data = json.loads((root / "bom" / f"{safe}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    items = data.get("items") if isinstance(data, dict) else data
    aliases = {
        str(item.get("alias") or item.get("id") or "").strip()
        for item in items or []
        if isinstance(item, dict)
    }
    return {a for a in aliases if a}


def reference_issues(raw: dict) -> tuple[list[str], list[str]]:
    """校验 parser / process / preset 引用；返回 (错误列表, 警告列表)。

    错误阻断保存（400），警告（device_config 中 BOM 之外的别名）仅提示。
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        import processes
        import tps_scaffold
    except Exception as exc:  # noqa: BLE001 - 模块缺失时不做引用校验
        return errors, [f"引用校验已跳过：{exc}"]

    parser = raw.get("parser")
    if isinstance(parser, dict) and str(parser.get("name") or "").strip():
        name = str(parser["name"]).strip()
        allowed = tps_scaffold.parser_names()
        if name not in allowed:
            errors.append(f"parser.name 不在解析器清单内：{name}（可选：{'、'.join(allowed)}）")
    elif isinstance(parser, str) and parser.strip() and parser.strip() not in tps_scaffold.parser_names():
        errors.append(
            f"parser 不在解析器清单内：{parser}（可选：{'、'.join(tps_scaffold.parser_names())}）"
        )

    process = raw.get("process")
    if isinstance(process, dict) and str(process.get("id") or "").strip():
        pid = str(process["id"]).strip()
        if not processes.exists(pid):
            errors.append(f"process.id 引用了不存在的工序：{pid}")

    preset_id = str(raw.get("preset_id") or "").strip()
    if preset_id:
        aliases = _preset_bom_aliases(preset_id)
        if aliases is None:
            errors.append(f"preset_id 在装备树中不存在：{preset_id}")
        else:
            device_config = raw.get("device_config")
            if isinstance(device_config, dict):
                extra = [str(k) for k in device_config if str(k) not in aliases]
                if extra:
                    warnings.append(
                        f"device_config 含预设 {preset_id} 的 BOM 之外的别名：{'、'.join(sorted(extra))}"
                    )
    return errors, warnings


def save(raw: dict, overwrite: bool = True) -> dict:
    warnings = _validate(raw)
    tps_id = _safe_id(str(raw["id"]))
    target = _dir(tps_id)
    if target.exists() and not overwrite:
        raise TpsError(f"TPS 已存在：{tps_id}")
    target.mkdir(parents=True, exist_ok=True)

    payload = dict(raw)
    payload["updated_at"] = _now()
    (target / "tps.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = _summary(payload)
    if warnings:
        summary["warnings"] = warnings
    return summary


def import_json(content: str, overwrite: bool = True) -> dict:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TpsError(f"JSON 解析失败：{exc}") from exc
    return save(raw, overwrite=overwrite)


def import_zip(name: str, content_base64: str, overwrite: bool = True) -> dict:
    try:
        blob = base64.b64decode(content_base64)
    except Exception as exc:  # noqa: BLE001
        raise TpsError(f"压缩包解码失败：{exc}") from exc

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise TpsError("不是合法的 zip 压缩包") from exc

    names = [n.replace("\\", "/") for n in zf.namelist()]
    manifest_name = next((n for n in names if n == "tps.json" or n.endswith("/tps.json")), "")
    if not manifest_name:
        raise TpsError("压缩包内缺少 tps.json")

    raw = json.loads(zf.read(manifest_name).decode("utf-8"))
    _validate(raw)

    tps_id = _safe_id(str(raw["id"]))
    target = _dir(tps_id)
    if target.exists() and not overwrite:
        raise TpsError(f"TPS 已存在：{tps_id}")

    # 允许压缩包自带一层根目录
    prefix = manifest_name[: -len("tps.json")]
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    for member in zf.namelist():
        norm = member.replace("\\", "/")
        if not norm.startswith(prefix) or norm.endswith("/"):
            continue
        relative = norm[len(prefix):]
        if not relative or ".." in relative.split("/"):
            continue
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(member))

    payload = json.loads((target / "tps.json").read_text(encoding="utf-8"))
    payload["updated_at"] = _now()
    (target / "tps.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return _summary(payload)


def list_files(tps_id: str) -> list[str]:
    root = _dir(tps_id)
    if not root.is_dir():
        raise TpsError(f"TPS 不存在：{tps_id}")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result.append(path.relative_to(root).as_posix())
    return result


def _inside(tps_id: str, relative: str) -> Path:
    """把 TPS 内相对路径解析成绝对路径；拒绝绝对路径与 `..` 穿越。"""
    root = _dir(tps_id).resolve()
    raw = str(relative or "").replace("\\", "/").strip()
    if not raw:
        raise TpsError("缺少文件路径")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise TpsError(f"只接受 TPS 目录内的相对路径：{relative}")
    if ".." in raw.split("/"):
        raise TpsError(f"路径越界：{relative}")
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise TpsError(f"路径越界：{relative}") from exc
    return target


def read_file(tps_id: str, relative: str) -> bytes:
    target = _inside(tps_id, relative)
    if not target.is_file():
        raise TpsError(f"文件不存在：{relative}")
    return target.read_bytes()


def write_file(tps_id: str, relative: str, content: str, encoding: str = "utf-8") -> dict:
    """写入代码 / 文档文件：限制后缀、大小与编码，自动建父目录。"""
    root = _dir(tps_id)
    if not root.is_dir():
        raise TpsError(f"TPS 不存在：{tps_id}")
    target = _inside(tps_id, relative)

    suffix = target.suffix.lower()
    if suffix not in ALLOWED_FILE_SUFFIXES:
        raise TpsError(
            f"不支持的文件类型：{suffix or '无后缀'}"
            f"（只允许 {'、'.join(sorted(ALLOWED_FILE_SUFFIXES))}）"
        )

    enc = str(encoding or "utf-8").strip().lower()
    if enc in ("utf8", "utf-8"):
        enc = "utf-8"
    else:
        raise TpsError(f"encoding 只支持 utf-8：{encoding}")
    if not isinstance(content, str):
        raise TpsError("content 必须是字符串")
    try:
        data = content.encode(enc)
    except UnicodeEncodeError as exc:
        raise TpsError(f"content 无法按 {enc} 编码：{exc}") from exc
    if len(data) > MAX_FILE_BYTES:
        raise TpsError(f"文件超过 2MB 上限（实际 {len(data)} 字节）")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {
        "path": target.relative_to(root.resolve()).as_posix(),
        "size": len(data),
        "updated_at": _now(),
    }


def delete_file(tps_id: str, relative: str) -> str:
    """删除 testcase/ 、resource/ 下的文件；清单与目录结构不动。"""
    root = _dir(tps_id)
    if not root.is_dir():
        raise TpsError(f"TPS 不存在：{tps_id}")
    target = _inside(tps_id, relative)
    rel = target.relative_to(root.resolve()).as_posix()
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] not in DELETABLE_ROOTS:
        raise TpsError(f"只允许删除 {'、'.join(sorted(DELETABLE_ROOTS))} 下的文件：{relative}")
    if not target.is_file():
        raise TpsError(f"文件不存在：{relative}")
    target.unlink()
    return f"已删除 {rel}"


def exists(tps_id: str) -> bool:
    """TPS 目录与 tps.json 是否都在。"""
    try:
        return (_dir(tps_id) / "tps.json").is_file()
    except TpsError:
        return False


def stats() -> dict:
    items = list_tps()
    return {
        "count": len(items),
        "with_constants": sum(1 for i in items if i["has_constants"]),
        "with_deploy": sum(1 for i in items if i["deploy_count"]),
        "root": str(TPS_ROOT),
    }
