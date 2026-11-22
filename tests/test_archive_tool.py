"""归档助手 CLI（SR01-AR08）：产出包必须能通过服务端同源校验。"""
import zipfile
from pathlib import Path

import software
from tools import archive_assistant


def _src(tmp: Path, kind="driver") -> Path:
    root = tmp / "src"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "probe.bat").write_text("@echo off\r\nexit /b 10\r\n", encoding="utf-8")
    (root / "scripts" / "install.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    if kind == "assistant":
        (root / "scripts" / "uninstall.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "main.exe").write_bytes(b"MZ")
    return root


def _args(src: Path, out: Path, kind="driver"):
    import argparse

    return argparse.Namespace(
        kind=kind, product="1000-A", subsystem="SPM", name=f"e2e_{kind}", version="1.0.0",
        src=str(src), out=str(out), vendor="", license="",
        needs_deploy=True, auto_deploy=False,
    )


def test_pack_driver_and_validate(tmp_path):
    result = archive_assistant.pack(_args(_src(tmp_path), tmp_path / "dist"))
    assert result["success"], result
    assert Path(result["path"]).is_file()
    with zipfile.ZipFile(result["path"]) as zf:
        assert "manifest.json" in zf.namelist()
        assert "scripts/probe.bat" in zf.namelist()
    assert set(result["scripts"]) == {"probe", "install"}


def test_pack_assistant_needs_three_scripts(tmp_path):
    result = archive_assistant.pack(_args(_src(tmp_path, "assistant"), tmp_path / "dist", "assistant"))
    assert result["success"], result
    assert set(result["scripts"]) == {"probe", "install", "uninstall"}


def test_pack_missing_script_is_rejected_and_not_left_behind(tmp_path):
    src = _src(tmp_path)
    (src / "scripts" / "install.bat").unlink()
    result = archive_assistant.pack(_args(src, tmp_path / "dist"))
    assert result["success"] is False
    assert "install" in result["message"]
    assert not list((tmp_path / "dist").glob("*.zip"))
