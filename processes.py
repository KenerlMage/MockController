"""工序清单（mock）：与装备树同级的工序字典。

数据文件：`<REPO_ROOT>/processes.json`，首次访问自动落种 8 条中文工序
（覆盖装配 / 测试 / 老化 / 包装）。工序被 TPS 的 `process.id` 引用时不允许删除。

对外接口在 api_v1.py：
    GET    /api/v1/processes
    GET    /api/v1/processes/{id}
    POST   /api/v1/processes        # 按 id 新增或更新
    DELETE /api/v1/processes/{id}   # 被引用时 409
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 工序编号：字母/数字/点/下划线/短横线，最长 40（与 TPS 引用写法保持一致）
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

_LOCK = threading.RLock()

DEFAULT_PROCESSES: list[dict] = [
    {
        "id": "PRC-SMT-01",
        "code": "SMT-01",
        "name": "SMT 贴装",
        "stage": "装配",
        "description": "PCB 贴片与回流焊",
        "typical_minutes": 25,
        "product_scope": ["1000-A", "2000-B"],
    },
    {
        "id": "PRC-DIP-02",
        "code": "DIP-02",
        "name": "插件与波峰焊",
        "stage": "装配",
        "description": "通孔插件与波峰焊",
        "typical_minutes": 18,
        "product_scope": [],
    },
    {
        "id": "PRC-ICT-03",
        "code": "ICT-03",
        "name": "在线测试 ICT",
        "stage": "测试",
        "description": "飞针/针床在线測試",
        "typical_minutes": 12,
        "product_scope": ["1000-A"],
    },
    {
        "id": "PRC-FCT-04",
        "code": "FCT-04",
        "name": "功能测试 FCT",
        "stage": "测试",
        "description": "整机功能与接口测试",
        "typical_minutes": 40,
        "product_scope": ["1000-A", "2000-B", "3000-C"],
    },
    {
        "id": "PRC-AGING-05",
        "code": "AGING-05",
        "name": "高温老化",
        "stage": "老化",
        "description": "高温带电老化 4h",
        "typical_minutes": 240,
        "product_scope": [],
    },
    {
        "id": "PRC-CAL-06",
        "code": "CAL-06",
        "name": "标定与校准",
        "stage": "标定",
        "description": "机器常数回填与校验",
        "typical_minutes": 35,
        "product_scope": ["1000-A", "2000-B"],
    },
    {
        "id": "PRC-FQC-07",
        "code": "FQC-07",
        "name": "出货抽检 FQC",
        "stage": "质检",
        "description": "按 AQL 抽检外观与电性能",
        "typical_minutes": 20,
        "product_scope": [],
    },
    {
        "id": "PRC-PACK-08",
        "code": "PACK-08",
        "name": "包装入库",
        "stage": "包装",
        "description": "贴标、装盒、入库",
        "typical_minutes": 8,
        "product_scope": [],
    },
]


class ProcessError(Exception):
    """可读的工序清单错误；status_code 供路由层直接映射。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _repo_root() -> Path:
    """仓库根目录以 tps_store 为准（同一套环境变量 / 默认值）。"""
    import tps_store

    return tps_store.REPO_ROOT


def _path() -> Path:
    return _repo_root() / "processes.json"


def _seed_payload() -> dict:
    return {
        "version": 1,
        "updated_at": _now(),
        "processes": [dict(item, product_scope=list(item.get("product_scope") or [])) for item in DEFAULT_PROCESSES],
    }


def _write(data: dict) -> None:
    """原子写：先写 .tmp 再替换，避免写入中断损坏工序表。"""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load() -> dict:
    """读取工序表；文件缺失时按默认 8 条落种。"""
    with _LOCK:
        path = _path()
        if not path.is_file():
            _write(_seed_payload())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProcessError(f"工序清单读取失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ProcessError("工序清单格式错误：顶层必须是对象")
        items = data.get("processes")
        if not isinstance(items, list):
            data["processes"] = []
        data.setdefault("version", 1)
        return data


def _normalize(raw: dict, existing: Optional[dict] = None) -> dict:
    """合并请求字段并做必填 / 格式校验。"""
    base = dict(existing or {})
    for key in ("id", "code", "name", "stage", "description", "typical_minutes", "product_scope"):
        if key in raw:
            base[key] = raw[key]

    pid = str(base.get("id") or "").strip()
    if not ID_RE.match(pid):
        raise ProcessError("工序 id 只允许字母、数字、下划线、点与短横线（1-40 位）")
    name = str(base.get("name") or "").strip()
    if not name:
        raise ProcessError("工序 name 必填")

    minutes = base.get("typical_minutes")
    if minutes is None or minutes == "":
        minutes = None
    else:
        try:
            minutes = int(minutes)
        except (TypeError, ValueError) as exc:
            raise ProcessError(f"typical_minutes 必须是整数：{minutes!r}") from exc
        if minutes < 0:
            raise ProcessError("typical_minutes 不能为负")

    scope = base.get("product_scope") or []
    if not isinstance(scope, list):
        raise ProcessError("product_scope 必须是产品编号数组")

    return {
        "id": pid,
        "code": str(base.get("code") or "").strip(),
        "name": name,
        "stage": str(base.get("stage") or "").strip(),
        "description": str(base.get("description") or "").strip(),
        "typical_minutes": minutes,
        "product_scope": [str(x).strip() for x in scope if str(x).strip()],
    }


def list_processes() -> list[dict]:
    return [dict(x) for x in _load().get("processes") or [] if isinstance(x, dict)]


def get_process(process_id: str) -> Optional[dict]:
    pid = str(process_id or "").strip()
    for item in list_processes():
        if item.get("id") == pid:
            return item
    return None


def exists(process_id: str) -> bool:
    return get_process(process_id) is not None


def upsert_process(raw: dict) -> dict:
    """按 id 新增或更新；未给出的字段保留原值。"""
    with _LOCK:
        data = _load()
        items = [x for x in data.get("processes") or [] if isinstance(x, dict)]
        raw_id = str((raw or {}).get("id") or "").strip()
        existing = next((x for x in items if x.get("id") == raw_id), None)
        item = _normalize(raw or {}, existing)
        if existing is None:
            items.append(item)
        else:
            items[items.index(existing)] = item
        data["processes"] = items
        data["updated_at"] = _now()
        _write(data)
        return dict(item)


def _tps_root() -> Path:
    import tps_store

    return tps_store.TPS_ROOT


def references(process_id: str) -> list[str]:
    """引用该工序的 TPS 编号列表。"""
    pid = str(process_id or "").strip()
    root = _tps_root()
    if not pid or not root.is_dir():
        return []
    found: list[str] = []
    for path in sorted(root.glob("*/tps.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        process = raw.get("process") if isinstance(raw, dict) else None
        if isinstance(process, dict) and str(process.get("id") or "").strip() == pid:
            found.append(str(raw.get("id") or path.parent.name))
    return found


def delete_process(process_id: str) -> str:
    """删除工序；被 TPS 引用时抛 ProcessError（调用方映射 409）。"""
    pid = str(process_id or "").strip()
    with _LOCK:
        data = _load()
        items = [x for x in data.get("processes") or [] if isinstance(x, dict)]
        if not any(x.get("id") == pid for x in items):
            raise ProcessError(f"工序不存在：{pid}", 404)
        refs = references(pid)
        if refs:
            preview = "、".join(refs[:5])
            more = "" if len(refs) <= 5 else f" 等 {len(refs)} 个"
            raise ProcessError(
                f"工序 {pid} 正被 {len(refs)} 个 TPS 引用（{preview}{more}），删除前请先解除引用", 409
            )
        data["processes"] = [x for x in items if x.get("id") != pid]
        data["updated_at"] = _now()
        _write(data)
    return f"已删除工序 {pid}"
