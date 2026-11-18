"""冲突证明只读接口（GET only）。"""
from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules.dual_carrier_conflict import proofs

router = APIRouter(prefix="/api/conflicts", tags=["dual_carrier_conflict"])


@router.get("")
def list_conflicts():
    conn = connect()
    try:
        return proofs.list_conflicts(conn)
    finally:
        conn.close()


@router.get("/{group_id}")
def conflict_proof(group_id: str):
    conn = connect()
    try:
        detail = proofs.get_conflict(conn, group_id)
        if not detail:
            raise HTTPException(404, "冲突组不存在")
        return detail
    finally:
        conn.close()
