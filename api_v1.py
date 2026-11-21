"""Controller API v1：TPS 编辑、工序清单、元数据、能力协商。

Runner 只读这些接口；Web 端执行写操作。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import assistants
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
    constant_version: str = ""
    pn: str = ""
    models: list[str] = []
    device_aliases: list[str] = []
    parser: str = tps_scaffold.DEFAULT_PARSER
    save: bool = False


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
