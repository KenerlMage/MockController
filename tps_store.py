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
    return {
        "id": raw.get("id", ""),
        "name": raw.get("name", ""),
        "version": raw.get("version", ""),
        "schema": raw.get("schema") or ("tps.v2" if tps_schema.is_v2(raw) else "tps.v1"),
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


def _validate(raw: dict) -> None:
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


def save(raw: dict, overwrite: bool = True) -> dict:
    _validate(raw)
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
    return _summary(payload)


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


def read_file(tps_id: str, relative: str) -> bytes:
    root = _dir(tps_id).resolve()
    target = (root / relative).resolve()
    if not str(target).startswith(str(root)):
        raise TpsError(f"路径越界：{relative}")
    if not target.is_file():
        raise TpsError(f"文件不存在：{relative}")
    return target.read_bytes()


def stats() -> dict:
    items = list_tps()
    return {
        "count": len(items),
        "with_constants": sum(1 for i in items if i["has_constants"]),
        "with_deploy": sum(1 for i in items if i["deploy_count"]),
        "root": str(TPS_ROOT),
    }
