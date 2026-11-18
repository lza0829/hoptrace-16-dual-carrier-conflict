from app.db import utcnow
from app.seed import map_status


def list_primary(conn, waybill_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, waybill_id, event_ts, location_code, event_code, seq, channel, retracted
        FROM hops
        WHERE waybill_id=? AND channel='primary' AND retracted=0
        ORDER BY event_ts ASC, id ASC
        """,
        (waybill_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def refresh_projection(conn, waybill_id: str) -> None:
    """按正式线末 hop 重算状态投影并写证明 —— 与时间线同源。"""
    row = conn.execute(
        """
        SELECT id, event_code FROM hops
        WHERE waybill_id=? AND channel='primary' AND retracted=0
        ORDER BY event_ts DESC, id DESC LIMIT 1
        """,
        (waybill_id,),
    ).fetchone()
    proj = map_status(row["event_code"]) if row else "未知"
    conn.execute("UPDATE waybills SET status_proj=? WHERE id=?", (proj, waybill_id))
    conn.execute(
        "INSERT INTO status_proofs(waybill_id, last_hop_id, mapped_code, proj_value, created_at)"
        " VALUES (?,?,?,?,?)",
        (waybill_id, row["id"] if row else None, row["event_code"] if row else None,
         proj, utcnow()),
    )
