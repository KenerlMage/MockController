"""迁移脚本：把 Runner 本地货架（catalog.json + packages/）迁入 Controller。

Runner 侧的货架清单与安装包原本随程序本地持有（assistant_shelf/catalog.json 与
packages/<id>/），等价于远端下发的结果。迁移后 Controller 成为唯一事实源，
Runner 改为通过 /api/v1/assistants 拉取。

用法（在 D:\\ATE\\MockController 下）：
    python seed_assistants.py
    python seed_assistants.py --source D:\\ATE\\MockRunner\\backend\\assistant_shelf
    python seed_assistants.py --repo D:\\tmp\\repo --channel demo

可重复执行：每个助手先写到货架目录内的临时目录，算完 sha256 再整体替换，
因此重跑只会刷新内容，不会留下半成品目录；catalog 中已移除的助手不自动删除，
仅在结尾提示仍留在货架上的条目。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import assistants

DEFAULT_SOURCE = Path(r"D:\ATE\MockRunner\backend\assistant_shelf")
DEFAULT_NOTE = "助手清单由 Controller 统一发布；Runner 通过 /api/v1/assistants 拉取清单与文件。"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _file_paths(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _merge_meta(item: dict, package_meta: dict, paths: set[str]) -> dict:
    """catalog 提供展示字段，包内 assistant.json 提供入口与实际运行方式。

    size_hint 不照搬 catalog：它是人读体积，按实际文件统计生成，
    避免与实际字节数矛盾（catalog 中的旧值在结尾告警）。
    """
    meta: dict = {
        key: item[key]
        for key in assistants.META_FIELDS
        if key in item and key != "size_hint"
    }
    entry = str(package_meta.get("entry") or item.get("entry") or "").strip()
    if entry in paths:
        meta["entry"] = entry
    elif "start.bat" in paths:
        print(f"  [warn] {item.get('id')}: entry={entry or '空'} 不在包内，回退 start.bat")
        meta["entry"] = "start.bat"
    else:
        print(f"  [warn] {item.get('id')}: 包内无可用入口，entry 留空")
        meta["entry"] = ""
    icon = str(package_meta.get("icon") or item.get("icon_file") or assistants.DEFAULT_ICON).strip()
    meta["icon_file"] = icon or assistants.DEFAULT_ICON
    meta["self_contained"] = bool(item.get("self_contained") or package_meta.get("self_contained"))
    return meta


def seed(source: Path, repo: Path, channel: str, note: str) -> int:
    catalog_path = source / "catalog.json"
    if not catalog_path.is_file():
        print(f"[error] 找不到货架清单：{catalog_path}")
        return 2
    catalog = _read_json(catalog_path, default={}) or {}
    items = catalog.get("items") or []
    if not isinstance(items, list) or not items:
        print(f"[error] 货架清单为空或格式不对：{catalog_path}")
        return 2

    assistants.ASSIST_ROOT = (repo / "assistants").resolve()
    assistants.ensure_root()
    print(f"源目录：{source}")
    print(f"目标目录：{assistants.ASSIST_ROOT}")

    published: list[str] = []
    total_files = 0
    total_bytes = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            print("  [warn] 清单条目缺少 id，已跳过")
            continue
        try:
            assistant_id = assistants.safe_id(str(item["id"]))
        except assistants.AssistError as exc:
            print(f"  [warn] {exc}")
            continue
        package = source / "packages" / assistant_id
        if not package.is_dir():
            print(f"  [warn] {assistant_id}: 缺少安装包目录 {package}，已跳过")
            continue

        paths = _file_paths(package)
        package_meta = _read_json(package / "assistant.json", default={}) or {}
        meta = _merge_meta(item, package_meta, paths)

        staging = assistants.staging_dir(assistant_id)
        try:
            shutil.copytree(package, staging / "files")
            result = assistants.commit(assistant_id, staging, meta)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        published.append(assistant_id)
        total_files += result["file_count"]
        total_bytes += result["size_bytes"]
        print(
            f"  {assistant_id:18s} {result['name']} v{result['version']}  "
            f"{result['file_count']:4d} 个文件  {result['size_bytes']:>11d} 字节  "
            f"({result['size_hint']})"
        )
        declared = str(item.get("size_hint") or "").strip()
        if declared and declared != result["size_hint"]:
            print(
                f"  [warn] {assistant_id}: catalog 标注 {declared}，实际 {result['size_hint']}，"
                "已按实际体积发布"
            )

    assistants.write_shelf(channel=channel, note=note)

    stale = [
        entry.name
        for entry in assistants.ASSIST_ROOT.iterdir()
        if entry.is_dir() and not entry.name.startswith("_") and entry.name not in published
    ]
    print(
        f"完成：{len(published)} 个助手 / {total_files} 个文件 / {total_bytes} 字节 "
        f"（{assistants.fmt_size(total_bytes)}）"
    )
    if stale:
        print(f"[note] 货架上还有 catalog 之外的助手，未删除：{', '.join(sorted(stale))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 Runner 本地助手货架迁入 Controller 仓库")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Runner 货架目录（含 catalog.json 与 packages/）")
    parser.add_argument("--repo", default=str(assistants.REPO_ROOT), help="Controller 数据根，默认取 MOCK_CONTROLLER_REPO 或程序目录 repo")
    parser.add_argument("--channel", default=assistants.DEFAULT_CHANNEL, help="货架渠道标识，默认 stable")
    parser.add_argument("--note", default=DEFAULT_NOTE, help="货架备注")
    args = parser.parse_args(argv)
    return seed(Path(args.source).resolve(), Path(args.repo).resolve(), args.channel, args.note)


if __name__ == "__main__":
    sys.exit(main())
