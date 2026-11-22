# -*- coding: utf-8 -*-
"""机器常数目录层：模型清单 / 版本历史 / TPS 复数关联 / 标定徽标 / 元数据二级菜单 / 字段映射

覆盖需求：
    SR01-AR05  按子系统查看模型列表与版本历史（直接查 constant_model / constant_value，不新建表）
    SR01-AR06  TPS 关联复数常数模型（同一 model_key 可重复，alias 唯一，source 枚举）
    SR01-AR07  标定类模型/ TPS 判定与徽标显示位置配置（base_dir/badge.json）
    SR01-AR08  元数据二级菜单（装备树 / 机器常数 / 工序）与条目数
    SR02-AR09  下位机字段映射表与 scale/offset 换算

对外函数（供其他模块调用）：
    is_calibration_model(pn, model_key, db_path=None, base_dir=None) -> bool
    list_models(...) / model_history(...) / get_badge(...) / set_badge(...)
    metadata_menu(...) / get_tps_constants(...) / set_tps_constants(...)
    get_field_mapping(...) / set_field_mapping(...) / apply_field_mapping(...)
    以及 register_routes(app, base_dir, db_path=None)

TPS 读写方式说明（重要）：
    tps_store 把 TPS 根目录放在模块级全局 `TPS_ROOT`（进程启动时由环境变量决定），
    与本模块拿到的 `base_dir` 不保证一致；且 `tps_store.save()` 会按 tps_schema 重新
    校验整份清单（process / preset 引用等），写常数绑定这一窄场景不该被无关校验拦住。
    因此这里的做法是：
      1. 路径统一按 tps_store 的目录约定解析为 `<base_dir>/tps/<tps_id>/tps.json`；
      2. 当 `tps_store.TPS_ROOT` 恰好等于 `<base_dir>/tps` 时，读取复用 `tps_store.load()`；
      3. 其余情况（含测试用的临时 base_dir）直接用 json 原子读写该文件；
      4. 写入后同步旧字段 `constant_model`，保证 constants.py / Runner 旧读法仍可用。
    绑定行同时保留 `model_key` 与 `models: [model_key]`，让 tps_store 的摘要统计不掉数。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import ate_db

_LOCK = threading.RLock()

# 运行期由 register_routes 注入（供不带 db_path / base_dir 的跨模块调用使用）
_BASE_DIR: Optional[Path] = None
_DB_PATH: Optional[Path] = None

# 标定徽标显示位置：前置 / 后置 / 仅图标
BADGE_POSITIONS = ("before", "after", "icon")
BADGE_LABELS = {"before": "名称前", "after": "名称后", "icon": "仅图标"}
DEFAULT_BADGE_POSITION = "before"

# 绑定取值来源：默认值 / 指定版本 / 按 SN 标定值
BINDING_SOURCES = ("default", "version", "sn")
SOURCE_LABELS = {"default": "默认值", "version": "指定版本", "sn": "按 SN 标定值"}

# 变更类型
CHANGE_LABELS = {
    "initial": "首次导入",
    "modified": "结构变更",
    "unchanged": "结构未变",
}

# 判定「标定类步骤」时接受的 type / kind 取值（大小写不敏感）
CALIB_STEP_TYPES = {"calib", "calibration", "calibrate", "标定"}

TPS_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# 菜单路径（前端二级路由）
MENU_PATHS = {
    "tree": "/metadata/tree",
    "constants": "/metadata/constants",
    "processes": "/metadata/processes",
}


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _bbox(pn: Any) -> str:
    return _text(pn, 64).upper()


def _base(base_dir: Optional[Path]) -> Path:
    return Path(base_dir) if base_dir else Path(_BASE_DIR or Path.cwd())


def _db_path(db_path: Optional[Path]) -> Path:
    return Path(db_path) if db_path else Path(_DB_PATH or ate_db.DB_PATH)


def _connect(db_path: Optional[Path] = None):
    path = _db_path(db_path)
    ate_db.init_db(path)
    return ate_db.connect(path)


def _as_float(value: Any, default: float) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"不是数值：{value!r}") from exc


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "是")
    return bool(value)


def _row_fields(raw: Any) -> list[dict]:
    """fields_json 解析成字段定义列表；坏数据按空列表处理（不让目录页整体失败）。"""
    data = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
    if not isinstance(data, list):
        return []
    return [f for f in data if isinstance(f, dict)]


def _field_key(field: dict) -> str:
    return _text(field.get("key") or field.get("id") or field.get("name"), 64)


def _field_type(field: dict) -> str:
    return _text(field.get("type"), 16).lower() or "float"


def _field_is_calib(field: dict) -> bool:
    """字段级标定标记：`calibration=true`（兼容 is_calibration / 字符串写法）。"""
    for key in ("calibration", "is_calibration"):
        if key in field:
            raw = field.get(key)
            if isinstance(raw, str):
                if raw.strip().lower() in ("1", "true", "yes", "y", "是"):
                    return True
            elif bool(raw):
                return True
    return False


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _active_version(conn) -> str:
    row = conn.execute(
        "SELECT version FROM constant_version WHERE is_active = 1 "
        "ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    return (_text(row["version"], 48) if row else "") or ""


# ---------------------------------------------------------------- 版本差异（只比结构）


def _diff_fields(old_fields: list[dict], new_fields: list[dict]) -> dict:
    """按字段 key 集合与 type 比较两版结构；不 diff 具体值。"""
    old_map = {_field_key(f): _field_type(f) for f in old_fields if _field_key(f)}
    new_map = {_field_key(f): _field_type(f) for f in new_fields if _field_key(f)}
    added = sorted(set(new_map) - set(old_map))
    removed = sorted(set(old_map) - set(new_map))
    type_changed = [
        {"key": key, "from": old_map[key], "to": new_map[key]}
        for key in sorted(set(old_map) & set(new_map))
        if old_map[key] != new_map[key]
    ]
    return {"added": added, "removed": removed, "type_changed": type_changed}


def _diff_summary(diff: dict, field_count: int, *, is_first: bool) -> str:
    if is_first:
        return f"首次导入，共 {field_count} 个字段"
    parts: list[str] = []
    if diff["added"]:
        parts.append(f"新增 {len(diff['added'])} 个字段（{'、'.join(diff['added'][:8])}）")
    if diff["removed"]:
        parts.append(f"删除 {len(diff['removed'])} 个字段（{'、'.join(diff['removed'][:8])}）")
    if diff["type_changed"]:
        preview = "、".join(f"{c['key']}: {c['from']}→{c['to']}" for c in diff["type_changed"][:5])
        parts.append(f"类型变化 {len(diff['type_changed'])} 个（{preview}）")
    return "；".join(parts) if parts else "结构未变化"


# ---------------------------------------------------------------- 标定类判定


def _tps_root(base_dir: Optional[Path] = None) -> Path:
    base = _base(base_dir)
    root = base / "tps"
    if root.is_dir():
        return root
    try:  # base_dir 与工程数据根不一致时退回 tps_store 的实际目录
        import tps_store

        fallback = Path(tps_store.TPS_ROOT)
        if fallback.is_dir():
            return fallback
    except Exception:  # noqa: BLE001 - tps_store 不可用时按 base_dir 处理
        pass
    return root


def _tps_files(root: Path) -> Iterable[tuple[str, Path]]:
    if not root.is_dir():
        return []
    return [
        (path.parent.name, path)
        for path in sorted(root.glob("*/tps.json"))
    ]


def _has_calib_step(tps: dict) -> bool:
    """TPS 是否含标定步骤（v1 的 steps 与 v2 的 setup/cmd_suit/teardown 都看）。"""
    for section in ("steps", "setup", "cmd_suit", "teardown"):
        entries = tps.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("type", "kind", "case_type", "category"):
                if _text(entry.get(key), 32).lower() in CALIB_STEP_TYPES:
                    return True
            if _as_bool(entry.get("calibration"), False):
                return True
    return False


def _binding_rows(tps: dict) -> list[dict]:
    """把清单里的常数绑定读成扁平行：{pn, model_key, alias, source, version}。

    兼容三种历史形态：
      * `constant_bindings: [{pn, model_key, alias, source, version}]`（本模块写入）
      * `constant_bindings: [{version, pn, models: [...]}]`（tps_store 原生多行）
      * `constant_model: {version, pn, models: [...]}`（最老的单行）
    """
    if not isinstance(tps, dict):
        return []
    rows: list[dict] = []
    raw_bindings = tps.get("constant_bindings")
    if isinstance(raw_bindings, list) and raw_bindings:
        for item in raw_bindings:
            rows.extend(_rows_of_one(item))
    elif isinstance(tps.get("constant_model"), dict) and tps.get("constant_model"):
        rows.extend(_rows_of_one(tps.get("constant_model")))
    return rows


def _rows_of_one(item: Any) -> list[dict]:
    if not isinstance(item, dict):
        return []
    pn = _bbox(item.get("pn"))
    version = _text(item.get("version"), 48)
    source = _text(item.get("source"), 16).lower() or ("version" if version else "default")
    models = [ _text(m, 64) for m in (item.get("models") or []) if _text(m, 64) ]
    model_key = _text(item.get("model_key") or item.get("model"), 64)
    keys = [model_key] if model_key else models
    rows: list[dict] = []
    for key in keys:
        rows.append(
            {
                "pn": pn,
                "model_key": key,
                "alias": _text(item.get("alias"), 64) or key,
                "source": source if source in BINDING_SOURCES else "default",
                "version": version,
            }
        )
    return rows


def _calib_refs(root: Path) -> set[tuple[str, str]]:
    """被「含标定步骤的 TPS」引用的模型集合：(PN 大写, model_key)。"""
    refs: set[tuple[str, str]] = set()
    for _, path in _tps_files(root):
        try:
            tps = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(tps, dict) or not _has_calib_step(tps):
            continue
        for row in _binding_rows(tps):
            if row["pn"] and row["model_key"]:
                refs.add((row["pn"], row["model_key"]))
    return refs


def is_calibration_model(
    pn: str,
    model_key: str,
    db_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> bool:
    """模型是否标定类：字段带 calibration=true，或被含标定步骤的 TPS 引用。"""
    pn_n = _bbox(pn)
    key = _text(model_key, 64)
    if not pn_n or not key:
        return False
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT fields_json FROM constant_model WHERE UPPER(pn) = ? AND model_key = ?",
                (pn_n, key),
            ).fetchall()
    except Exception:  # noqa: BLE001 - 目录不可读时不阻断徽标渲染
        rows = []
    for row in rows:
        if any(_field_is_calib(f) for f in _row_fields(row["fields_json"])):
            return True
    return (pn_n, key) in _calib_refs(_tps_root(base_dir))


# ---------------------------------------------------------------- SR01-AR05 列表


def list_models(
    product: str = "",
    subsystem: str = "",
    pn: str = "",
    version: str = "",
    db_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> dict:
    """按产品 / 子系统 / PN 列模型；缺省版本取生效版本，无生效版本时按模型取最新。"""
    product_f = _text(product, 64).upper()
    subsystem_f = _text(subsystem, 64).upper()
    pn_f = _bbox(pn)
    with _connect(db_path) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT m.pn, m.product, m.subsystem, m.model_key, m.name, m.category, "
                "       m.description, m.unit, m.fields_json, m.field_count, m.struct_checksum, "
                "       m.updated_at, m.version, "
                "       v.imported_at AS imported_at, v.imported_by AS imported_by, "
                "       v.is_active AS is_active "
                "FROM constant_model m "
                "LEFT JOIN constant_version v ON v.version = m.version "
                "ORDER BY m.pn, m.model_key, v.imported_at DESC, m.version DESC"
            ).fetchall()
        ]
        active = _active_version(conn)

    chosen = _text(version, 48) or active
    if chosen:
        rows = [r for r in rows if r["version"] == chosen]
    else:  # 没有版本头时按 (pn, model_key) 取最新一版，避免同模型重复出现
        seen: set[tuple] = set()
        unique: list[dict] = []
        for row in rows:
            ident = (row["pn"], row["model_key"])
            if ident in seen:
                continue
            seen.add(ident)
            unique.append(row)
        rows = unique

    if product_f:
        rows = [r for r in rows if (r["product"] or "").upper() == product_f]
    if subsystem_f:
        rows = [r for r in rows if (r["subsystem"] or "").upper() == subsystem_f]
    if pn_f:
        rows = [r for r in rows if (r["pn"] or "").upper() == pn_f]

    calib_refs = _calib_refs(_tps_root(base_dir))
    models: list[dict] = []
    for row in rows:
        fields = _row_fields(row["fields_json"])
        calib_fields = [_field_key(f) for f in fields if _field_key(f) and _field_is_calib(f)]
        is_calib = bool(calib_fields) or ((row["pn"] or "").upper(), row["model_key"]) in calib_refs
        models.append(
            {
                "pn": row["pn"],
                "product": row["product"] or "",
                "subsystem": row["subsystem"] or "",
                "model_key": row["model_key"],
                "name": row["name"] or row["model_key"],
                "category": row["category"] or "",
                "description": row["description"] or "",
                "unit": row["unit"] or "",
                "field_count": row["field_count"] or len(fields),
                "version": row["version"],
                "updated_at": row["updated_at"] or "",
                "imported_at": row["imported_at"] or "",
                "is_active": bool(row["is_active"]),
                "is_calibration": bool(is_calib),
                "calibration_fields": calib_fields,
                "struct_checksum": row["struct_checksum"] or "",
            }
        )
    models.sort(key=lambda m: (m["product"], m["subsystem"], m["pn"], m["model_key"]))
    return {
        "success": True,
        "version": chosen,
        "active_version": active,
        "filters": {"product": product_f, "subsystem": subsystem_f, "pn": pn_f},
        "count": len(models),
        "models": models,
    }


def model_history(pn: str, model_key: str, db_path: Optional[Path] = None) -> dict:
    """版本历史：按 constant_version.imported_at 倒序，差异只比字段键集合与类型。"""
    pn_n = _bbox(pn)
    key = _text(model_key, 64)
    if not pn_n or not key:
        raise ValueError("需要同时给出 pn 与 model_key")
    with _connect(db_path) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT m.pn, m.model_key, m.name, m.version, m.fields_json, m.field_count, "
                "       m.struct_checksum, m.updated_at, "
                "       v.imported_at AS imported_at, v.imported_by AS imported_by, "
                "       v.is_active AS is_active "
                "FROM constant_model m "
                "LEFT JOIN constant_version v ON v.version = m.version "
                "WHERE UPPER(m.pn) = ? AND m.model_key = ? "
                "ORDER BY v.imported_at DESC, m.version DESC",
                (pn_n, key),
            ).fetchall()
        ]
        active = _active_version(conn)
    if not rows:
        raise ValueError(f"未找到常数模型：{pn_n} / {key}")

    versions: list[dict] = []
    for index, row in enumerate(rows):
        fields = _row_fields(row["fields_json"])
        older = rows[index + 1] if index + 1 < len(rows) else None
        is_first = older is None
        diff = (
            {"added": [], "removed": [], "type_changed": []}
            if is_first
            else _diff_fields(_row_fields(older["fields_json"]), fields)
        )
        changed = bool(diff["added"] or diff["removed"] or diff["type_changed"])
        change_type = "initial" if is_first else ("modified" if changed else "unchanged")
        versions.append(
            {
                "version": row["version"],
                "imported_at": row["imported_at"] or "",
                "imported_by": row["imported_by"] or "",
                "updated_at": row["updated_at"] or "",
                "field_count": row["field_count"] or len(fields),
                "struct_checksum": row["struct_checksum"] or "",
                "change_type": change_type,
                "change_type_label": CHANGE_LABELS[change_type],
                "diff": diff,
                "diff_summary": _diff_summary(diff, len(fields), is_first=is_first),
                "is_active": bool(row["is_active"]),
            }
        )
    return {
        "success": True,
        "pn": pn_n,
        "model_key": key,
        "name": rows[0]["name"] or key,
        "active_version": active,
        "count": len(versions),
        "versions": versions,
    }


# ---------------------------------------------------------------- SR01-AR07 徽标配置


def _badge_file(base_dir: Optional[Path] = None) -> Path:
    return _base(base_dir) / "badge.json"


def get_badge(base_dir: Optional[Path] = None) -> dict:
    """读徽标配置；文件缺失或取值非法时回退 before（带 warning，不报错）。"""
    path = _badge_file(base_dir)
    warning = ""
    position = DEFAULT_BADGE_POSITION
    updated_at = ""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            data = {}
            warning = f"徽标配置不可读，已回退 {DEFAULT_BADGE_POSITION}：{exc}"
        if isinstance(data, dict):
            raw = _text(data.get("position"), 16).lower()
            updated_at = _text(data.get("updated_at"), 40)
            if raw in BADGE_POSITIONS:
                position = raw
            elif raw:
                warning = f"徽标位置 {raw} 非法，已回退 {DEFAULT_BADGE_POSITION}"
        else:
            warning = f"徽标配置格式错误，已回退 {DEFAULT_BADGE_POSITION}"
    return {
        "success": True,
        "message": warning or None,
        "position": position,
        "label": BADGE_LABELS[position],
        "options": [{"value": p, "label": BADGE_LABELS[p]} for p in BADGE_POSITIONS],
        "default": DEFAULT_BADGE_POSITION,
        "updated_at": updated_at,
        "file": str(path),
    }


def set_badge(position: str, base_dir: Optional[Path] = None) -> dict:
    """写徽标位置；非法取值直接拒绝（不做静默纠正）。"""
    raw = _text(position, 16).lower()
    if raw not in BADGE_POSITIONS:
        raise ValueError(
            f"徽标位置只能是 {' / '.join(BADGE_POSITIONS)}：{position}"
        )
    path = _badge_file(base_dir)
    payload = {"position": raw, "updated_at": _now()}
    with _LOCK:
        _write_json(path, payload)
    return {
        "success": True,
        "message": f"标定徽标显示位置已设为{BADGE_LABELS[raw]}",
        "position": raw,
        "label": BADGE_LABELS[raw],
        "updated_at": payload["updated_at"],
        "file": str(path),
    }


# ---------------------------------------------------------------- SR01-AR08 元数据菜单


def _tree_count(base_dir: Path, tps_root: Path) -> tuple[int, str]:
    """装备树条目数：tree.json 的产品数；缺失时退回 TPS 数；都缺省 0。"""
    path = base_dir / "tree" / "tree.json"
    if path.is_file():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            store = {}
        products = store.get("products") if isinstance(store, dict) else None
        if isinstance(products, list):
            return len(products), "tree.products"
    tps = list(_tps_files(tps_root))
    if tps:
        return len(tps), "tps"
    return 0, "none"


def _process_count(base_dir: Path) -> tuple[int, str]:
    """工序条目数：优先 base_dir/processes.json；与工程根同源时走 processes 模块。"""
    path = base_dir / "processes.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0, "processes.json"
        items = data.get("processes") if isinstance(data, dict) else data
        return (len(items) if isinstance(items, list) else 0), "processes.json"
    try:
        import tps_store

        same_root = Path(tps_store.REPO_ROOT).resolve() == base_dir.resolve()
    except Exception:  # noqa: BLE001
        same_root = False
    if same_root:
        try:
            import processes

            return len(processes.list_processes()), "processes"
        except Exception:  # noqa: BLE001 - 工序模块不可用时按 0 处理
            return 0, "processes"
    return 0, "none"


def metadata_menu(
    base_dir: Optional[Path] = None, db_path: Optional[Path] = None
) -> dict:
    """元数据二级菜单三项及其条目数。"""
    base = _base(base_dir)
    tps_root = _tps_root(base_dir)
    tree_count, tree_source = _tree_count(base, tps_root)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ("
            "  SELECT DISTINCT pn, model_key FROM constant_model"
            ")"
        ).fetchone()
    constant_count = int(row["n"] or 0) if row else 0
    process_count, process_source = _process_count(base)
    items = [
        {
            "key": "tree",
            "label": "装备树",
            "count": tree_count,
            "path": MENU_PATHS["tree"],
            "unit": "个产品",
            "source": tree_source,
        },
        {
            "key": "constants",
            "label": "机器常数",
            "count": constant_count,
            "path": MENU_PATHS["constants"],
            "unit": "个模型",
            "source": "constant_model",
        },
        {
            "key": "processes",
            "label": "工序",
            "count": process_count,
            "path": MENU_PATHS["processes"],
            "unit": "道工序",
            "source": process_source,
        },
    ]
    return {"success": True, "count": len(items), "items": items}


# ---------------------------------------------------------------- SR01-AR06 TPS 复数关联


def _tps_json(base_dir: Optional[Path], tps_id: str) -> Path:
    text = _text(tps_id, 64)
    if not TPS_ID_RE.match(text):
        raise ValueError(f"TPS 编号只允许字母、数字、下划线、点与短横线：{tps_id}")
    return _base(base_dir) / "tps" / text / "tps.json"


def _load_tps(tps_id: str, base_dir: Optional[Path] = None) -> tuple[dict, Path]:
    path = _tps_json(base_dir, tps_id)
    if not path.is_file():
        raise ValueError(f"TPS 不存在：{tps_id}")
    same_root = False
    try:  # 目录与 tps_store 一致时复用其 load()（保证读取口径与其余接口相同）
        import tps_store

        same_root = Path(tps_store.TPS_ROOT).resolve() == path.parent.parent.resolve()
    except Exception:  # noqa: BLE001
        same_root = False
    try:
        if same_root:
            import tps_store

            raw = tps_store.load(tps_id)
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"TPS 清单不是合法 JSON：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"TPS 清单读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("TPS 清单格式错误：顶层必须是对象")
    return raw, path


def get_tps_constants(tps_id: str, base_dir: Optional[Path] = None) -> dict:
    raw, path = _load_tps(tps_id, base_dir)
    rows = _binding_rows(raw)
    return {
        "success": True,
        "tps_id": _text(raw.get("id"), 64) or tps_id,
        "name": _text(raw.get("name"), 200),
        "bindings": rows,
        "count": len(rows),
        "sources": [{"value": s, "label": SOURCE_LABELS[s]} for s in BINDING_SOURCES],
        "file": str(path),
    }


def _visible_pairs(visible: Any) -> Optional[set[tuple[str, str]]]:
    """把 const_approval.visible_models 的返回值解释成 (PN, model_key) 集合。"""
    items: Any = visible
    if isinstance(visible, dict):
        items = visible.get("models") or visible.get("items") or visible.get("visible") or []
    if isinstance(items, (set, frozenset, list, tuple)):
        pairs: set[tuple[str, str]] = set()
        for item in items:
            if isinstance(item, dict):
                pn = _bbox(item.get("pn"))
                key = _text(item.get("model_key") or item.get("key"), 64)
                if pn and key:
                    pairs.add((pn, key))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.add((_bbox(item[0]), _text(item[1], 64)))
            elif isinstance(item, str) and "/" in item:
                left, right = item.split("/", 1)
                pairs.add((_bbox(left), _text(right, 64)))
        return pairs
    return None


def _approval_warning(rows: list[dict], db_path: Optional[Path]) -> Optional[str]:
    """校验关联模型无 pending 审批；审批模块不可用时降级为不校验并返回 warning。"""
    try:
        import const_approval
    except Exception as exc:  # noqa: BLE001 - 审批模块未建好时不阻断保存
        return f"审批模块不可用，已跳过待审批校验：{exc}"

    # 优先用单模型判定接口（const_approval.model_visible），避免解释整本清单
    single = getattr(const_approval, "model_visible", None)
    if callable(single):
        blocked: list[str] = []
        for row in rows:
            try:
                ok = bool(single(row["pn"], row["model_key"], db_path=db_path))
            except TypeError:  # 签名不认识 db_path 时退回两参数
                try:
                    ok = bool(single(row["pn"], row["model_key"]))
                except Exception as exc:  # noqa: BLE001
                    return f"待审批校验调用失败，已跳过：{exc}"
            except Exception as exc:  # noqa: BLE001
                return f"待审批校验调用失败，已跳过：{exc}"
            if not ok:
                blocked.append(f"{row['pn']}/{row['model_key']}")
        if blocked:
            raise ValueError(
                "以下常数模型待审批或不生效，不能关联：" + "、".join(sorted(set(blocked)))
            )
        return None

    fn = getattr(const_approval, "visible_models", None)
    if not callable(fn):
        return "审批模块缺少 visible_models，已跳过待审批校验"

    visible = None
    for kwargs in ({"db_path": db_path}, {}):
        try:
            visible = fn(**kwargs)
            break
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            return f"待审批校验调用失败，已跳过：{exc}"
    if visible is None:
        return "待审批校验调用签名不匹配，已跳过"
    pairs = _visible_pairs(visible)
    if pairs is None:
        return "待审批校验返回值无法解析，已跳过"
    blocked = [
        f"{row['pn']}/{row['model_key']}"
        for row in rows
        if (row["pn"], row["model_key"]) not in pairs
    ]
    if blocked:
        raise ValueError("以下常数模型待审批或不生效，不能关联：" + "、".join(sorted(set(blocked))))
    return None


def set_tps_constants(
    tps_id: str,
    bindings: Any,
    base_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """全量替换某个 TPS 的常数模型关联；同模型可重复，alias 全局唯一。"""
    if bindings is None:
        raise ValueError("bindings 必须是数组（清空传 []）")
    if not isinstance(bindings, list):
        raise ValueError("bindings 必须是数组")

    rows: list[dict] = []
    aliases: set[str] = set()
    for index, item in enumerate(bindings, 1):
        where = f"第 {index} 行绑定"
        if not isinstance(item, dict):
            raise ValueError(f"{where} 不是对象")
        pn = _bbox(item.get("pn"))
        model_key = _text(item.get("model_key") or item.get("model"), 64)
        alias = _text(item.get("alias"), 64)
        source = _text(item.get("source"), 16).lower() or "default"
        version = _text(item.get("version"), 48)
        if not pn:
            raise ValueError(f"{where} 缺少 pn")
        if not model_key:
            raise ValueError(f"{where} 缺少 model_key")
        if not alias:
            raise ValueError(f"{where} 的 alias 不能为空")
        if alias in aliases:
            raise ValueError(f"绑定别名重复：{alias}（同一 TPS 内别名必须唯一）")
        if source not in BINDING_SOURCES:
            raise ValueError(
                f"{where} 的 source 只能是 {' / '.join(BINDING_SOURCES)}：{source}"
            )
        if source == "version" and not version:
            raise ValueError(f"{where} 的 source=version 时必须给出 version")
        aliases.add(alias)
        rows.append(
            {
                "pn": pn,
                "model_key": model_key,
                "alias": alias,
                "source": source,
                "version": version,
            }
        )

    if rows:  # 模型必须已存在（指定版本时按该版本查）
        missing: list[str] = []
        with _connect(db_path) as conn:
            for row in rows:
                sql = "SELECT 1 FROM constant_model WHERE UPPER(pn) = ? AND model_key = ?"
                params: list[Any] = [row["pn"], row["model_key"]]
                if row["version"]:
                    sql += " AND version = ?"
                    params.append(row["version"])
                if conn.execute(sql, params).fetchone() is None:
                    suffix = f"（版本 {row['version']}）" if row["version"] else ""
                    missing.append(f"{row['pn']}/{row['model_key']}{suffix}")
        if missing:
            raise ValueError("常数模型不存在，不能关联：" + "、".join(dict.fromkeys(missing)))

    warning = _approval_warning(rows, db_path) if rows else None

    raw, path = _load_tps(tps_id, base_dir)
    if rows:
        raw["constant_bindings"] = [
            {
                "pn": row["pn"],
                "model_key": row["model_key"],
                "models": [row["model_key"]],
                "alias": row["alias"],
                "source": row["source"],
                **({"version": row["version"]} if row["version"] else {}),
            }
            for row in rows
        ]
        first = rows[0]
        raw["constant_model"] = {
            "version": first["version"],
            "pn": first["pn"],
            "models": [r["model_key"] for r in rows if r["pn"] == first["pn"]],
        }
    else:
        raw.pop("constant_bindings", None)
        raw.pop("constant_model", None)
    raw["updated_at"] = _now()
    with _LOCK:
        _write_json(path, raw)
    return {
        "success": True,
        "message": f"已保存 {len(rows)} 条常数模型关联",
        "tps_id": _text(raw.get("id"), 64) or tps_id,
        "bindings": rows,
        "count": len(rows),
        "warning": warning,
        "file": str(path),
    }


# ---------------------------------------------------------------- SR02-AR09 字段映射


def _mapping_rows(conn, pn: str, model_key: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT product, subsystem, pn, model_key, source_field, target_field, "
            "       scale, offset, unit, required "
            "FROM const_field_mapping WHERE UPPER(pn) = ? AND model_key = ? ORDER BY id",
            (pn, model_key),
        ).fetchall()
    ]


def _norm_mapping(raw: dict, pn: str, model_key: str) -> dict:
    source_field = _text(raw.get("source_field") or raw.get("source"), 120)
    target_field = _text(raw.get("target_field") or raw.get("target"), 120)
    if not source_field:
        raise ValueError("mappings 每行必须有 source_field（下位机字段名）")
    if not target_field:
        raise ValueError(f"映射 {source_field} 缺少 target_field（模型字段键）")
    try:
        scale = _as_float(raw.get("scale"), 1.0)
        offset = _as_float(raw.get("offset"), 0.0)
    except ValueError as exc:
        raise ValueError(f"映射 {source_field} 的 {exc}") from exc
    return {
        "product": _text(raw.get("product"), 64) or "",
        "subsystem": _text(raw.get("subsystem"), 64) or "",
        "pn": pn,
        "model_key": model_key,
        "source_field": source_field,
        "target_field": target_field,
        "scale": scale,
        "offset": offset,
        "unit": _text(raw.get("unit"), 32),
        "required": 1 if _as_bool(raw.get("required"), True) else 0,
    }


def get_field_mapping(
    pn: str, model_key: str, db_path: Optional[Path] = None
) -> dict:
    pn_n = _bbox(pn)
    key = _text(model_key, 64)
    if not pn_n or not key:
        raise ValueError("需要同时给出 pn 与 model_key")
    with _connect(db_path) as conn:
        rows = _mapping_rows(conn, pn_n, key)
    mappings = [
        {
            "source_field": r["source_field"],
            "target_field": r["target_field"],
            "scale": float(r["scale"] if r["scale"] is not None else 1.0),
            "offset": float(r["offset"] if r["offset"] is not None else 0.0),
            "unit": r["unit"] or "",
            "required": bool(r["required"]),
        }
        for r in rows
    ]
    first = rows[0] if rows else {}
    return {
        "success": True,
        "pn": pn_n,
        "model_key": key,
        "product": (first.get("product") or ""),
        "subsystem": (first.get("subsystem") or ""),
        "count": len(mappings),
        "mappings": mappings,
    }


def set_field_mapping(payload: dict, db_path: Optional[Path] = None) -> dict:
    """全量替换某模型的映射（先删后插，单事务）。"""
    payload = payload or {}
    pn_n = _bbox(payload.get("pn"))
    key = _text(payload.get("model_key"), 64)
    if not pn_n or not key:
        raise ValueError("需要同时给出 pn 与 model_key")
    raw_mappings = payload.get("mappings")
    if raw_mappings is None:
        raw_mappings = []
    if not isinstance(raw_mappings, list):
        raise ValueError("mappings 必须是数组")

    product = _text(payload.get("product"), 64)
    subsystem = _text(payload.get("subsystem"), 64)
    rows: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_mappings, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条映射不是对象")
        merged = {"product": product, "subsystem": subsystem, **item}
        row = _norm_mapping(merged, pn_n, key)
        if row["source_field"] in seen:
            raise ValueError(f"下位机字段重复映射：{row['source_field']}")
        seen.add(row["source_field"])
        rows.append(row)

    now = _now()
    with _LOCK:
        with _connect(db_path) as conn:
            conn.execute(
                "DELETE FROM const_field_mapping WHERE UPPER(pn) = ? AND model_key = ?",
                (pn_n, key),
            )
            conn.executemany(
                "INSERT INTO const_field_mapping "
                "(product, subsystem, pn, model_key, source_field, target_field, "
                " scale, offset, unit, required, updated_at) "
                "VALUES (:product, :subsystem, :pn, :model_key, :source_field, :target_field, "
                "        :scale, :offset, :unit, :required, :updated_at)",
                [{**row, "updated_at": now} for row in rows],
            )
            conn.commit()
    data = get_field_mapping(pn_n, key, db_path=db_path)
    return {
        **data,
        "message": f"已保存 {len(rows)} 条字段映射（该模型原映射已全量替换）",
        "updated_at": now,
    }


def apply_field_mapping(
    pn: str, model_key: str, rows: Any, db_path: Optional[Path] = None
) -> dict:
    """按下位机返回值换算：target = value * scale + offset。"""
    mapping = get_field_mapping(pn, model_key, db_path=db_path)
    mappings = mapping["mappings"]
    if not mappings:
        raise ValueError(f"模型 {mapping['pn']}/{mapping['model_key']} 尚未配置字段映射")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("rows 必须是数组")
    if not rows:
        raise ValueError("rows 不能为空")

    by_source = {m["source_field"]: m for m in mappings}
    values: dict[str, float] = {}
    received: set[str] = set()
    unknown: list[str] = []
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 行返回值不是对象")
        source_field = _text(item.get("source_field"), 120)
        if not source_field:
            raise ValueError(f"第 {index} 行缺少 source_field")
        if source_field not in by_source:
            unknown.append(source_field)
            continue
        raw_value = item.get("value")
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            raise ValueError(f"字段 {source_field} 的值为空，无法换算")
        try:
            number = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"字段 {source_field} 的值不是数值：{raw_value!r}") from exc
        rule = by_source[source_field]
        values[rule["target_field"]] = round(number * rule["scale"] + rule["offset"], 10)
        received.add(source_field)

    if unknown:
        raise ValueError("存在未配置映射的下位机字段：" + "、".join(dict.fromkeys(unknown)))
    missing = [
        m["source_field"]
        for m in mappings
        if m["required"] and m["source_field"] not in received
    ]
    if missing:
        raise ValueError("缺少必填字段，未收到：" + "、".join(missing))
    # 契约写的是「返回 {target_field: value}」：除结构化的 values 外，再把换算结果平铺到顶层，
    # 但保留字（success/message/pn/...）不让覆盖，避免响应被业务字段踢坏。
    reserved = {"success", "message", "pn", "model_key", "count", "values"}
    flat = {k: v for k, v in values.items() if k not in reserved}
    return {
        "success": True,
        "pn": mapping["pn"],
        "model_key": mapping["model_key"],
        "count": len(values),
        "values": values,
        **flat,
    }


# ---------------------------------------------------------------- 路由


def register_routes(app, base_dir: Path, db_path: Optional[Path] = None) -> None:
    """挂载 /api/v1 下的目录层路由；异常一律包成 success=false + 中文 message。"""
    global _BASE_DIR, _DB_PATH
    _BASE_DIR = Path(base_dir)
    _DB_PATH = Path(db_path) if db_path else ate_db.DB_PATH
    ate_db.init_db(_DB_PATH)
    # 路由内部用局部变量：同进程挂多个 app（如测试）时不会串库
    root = _BASE_DIR
    db_file = _DB_PATH

    from fastapi import Body

    def _fail(exc: Exception, prefix: str = "") -> dict:
        message = _text(exc, 400) or exc.__class__.__name__
        return {"success": False, "message": f"{prefix}{message}"}

    # ---- SR01-AR05 列表 / 历史 ----

    @app.get("/api/v1/constants/models/detail")
    def constants_models_detail(
        product: str = "", subsystem: str = "", pn: str = "", version: str = ""
    ):
        try:
            return list_models(
                product=product,
                subsystem=subsystem,
                pn=pn,
                version=version,
                db_path=db_file,
                base_dir=root,
            )
        except ValueError as exc:
            return {**_fail(exc), "models": [], "count": 0}
        except Exception as exc:  # noqa: BLE001 - 兜底，不把裸异常抛给前端
            return {**_fail(exc, "读取常数模型列表失败："), "models": [], "count": 0}

    @app.get("/api/v1/constants/models/{pn}/{model_key}/history")
    def constants_model_history(pn: str, model_key: str):
        try:
            return model_history(pn, model_key, db_path=db_file)
        except ValueError as exc:
            return {**_fail(exc), "versions": [], "count": 0}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "读取版本历史失败："), "versions": [], "count": 0}

    # ---- SR01-AR07 标定徽标 ----

    @app.get("/api/v1/constants/calib-badge")
    def constants_calib_badge():
        try:
            return get_badge(root)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "读取标定徽标配置失败：")

    @app.put("/api/v1/constants/calib-badge")
    def constants_calib_badge_save(payload: Any = Body(default=None)):
        try:
            data = payload or {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 {position: before|after|icon}")
            return set_badge(data.get("position"), root)
        except ValueError as exc:
            return _fail(exc)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "保存标定徽标配置失败：")

    # 需求契约里的旧路径（/api/v1/settings/tps-badge）保留同义别名
    @app.get("/api/v1/settings/tps-badge")
    def settings_tps_badge():
        try:
            return get_badge(root)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "读取标定徽标配置失败：")

    @app.put("/api/v1/settings/tps-badge")
    def settings_tps_badge_save(payload: Any = Body(default=None)):
        try:
            data = payload or {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 {position: before|after|icon}")
            return set_badge(data.get("position"), root)
        except ValueError as exc:
            return _fail(exc)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "保存标定徽标配置失败：")

    # ---- SR01-AR08 元数据二级菜单 ----

    @app.get("/api/v1/metadata/menu")
    def metadata_menu_route():
        try:
            return metadata_menu(root, db_file)
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "读取元数据菜单失败："), "items": [], "count": 0}

    # ---- SR01-AR06 TPS 复数关联 ----

    @app.get("/api/v1/tps/{tps_id}/constants")
    def tps_constants_get(tps_id: str):
        try:
            return get_tps_constants(tps_id, root)
        except ValueError as exc:
            return {**_fail(exc), "bindings": [], "count": 0}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "读取 TPS 常数关联失败："), "bindings": [], "count": 0}

    @app.put("/api/v1/tps/{tps_id}/constants")
    def tps_constants_put(tps_id: str, payload: Any = Body(default=None)):
        try:
            data = payload or {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 {bindings: [...]}")
            return set_tps_constants(
                tps_id, data.get("bindings"), base_dir=root, db_path=db_file
            )
        except ValueError as exc:
            return {**_fail(exc), "bindings": []}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "保存 TPS 常数关联失败："), "bindings": []}

    # ---- SR02-AR09 字段映射 ----

    @app.get("/api/v1/constants/field-mapping")
    def constants_field_mapping(pn: str = "", model_key: str = ""):
        try:
            return get_field_mapping(pn, model_key, db_path=db_file)
        except ValueError as exc:
            return {**_fail(exc), "mappings": [], "count": 0}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "读取字段映射失败："), "mappings": [], "count": 0}

    @app.put("/api/v1/constants/field-mapping")
    def constants_field_mapping_put(payload: Any = Body(default=None)):
        try:
            data = payload or {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 {pn, model_key, mappings: [...]}")
            return set_field_mapping(data, db_path=db_file)
        except ValueError as exc:
            return {**_fail(exc), "mappings": []}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "保存字段映射失败："), "mappings": []}

    @app.post("/api/v1/constants/field-mapping/apply")
    def constants_field_mapping_apply(payload: Any = Body(default=None)):
        try:
            data = payload or {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须是 {pn, model_key, rows: [...]}")
            return apply_field_mapping(
                data.get("pn"), data.get("model_key"), data.get("rows"), db_path=db_file
            )
        except ValueError as exc:
            return {**_fail(exc), "values": {}}
        except Exception as exc:  # noqa: BLE001
            return {**_fail(exc, "字段映射换算失败："), "values": {}}
