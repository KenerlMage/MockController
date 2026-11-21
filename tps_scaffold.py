"""TPS 脚手架与编辑器上下文聚合。

两块职责：
1. `context(tps)` —— 编辑器一次取全：清单 + 预设测试台 + BOM + 工序 + 解析器 + 机器常数；
2. `scaffold(...)` —— 按预设测试台生成合法的 tps.v2 清单与起始用例文件（可选直接落盘）。

解析器清单是唯一事实源（`PARSERS`），保存校验（tps_store）也复用这里。
"""

from __future__ import annotations

import re
from typing import Any, Optional

import metadata
import processes

DEFAULT_PARSER = "tps.v2"

PARSERS: list[dict] = [
    {
        "name": "tps.v2",
        "label": "TPS v2（testcase/ + cmd_suit）",
        "schema": "tps.v2",
        "runtime": "2.0",
        "default": True,
        "description": "用例实现放 testcase/，cmd_suit 引用 module::func",
    },
    {
        "name": "tps.v1",
        "label": "TPS v1（单文件 steps）",
        "schema": "tps.v1",
        "runtime": "1.0",
        "default": False,
        "description": "兼容旧清单，steps 数组直写",
    },
]


class ScaffoldError(Exception):
    """脚手架参数错误；status_code 供路由层直接映射。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def parser_names() -> list[str]:
    return [p["name"] for p in PARSERS]


def parser_of(name: str) -> Optional[dict]:
    return next((p for p in PARSERS if p["name"] == name), None)


def parser_list() -> list[dict]:
    return [dict(p) for p in PARSERS]


# ------------------------------------------------------------------ 上下文


def _preset_view(preset: dict) -> dict:
    return {
        "id": preset.get("id", ""),
        "name": preset.get("name", ""),
        "product": preset.get("product", ""),
        "product_name": preset.get("product_name", ""),
        "subsystem": preset.get("subsystem", ""),
        "category": preset.get("category", ""),
        "typical_dut": preset.get("typical_dut", ""),
    }


def _bom_view(bom: list[dict]) -> list[dict]:
    return [
        {
            "alias": str(item.get("alias") or item.get("id") or ""),
            "device_type": str(item.get("device_type") or item.get("category") or ""),
            "resource": str(item.get("resource") or item.get("default_address") or ""),
        }
        for item in bom
        if isinstance(item, dict)
    ]


def find_preset(preset_id: str) -> Optional[dict]:
    pid = str(preset_id or "").strip()
    if not pid:
        return None
    return next((p for p in metadata.load_presets() if p.get("id") == pid), None)


def _constant_models(version: str, pn: str = "") -> list[dict]:
    """按版本（+PN）列模型，只返回精简字段 model_key/name/pn。"""
    if not version:
        return []
    try:
        import constants
    except Exception:  # noqa: BLE001 - 常数模块未挂载时降级为空
        return []
    try:
        if pn:
            data = constants.models_of(pn, version)
            return [
                {"model_key": m.get("model_key", ""), "name": m.get("name", ""), "pn": data.get("pn", pn)}
                for m in data.get("models") or []
            ]
        # 未绑定 PN：返回该版本下全部 PN 的模型
        out: list[dict] = []
        for product in (constants.tree(version).get("tree") or []):
            for sub in product.get("subsystems") or []:
                for pn_node in sub.get("pns") or []:
                    row_pn = pn_node.get("pn", "")
                    data = constants.models_of(row_pn, version)
                    out.extend(
                        {"model_key": m.get("model_key", ""), "name": m.get("name", ""), "pn": data.get("pn", row_pn)}
                        for m in data.get("models") or []
                    )
        return out
    except Exception:  # noqa: BLE001 - 常数库缺失 / 查询失败时不影响编辑器打开
        return []


def constants_view(binding: Optional[dict] = None) -> dict:
    binding = binding if isinstance(binding, dict) else {}
    versions: list[str] = []
    active = ""
    try:
        import constants

        info = constants.list_versions()
        versions = [v.get("version", "") for v in info.get("versions") or [] if v.get("version")]
        active = info.get("active") or ""
    except Exception:  # noqa: BLE001
        pass

    pn = str(binding.get("pn") or "").strip()
    version = str(binding.get("version") or "").strip()
    models = _constant_models(version or active, pn)
    return {
        "active": active,
        "versions": versions,
        "pn": pn,
        "models": models,
    }


def context(tps: dict) -> dict:
    """编辑器上下文：preset/bom 仅在清单存了 preset_id 时返回。"""
    preset = find_preset(str(tps.get("preset_id") or "")) if tps.get("preset_id") else None
    bom = _bom_view(preset.get("bom") or []) if preset else []
    return {
        "ok": True,
        "tps": tps,
        "preset": _preset_view(preset) if preset else None,
        "bom": bom,
        "processes": [
            {"id": p.get("id", ""), "code": p.get("code", ""), "name": p.get("name", ""), "stage": p.get("stage", "")}
            for p in processes.list_processes()
        ],
        "parsers": [{"name": p["name"], "label": p["label"], "default": p["default"]} for p in PARSERS],
        "constants": constants_view(tps.get("constant_model")),
    }


# ------------------------------------------------------------------ 脚手架


def _slug(preset_id: str, sep: str) -> str:
    text = re.sub(r"[^a-z0-9]+", sep, str(preset_id).lower()).strip(sep)
    return text or "tps"


def _default_id(preset_id: str) -> str:
    """spm-rh-dyn-01 -> spm-rh-dyn-01-tps；重名追加 -2/-3..."""
    import tps_store

    base = f"{_slug(preset_id, '-')}-tps"
    candidate = base
    index = 2
    while (tps_store.TPS_ROOT / candidate).exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _module_name(preset_id: str) -> str:
    return _slug(preset_id, "_")


def _template(preset: dict, module: str, process: Optional[dict]) -> str:
    title = preset.get("name") or preset.get("id", "")
    dut = preset.get("typical_dut") or "待填被测对象"
    stage = f"{process.get('name', '')}（{process.get('code', '')}）" if process else "待填工序"
    return f'''# -*- coding: utf-8 -*-
"""{title} TPS 用例实现。

由 Controller 脚手架生成：testcase/{module}.py
- setup   ：上电 / 夹具准备，对应清单 setup 段落；
- teardown：断电 / 资源释放，对应清单 teardown 段落；
- main    ：主判据用例，对应清单 cmd_suit，返回 {{阈值键: 实测值}}。

被测对象：{dut}
适用工序：{stage}
"""


def setup(ctx, **params):
    """上电与夹具准备。"""
    # TODO: 接入 atefr 设备会话，例如
    #   psu = ctx.device("rh-psu")
    #   psu.set_voltage(5.0)
    #   psu.output_on()
    return {{}}


def teardown(ctx, **params):
    """断电与资源释放。"""
    # TODO: 例如 ctx.device("rh-psu").output_off()
    return {{}}


def main(ctx, **params):
    """主判据：读取实测值并回填到 testconfig 的阈值键。"""
    # TODO: 按 BOM 别名取设备并采集数据，例如
    #   scope = ctx.device("rh-scope")
    #   wave = scope.read_waveform(channel=1)
    #   return {{"TC_MAIN": wave.vpp}}
    raise NotImplementedError("TODO: 实现 {title} 的主判据采集")
'''


def scaffold(
    preset_id: str,
    process_id: str = "",
    tps_id: str = "",
    name: str = "",
    constant_version: str = "",
    pn: str = "",
    models: Optional[list[str]] = None,
    device_aliases: Optional[list[str]] = None,
    parser: str = DEFAULT_PARSER,
    save: bool = False,
) -> dict:
    import tps_store

    parser_name = str(parser or DEFAULT_PARSER).strip()
    parser_def = parser_of(parser_name)
    if parser_def is None:
        raise ScaffoldError(f"parser 不合法：{parser_name}（可选：{'、'.join(parser_names())}）", 400)

    preset = find_preset(preset_id)
    if preset is None:
        raise ScaffoldError(f"预设测试台不存在：{preset_id}", 404)

    process: Optional[dict] = None
    if str(process_id or "").strip():
        process = processes.get_process(process_id)
        if process is None:
            raise ScaffoldError(f"工序不存在：{process_id}", 400)

    bom = [x for x in preset.get("bom") or [] if isinstance(x, dict)]
    bom_by_alias = {str(x.get("alias") or x.get("id") or ""): x for x in bom}
    bom_by_alias.pop("", None)

    wanted = [str(a).strip() for a in (device_aliases or []) if str(a).strip()]
    if not wanted:
        wanted = list(bom_by_alias.keys())
    unknown = [a for a in wanted if a not in bom_by_alias]
    if unknown:
        raise ScaffoldError(
            f"device_aliases 不在预设 {preset.get('id')} 的 BOM 内：{'、'.join(unknown)}", 400
        )
    device_config = {
        alias: {
            "device_type": str(bom_by_alias[alias].get("device_type") or bom_by_alias[alias].get("category") or ""),
            "resource": str(bom_by_alias[alias].get("resource") or bom_by_alias[alias].get("default_address") or ""),
            # tps_schema 要求 device_config 条目至少含 device_id/role/category/model 之一
            "role": alias,
        }
        for alias in wanted
    }

    resolved_id = str(tps_id or "").strip() or _default_id(str(preset.get("id") or ""))
    resolved_name = str(name or "").strip() or f"{preset.get('name') or preset.get('id')} TPS"
    module = _module_name(str(preset.get("id") or ""))

    manifest: dict[str, Any] = {
        "schema": "tps.v2",
        "id": resolved_id,
        "name": resolved_name,
        "version": "1.0.0",
        "description": preset.get("description", ""),
        "preset_id": preset.get("id", ""),
        "product": preset.get("product", ""),
        "subsystem": preset.get("subsystem", ""),
        "project": preset.get("product_name") or preset.get("product", ""),
        "dut": preset.get("typical_dut", ""),
        "parser": {"name": parser_def["name"], "version": parser_def["runtime"]},
        "device_config": device_config,
        "testconfig": {"TC_MAIN": {"label": "主判据", "min": 0, "max": 1, "unit": ""}},
        "setup": [
            {"id": "SETUP01", "name": "上电与夹具准备", "case": f"testcase.{module}::setup"},
        ],
        "cmd_suit": [
            {
                "id": "TC001",
                "name": "主判据采集",
                "case": f"testcase.{module}::main",
                "checks": {"value": "TC_MAIN"},
            },
        ],
        "teardown": [
            {"id": "TD01", "name": "断电与资源释放", "case": f"testcase.{module}::teardown"},
        ],
    }
    if process:
        manifest["process"] = {
            "id": process.get("id", ""),
            "code": process.get("code", ""),
            "name": process.get("name", ""),
        }

    model_keys = [str(m).strip() for m in (models or []) if str(m).strip()]
    version = str(constant_version or "").strip()
    pn_value = str(pn or "").strip()
    if version and pn_value and model_keys:
        manifest["constant_model"] = {"version": version, "pn": pn_value, "models": model_keys}

    # 生成即校验：save=false 时也提前暴露 id / device_config / 用例引用问题
    if not save:
        tps_store._validate(manifest)

    file_path = f"testcase/{module}.py"
    file_content = _template(preset, module, process)
    result: dict[str, Any] = {
        "ok": True,
        "tps": manifest,
        "files": [{"path": file_path, "content": file_content}],
        "saved": False,
        "context": None,
        "warnings": [],
    }

    if save:
        summary = tps_store.save(manifest)
        tps_store.write_file(resolved_id, file_path, file_content)
        stored = tps_store.load(resolved_id)
        result.update(
            {
                "saved": True,
                "tps": stored,
                "summary": summary,
                "warnings": summary.get("warnings", []),
                "context": context(stored),
            }
        )
    return result
