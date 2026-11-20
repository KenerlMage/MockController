"""Controller API v1：TPS 编辑、元数据、能力协商。

Runner 只读这些接口；Web 端执行写操作。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import assistants
import constants_export
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
        "features": ["packages", "tps", "metadata", "constants"],
        "tps_schema": ["tps.v1", "tps.v2"],
    }


@router.get("/tps")
def list_tps() -> dict:
    items = tps_store.list_tps()
    return {"ok": True, "count": len(items), "tps": items}


@router.get("/tps/{tps_id}")
def get_tps(tps_id: str) -> dict:
    try:
        return {"ok": True, "tps": tps_store.load(tps_id)}
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/tps/{tps_id}")
def put_tps(tps_id: str, req: TpsSave) -> dict:
    tps = dict(req.tps)
    if str(tps.get("id") or "") != tps_id:
        raise HTTPException(status_code=400, detail="路径中的 id 与清单 id 不一致")
    try:
        summary = tps_store.save(tps, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "tps": summary, "message": f"已保存 {tps_id}"}


@router.post("/tps/import")
def import_tps(req: TpsImport) -> dict:
    try:
        summary = tps_store.import_json(req.content, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "tps": summary, "message": f"TPS 导入成功: {summary['id']}"}


@router.post("/tps/import/zip")
def import_tps_zip(req: TpsZipImport) -> dict:
    try:
        summary = tps_store.import_zip(req.name, req.content_base64, overwrite=req.overwrite)
    except tps_store.TpsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "tps": summary, "message": f"TPS 导入成功: {summary['id']}"}


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
