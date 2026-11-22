"""Controller API v1：TPS 编辑、工序清单、元数据、能力协商。

Runner 只读这些接口；Web 端执行写操作。
"""

from __future__ import annotations

import base64

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import assistants
import constants
import constants_export
import processes
import tps_scaffold
import tps_store

router = APIRouter(prefix="/api/v1", tags=["v1"])


class TpsImport(BaseModel):
    content: str
    overwrite: bool = True


class TpsZipImport(BaseModel):
    name: str = ""
    content_base64: str
    overwrite: bool = True


class TpsSave(BaseModel):
    tps: dict[str, Any]
    overwrite: bool = True


class TpsFileWrite(BaseModel):
    content: str
    encoding: str = "utf-8"


class TpsSopSave(BaseModel):
    """人工步骤 SOP：写 resource/<filename 或 step_id>.md 并回填条目的 resource。"""

    step_id: str
    title: str = ""
    markdown: str = ""
    filename: str = ""


class ProcessUpsert(BaseModel):
    id: str
    code: str = ""
    name: str = ""
    stage: str = ""
    description: str = ""
    typical_minutes: Optional[int] = None
    product_scope: list[str] = []


class ScaffoldRequest(BaseModel):
    preset_id: str
    process_id: str = ""
    id: str = ""
    name: str = ""
    # 多行绑定优先；constant_version/pn/models 为旧的单行写法，兼容保留
    bindings: list[dict[str, Any]] = []
    constant_version: str = ""
    pn: str = ""
    models: list[str] = []
    device_aliases: list[str] = []
    parser: str = tps_scaffold.DEFAULT_PARSER
    save: bool = False


class ConstantFile(BaseModel):
    name: str = ""
    content_base64: str = ""


class ConstantsBundleImport(BaseModel):
    subsystem: str = ""
    product: str = ""
    mode: str = "overwrite"
    version: str = ""
    files: list[ConstantFile] = []
    filename: str = ""


class ConstantsZipImport(BaseModel):
    subsystem: str = ""
    product: str = ""
    mode: str = "overwrite"
    version: str = ""
    filename: str = ""
    content_base64: str = ""


class ConstantsFileImport(BaseModel):
    subsystem: str = ""
    product: str = ""
    mode: str = "overwrite"
    version: str = ""
    filename: str = ""
    content: str = ""
    content_base64: str = ""


@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "MockController",
        "api": "v1",
        "tps": tps_store.stats(),
        "assistants": assistants.stats(),
    }


@router.get("/capabilities")
def capabilities() -> dict:
    return {
        "ok": True,
        "features": ["packages", "tps", "metadata", "constants", "processes"],
        "tps_schema": ["tps.v1", "tps.v2"],
        "tps_parsers": tps_scaffold.parser_names(),
    }


# ---------------------------------------------------------------- 工序清单


@router.get("/processes")
def list_processes() -> dict:
    items = processes.list_processes()
    return {"ok": True, "count": len(items), "processes": items}


@router.get("/processes/{process_id}")
def get_process(process_id: str) -> dict:
    item = processes.get_process(process_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"工序不存在：{process_id}")
    return {"ok": True, "process": item}


@router.post("/processes")
def upsert_process(req: ProcessUpsert) -> dict:
    try:
        # 只合并客户端实际给出的字段，未传的字段保留原值
        item = processes.upsert_process(req.model_dump(exclude_unset=True))
    except processes.ProcessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True, "process": item}


@router.delete("/processes/{process_id}")
def delete_process(process_id: str) -> dict:
    try:
        message = processes.delete_process(process_id)
    except processes.ProcessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True, "message": message}


# ---------------------------------------------------------------- TPS 清单


@router.get("/tps")
def list_tps() -> dict:
    items = tps_store.list_tps()
    return {"ok": True, "count": len(items), "tps": items}


# 注意：/tps/parsers、/tps/scaffold 必须先于 /tps/{tps_id} 声明


@router.get("/tps/parsers")
def tps_parsers() -> dict:
    return {"ok": True, "parsers": tps_scaffold.parser_list()}


@router.post("/tps/scaffold")
def tps_scaffold_create(req: ScaffoldRequest) -> dict:
    try:
        return tps_scaffold.scaffold(
            preset_id=req.preset_id,
            process_id=req.process_id,
            tps_id=req.id,
            name=req.name,
            constant_version=req.constant_version,
            pn=req.pn,
            models=req.models,
            bindings=req.bindings,
            device_aliases=req.device_aliases,
            parser=req.parser,
            save=req.save,
        )
    except tps_scaffold.ScaffoldError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tps/{tps_id}")
def get_tps(tps_id: str) -> dict:
    try:
        return {"ok": True, "tps": tps_store.load(tps_id)}
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tps/{tps_id}/context")
def tps_context(tps_id: str) -> dict:
    """编辑器一次取全：清单 + 预设/BOM + 工序 + 解析器 + 机器常数。"""
    try:
        raw = tps_store.load(tps_id)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tps_scaffold.context(raw)


@router.put("/tps/{tps_id}")
def put_tps(tps_id: str, req: TpsSave) -> dict:
    tps = dict(req.tps)
    if str(tps.get("id") or "") != tps_id:
        raise HTTPException(status_code=400, detail="路径中的 id 与清单 id 不一致")
    try:
        summary = tps_store.save(tps, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings = summary.pop("warnings", [])
    return {"ok": True, "tps": summary, "warnings": warnings, "message": f"已保存 {tps_id}"}


@router.post("/tps/import")
def import_tps(req: TpsImport) -> dict:
    try:
        summary = tps_store.import_json(req.content, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings = summary.pop("warnings", [])
    return {"ok": True, "tps": summary, "warnings": warnings, "message": f"TPS 导入成功: {summary['id']}"}


@router.post("/tps/import/zip")
def import_tps_zip(req: TpsZipImport) -> dict:
    try:
        summary = tps_store.import_zip(req.name, req.content_base64, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings = summary.pop("warnings", [])
    return {"ok": True, "tps": summary, "warnings": warnings, "message": f"TPS 导入成功: {summary['id']}"}


# ---------------------------------------------------------------- 常数按子系统导入


def _decode_files(files: list[ConstantFile]) -> list[tuple[str, bytes]]:
    """把 [{name, content_base64}] 解成 [(文件名, 字节)]；解码失败直接 400。"""
    parts: list[tuple[str, bytes]] = []
    errors: list[str] = []
    for index, item in enumerate(files or []):
        name = str(item.name or "").strip() or f"文件{index + 1}.json"
        if not str(item.content_base64 or "").strip():
            errors.append(f"{name}: 缺少 content_base64")
            continue
        try:
            parts.append((name, base64.b64decode(item.content_base64, validate=True)))
        except Exception as exc:  # noqa: BLE001 - 非法 base64
            errors.append(f"{name}: base64 解码失败（{exc}）")
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    return parts


@router.get("/constants/subsystems")
def constants_subsystems(version: str = "") -> dict:
    """装备树里的全部子系统（各自带上常数库中出现的 PN）。"""
    try:
        items = constants.subsystems_view(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "items": items}


@router.post("/constants/import-bundle")
def constants_import_bundle(req: ConstantsBundleImport) -> dict:
    """多文件常数模型 → 合并为一个 bundle → 一次落库（全成功或全不动）。"""
    parts = _decode_files(req.files)
    if not parts:
        raise HTTPException(status_code=400, detail="files 不能为空")
    try:
        result = constants.import_files(
            parts,
            product=req.product,
            subsystem=req.subsystem,
            version=req.version,
            mode=req.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        return {"ok": False, "version": result.get("version", ""), **result,
                "filename": req.filename}
    return {
        "ok": True,
        "version": result["version"],
        "stats": result["stats"],
        "imported_files": result["imported_files"],
        "warnings": result.get("warnings") or [],
        "success": True,
        "overwritten": result.get("overwritten", False),
        "imported_values": result.get("imported_values", 0),
        "restored_values": result.get("restored_values", 0),
        "message": result.get("message", ""),
    }


@router.post("/constants/import-zip")
def constants_import_zip(req: ConstantsZipImport) -> dict:
    """ZIP（20MB / 200 个 JSON 文件上限）→ 逐文件解析合并 → 一次落库。"""
    raw_text = str(req.content_base64 or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="缺少 content_base64")
    try:
        blob = base64.b64decode(raw_text, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"content_base64 解码失败（{exc}）") from exc
    if len(blob) > constants.IMPORT_MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"压缩包超过 {constants.IMPORT_MAX_ZIP_BYTES // (1024 * 1024)}MB 上限"
                   f"（实际 {len(blob)} 字节）",
        )
    try:
        result = constants.import_zip_files(
            blob,
            product=req.product,
            subsystem=req.subsystem,
            version=req.version,
            mode=req.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        return {"ok": False, "version": result.get("version", ""), **result,
                "filename": req.filename}
    return {
        "ok": True,
        "version": result["version"],
        "stats": result["stats"],
        "imported_files": result["imported_files"],
        "zip_files": result["zip_files"],
        "warnings": result.get("warnings") or [],
        "success": True,
        "overwritten": result.get("overwritten", False),
        "imported_values": result.get("imported_values", 0),
        "restored_values": result.get("restored_values", 0),
        "message": result.get("message", ""),
    }


@router.post("/constants/import-file")
def constants_import_file(req: ConstantsFileImport) -> dict:
    """单个常数模型文件（无需 zip）。content_base64 优先，其次 content。"""
    name = str(req.filename or "").strip() or "constants.json"
    if str(req.content_base64 or "").strip():
        parts = _decode_files([ConstantFile(name=name, content_base64=req.content_base64)])
    elif isinstance(req.content, str) and req.content.strip():
        parts = [(name, req.content.encode("utf-8"))]
    else:
        raise HTTPException(status_code=400, detail="缺少 content 或 content_base64")
    try:
        result = constants.import_files(
            parts,
            product=req.product,
            subsystem=req.subsystem,
            version=req.version,
            mode=req.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        return {"ok": False, "version": result.get("version", ""), **result, "filename": name}
    return {
        "ok": True,
        "version": result["version"],
        "stats": result["stats"],
        "imported_files": result["imported_files"],
        "warnings": result.get("warnings") or [],
        "success": True,
        "overwritten": result.get("overwritten", False),
        "imported_values": result.get("imported_values", 0),
        "restored_values": result.get("restored_values", 0),
        "message": result.get("message", ""),
    }


@router.post("/tps/{tps_id}/sop")
def tps_save_sop(tps_id: str, req: TpsSopSave) -> dict:
    if not tps_store.exists(tps_id):
        raise HTTPException(status_code=404, detail=f"TPS 不存在：{tps_id}")
    try:
        return tps_store.save_sop(
            tps_id, req.step_id, req.markdown, title=req.title, filename=req.filename
        )
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tps/{tps_id}/files")
def tps_files(tps_id: str) -> dict:
    try:
        return {"ok": True, "files": tps_store.list_files(tps_id)}
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/constants/export")
def export_constants(version: str = "") -> dict:
    """导出常数版本全量包，供 Runner 同步本地运行库"""
    try:
        bundle = constants_export.export_version(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "bundle": bundle}


@router.get("/tps/{tps_id}/files/{path:path}")
def tps_file(tps_id: str, path: str) -> Response:
    try:
        data = tps_store.read_file(tps_id, path)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="application/octet-stream")


@router.put("/tps/{tps_id}/files/{path:path}")
def put_tps_file(tps_id: str, path: str, req: TpsFileWrite) -> dict:
    """写入代码 / 文档文件（仅 .py/.md/.txt/.json/.yaml/.yml，2MB 内）。"""
    if not tps_store.exists(tps_id):
        raise HTTPException(status_code=404, detail=f"TPS 不存在：{tps_id}")
    try:
        info = tps_store.write_file(tps_id, path, req.content, req.encoding)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **info}


@router.delete("/tps/{tps_id}/files/{path:path}")
def delete_tps_file(tps_id: str, path: str) -> dict:
    if not tps_store.exists(tps_id):
        raise HTTPException(status_code=404, detail=f"TPS 不存在：{tps_id}")
    try:
        message = tps_store.delete_file(tps_id, path)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": message}
