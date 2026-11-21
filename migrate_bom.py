"""BOM 三要素迁移：把历史 BOM 补齐为「别名 + 设备类型 + 资源描述符」。

背景
----
旧 BOM 条目只有 name/model/category/interface/default_* 等字段，
`device_type` 曾被用作设备型号键（如 tektronix-mso5 / generic-dmm），
与新的「设备类型」（示波器 / 万用表 …）语义冲突，且没有 alias 与 resource。

迁移规则
--------
* `device_type_legacy` ← 旧 `device_type`（保留追溯，不参与校验）
* `device_type` ← 由 `category` 按映射表转为标准中文名（未命中归入「其他（VISA / 自定义）」）
* `alias` ← 由 `id` 生成 ASCII 别名（如 rh-scope），非法字符转下划线；冲突时追加序号
* `resource` ← 由既有连接参数生成：
    - 已有 default_address 且含 `::` 或形如 host:port → 直接用
    - LAN/ETHERNET/TCP/IP/SOCKET + host + port → `TCPIP0::host::port::SOCKET`
    - LAN + host（无端口）→ `TCPIP0::host::INSTR`
    - SERIAL/RS232/RS485/UART/COM + 串口 → `COM3:9600:8N1`
    - 其余（夹具/工装等）→ 留空

用法
----
    python migrate_bom.py              # 干跑，只打印将要做的事
    python migrate_bom.py --apply      # 实际写入，先备份到 bom/_backup/<时间戳>/

幂等：已有 alias 且有 resource 的条目视为已迁移，跳过；重复执行不会二次改写。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent / "repo"
BOM_DIR = REPO_ROOT / "tree" / "bom"

# 类别 → 标准设备类型（名称取自 metadata_templates.DEVICE_TYPES）
CATEGORY_TO_TYPE = {
    "示波器": "示波器",
    "万用表": "万用表",
    "电源": "可编程电源",
    "电流源": "可编程电源",
    "电子负载": "电子负载",
    "信号源": "信号发生器",
    "运动控制": "运动控制器",
    "夹具": "夹具/工装",
    "台架": "夹具/工装",
    "串口": "串口设备",
    "数据采集": "数据采集",
    "环境箱": "温控设备",
    "开关矩阵": "开关矩阵",
    "功率分析": "其他（VISA / 自定义）",
    "安规测试": "其他（VISA / 自定义）",
    "频谱仪": "其他（VISA / 自定义）",
    "总线分析": "其他（VISA / 自定义）",
}

TYPE_TO_KEY = {
    "示波器": "scope",
    "信号发生器": "awg",
    "万用表": "dmm",
    "可编程电源": "psu",
    "电子负载": "eload",
    "开关矩阵": "sw",
    "数据采集": "daq",
    "温控设备": "thermal",
    "运动控制器": "motion",
    "串口设备": "serial",
    "夹具/工装": "fixture",
    "其他（VISA / 自定义）": "other",
}

NET_IFACES = {"LAN", "ETHERNET", "TCP", "IP", "SOCKET"}
SERIAL_IFACES = {"SERIAL", "RS232", "RS485", "UART", "COM"}
ALIAS_OK = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def _alias_from_id(item_id: str, fallback_key: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item_id or "")).strip("_")
    if text and ALIAS_OK.match(text[:40]):
        return text[:40]
    return f"{fallback_key}_1"


def _resource_of(item: dict) -> str:
    if item.get("resource"):
        return str(item["resource"])

    address = str(item.get("default_address") or "").strip()
    iface = str(item.get("interface") or "").upper()
    host = str(item.get("default_host") or "").strip()
    port = item.get("default_port")
    serial = str(item.get("default_serial_port") or "").strip()
    baud = item.get("default_baudrate")

    if address and ("::" in address or re.search(r":\d+$", address)):
        return address
    if iface in NET_IFACES and host:
        return f"TCPIP0::{host}::{port}::SOCKET" if port else f"TCPIP0::{host}::INSTR"
    if iface in SERIAL_IFACES and serial:
        return f"{serial}:{baud or 9600}:8N1"
    if address:
        return address
    return ""


def migrate_file(path: Path, apply: bool) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {"preset": path.stem, "changed": 0, "skipped": 0, "items": []}

    changed = 0
    skipped = 0
    report: list[dict] = []
    used_alias: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        old_type = str(item.get("device_type") or "").strip()
        new_type = CATEGORY_TO_TYPE.get(str(item.get("category") or "").strip(), "其他（VISA / 自定义）")
        # 已迁移：有 alias，且（resource 非空 或 device_type 已是标准名）——夹具类 resource 本可为空
        already = bool(item.get("alias")) and (
            bool(item.get("resource")) or str(item.get("device_type") or "").strip() == new_type
        )
        if already:
            skipped += 1
            used_alias.add(str(item.get("alias")))
            continue

        alias = str(item.get("alias") or "").strip()
        if not alias or not ALIAS_OK.match(alias):
            base = _alias_from_id(item.get("id"), TYPE_TO_KEY.get(new_type, "dev"))
            alias = base
            n = 2
            while alias in used_alias:
                alias = f"{base}_{n}"
                n += 1
        used_alias.add(alias)

        resource = _resource_of(item)

        if not apply:
            report.append(
                {
                    "id": item.get("id"),
                    "alias": alias,
                    "device_type": f"{old_type} → {new_type}",
                    "resource": resource or "（留空）",
                }
            )
            changed += 1
            continue

        if old_type and old_type != new_type and not item.get("device_type_legacy"):
            item["device_type_legacy"] = old_type
        item["device_type"] = new_type
        item["alias"] = alias
        item["resource"] = resource
        report.append({"id": item.get("id"), "alias": alias, "device_type": new_type, "resource": resource or "（留空）"})
        changed += 1

    if apply and changed:
        if isinstance(payload, dict):
            payload["items"] = items
            payload["count"] = len(items)
            payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload["migration"] = "bom-3fields-v1"
        else:
            payload = items
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"preset": path.stem, "changed": changed, "skipped": skipped, "items": report}


def main() -> int:
    parser = argparse.ArgumentParser(description="BOM 三要素迁移（别名 / 设备类型 / 资源描述符）")
    parser.add_argument("--apply", action="store_true", help="实际写入；不加为干跑")
    parser.add_argument("--bom-dir", default=str(BOM_DIR), help="BOM 目录，默认 <程序目录>/repo/tree/bom")
    args = parser.parse_args()

    bom_dir = Path(args.bom_dir)
    files = sorted(p for p in bom_dir.glob("*.json") if p.is_file())
    if not files:
        print(f"未找到 BOM 文件：{bom_dir}")
        return 1

    if args.apply:
        backup = bom_dir / "_backup" / datetime.now().strftime("%Y%m%d%H%M%S")
        backup.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.copy2(path, backup / path.name)
        print(f"已备份 {len(files)} 个 BOM 文件 → {backup}\n")

    total_changed = 0
    total_skipped = 0
    for path in files:
        result = migrate_file(path, args.apply)
        total_changed += result["changed"]
        total_skipped += result["skipped"]
        if result["changed"]:
            print(f"[{result['preset']}] 迁移 {result['changed']} 条")
            for row in result["items"]:
                print(f"    {row['id']:<18} alias={row['alias']:<16} 类型={row['device_type']:<22} 资源={row['resource']}")
        elif result["skipped"]:
            print(f"[{result['preset']}] 已迁移过（{result['skipped']} 条），跳过")

    mode = "已写入" if args.apply else "干跑（未写入，加 --apply 生效）"
    print(f"\n合计：迁移 {total_changed} 条，跳过 {total_skipped} 条 · {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
