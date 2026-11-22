# -*- coding: utf-8 -*-
"""装备软件归档助手（SR01-AR08）。

把一个源目录打成 Controller 可直接接收的标准包，并按类型做与服务端同源的校验。

用法：
    python tools/archive_assistant.py \
        --kind driver --product 1000-A --subsystem SPM \
        --name spm_driver --version 2.1.0 --src ./pkg --out ./dist

目录要求（driver）：scripts/probe、scripts/install
目录要求（assistant）：scripts/probe、scripts/install、scripts/uninstall
三方软件：可无脚本，--auto-deploy 标记随 TPS 部署。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import software  # noqa: E402

DEFAULT_SCRIPTS = {
    "driver": {"probe": "scripts/probe", "install": "scripts/install"},
    "assistant": {"probe": "scripts/probe", "install": "scripts/install",
                  "uninstall": "scripts/uninstall"},
    "thirdparty": {},
}


def _find_script(src: Path, base: str) -> str:
    for ext in (".bat", ".cmd", ".ps1", ".sh", ".py", ""):
        rel = f"{base}{ext}"
        if (src / rel).is_file():
            return rel
    return ""


def build_manifest(args, src: Path) -> dict:
    scripts = {}
    for key, base in DEFAULT_SCRIPTS[args.kind].items():
        found = _find_script(src, base)
        if found:
            scripts[key] = found
    manifest = {
        "kind": args.kind,
        "product": args.product,
        "subsystem": args.subsystem,
        "name": args.name,
        "version": args.version,
        "vendor": args.vendor or "",
        "license": args.license or "",
        "needs_deploy": bool(args.needs_deploy) if args.kind == "assistant" else False,
        "auto_deploy": bool(args.auto_deploy) if args.kind == "thirdparty" else False,
        "scripts": scripts,
    }
    return manifest


def pack(args) -> dict:
    src = Path(args.src).resolve()
    if not src.is_dir():
        return {"success": False, "message": f"源目录不存在：{src}"}
    manifest = build_manifest(args, src)
    files = [p for p in src.rglob("*") if p.is_file()]
    if not files:
        return {"success": False, "message": "源目录没有文件"}

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.name}-{args.version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in files:
            rel = path.relative_to(src).as_posix()
            if rel == "manifest.json":
                continue
            zf.write(path, rel)

    blob = target.read_bytes()
    payload = {
        "kind": args.kind, "product": args.product, "subsystem": args.subsystem,
        "name": args.name, "version": args.version,
        "content_base64": base64.b64encode(blob).decode(),
        "needs_deploy": manifest["needs_deploy"], "auto_deploy": manifest["auto_deploy"],
        "scripts": manifest["scripts"],
    }
    checked = software.verify_archive(payload)
    if not checked.get("success"):
        target.unlink(missing_ok=True)
        return {"success": False, "message": f"校验未通过：{checked.get('message')}"}
    return {
        "success": True,
        "message": f"已生成 {target}",
        "path": str(target),
        "size": len(blob),
        "sha256": checked.get("sha256"),
        "scripts": checked.get("scripts"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="装备软件归档助手")
    parser.add_argument("--kind", required=True, choices=["driver", "assistant", "thirdparty"])
    parser.add_argument("--product", required=True)
    parser.add_argument("--subsystem", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--src", required=True, help="待归档的源目录")
    parser.add_argument("--out", default="dist", help="输出目录")
    parser.add_argument("--vendor", default="")
    parser.add_argument("--license", default="")
    parser.add_argument("--needs-deploy", action="store_true", help="装备助手：是否需要部署")
    parser.add_argument("--auto-deploy", action="store_true", help="三方软件：随 TPS 部署")
    args = parser.parse_args(argv)

    result = pack(args)
    print(result["message"])
    if result.get("sha256"):
        print(f"sha256: {result['sha256']}")
        print(f"scripts: {', '.join((result.get('scripts') or {}).keys())}")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
