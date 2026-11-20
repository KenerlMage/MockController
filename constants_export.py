"""常数库导出：把某个版本还原成可再导入的全量包。

用途：Runner 从 Controller 同步常数库到本地运行库（Runner 只读，不再本地编辑）。
导出结构与 `constants.parse_bundle` 的输入一致，可直接回灌。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import constants


def _connect() -> sqlite3.Connection:
    return constants._connect()  # noqa: SLF001  同模块族，复用连接与行工厂


def export_version(version: str = "") -> dict:
    """导出指定版本（缺省为生效版本）的全量常数包。"""
    target = (version or "").strip() or constants.active_version()
    if not target:
        raise ValueError("没有可导出的常数版本")

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        head = conn.execute(
            "SELECT * FROM constant_version WHERE version = ?", (target,)
        ).fetchone()
        if not head:
            raise ValueError(f"常数版本不存在: {target}")

        models = conn.execute(
            "SELECT pn, product, subsystem, model_key, name, category, description, unit, fields_json "
            "FROM constant_model WHERE version = ? ORDER BY pn, model_key",
            (target,),
        ).fetchall()

        values = conn.execute(
            "SELECT pn, model_key, field_key, value, unit, source FROM constant_value WHERE version = ?",
            (target,),
        ).fetchall()

    value_map: dict[tuple[str, str], dict[str, dict]] = {}
    for row in values:
        value_map.setdefault((row["pn"], row["model_key"]), {})[row["field_key"]] = {
            "value": row["value"],
            "unit": row["unit"] or "",
            "source": row["source"] or "",
        }

    groups: dict[str, dict] = {}
    for row in models:
        pn = row["pn"]
        slot = groups.setdefault(
            pn,
            {
                "pn": pn,
                "product": row["product"] or "",
                "subsystem": row["subsystem"] or "",
                "models": [],
            },
        )
        try:
            fields = json.loads(row["fields_json"] or "[]")
        except json.JSONDecodeError:
            fields = []

        marked = []
        for field in fields:
            item = dict(field)
            hit = value_map.get((pn, row["model_key"]), {}).get(item.get("key", ""))
            if hit:
                item["value"] = hit["value"]
                if hit["unit"]:
                    item["unit"] = hit["unit"]
                if hit["source"]:
                    item["value_source"] = hit["source"]
            marked.append(item)

        slot["models"].append(
            {
                "model_key": row["model_key"],
                "name": row["name"] or row["model_key"],
                "category": row["category"] or "",
                "description": row["description"] or "",
                "unit": row["unit"] or "",
                "fields": marked,
            }
        )

    payload: dict[str, Any] = {
        "version": target,
        "label": head["label"] or "",
        "source": head["source"] or "",
        "note": head["note"] or "",
        "is_active": bool(head["is_active"]),
        "imported_at": head["imported_at"] or "",
        "groups": list(groups.values()),
        "stats": {
            "pn_count": len(groups),
            "model_count": sum(len(g["models"]) for g in groups.values()),
            "field_count": sum(len(m["fields"]) for g in groups.values() for m in g["models"]),
        },
    }
    return payload
