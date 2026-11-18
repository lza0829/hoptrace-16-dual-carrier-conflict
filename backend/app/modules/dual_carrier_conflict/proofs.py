"""冲突证明只读查询：冲突表为唯一数据源，供接口 / 冲突页 / 检查器共用。"""
import json


def list_conflicts(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM conflict_groups ORDER BY created_at DESC, id"
    ).fetchall()
    return [_with_members(conn, dict(r)) for r in rows]


def get_conflict(conn, group_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM conflict_groups WHERE id=?", (group_id,)).fetchone()
    if not row:
        return None
    detail = _with_members(conn, dict(row))
    q = conn.execute(
        "SELECT id, waybill_id, reason, member_json, batch_id, created_at"
        " FROM quarantine WHERE group_ref=?",
        (group_id,),
    ).fetchone()
    detail["quarantine"] = dict(q) if q else None
    detail["divergence"] = _divergence_matrix(detail["members"], detail["divergent_fields"])
    return detail


def conflicts_for_waybill(conn, waybill_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM conflict_groups WHERE waybill_id=? ORDER BY id", (waybill_id,)
    ).fetchall()
    return [_with_members(conn, dict(r)) for r in rows]


def _with_members(conn, g: dict) -> dict:
    members = conn.execute(
        "SELECT * FROM conflict_members WHERE group_id=? ORDER BY id", (g["id"],)
    ).fetchall()
    member_list = [_member(dict(m)) for m in members]
    return {
        "id": g["id"],
        "waybill_id": g["waybill_id"],
        "batch_id": g["batch_id"],
        "axis_type": g["axis_type"],
        "axis_value": g["axis_value"],
        "window_seconds": g["window_seconds"],
        "divergent_fields": json.loads(g["divergent_fields"]),
        "created_at": g["created_at"],
        "member_count": len(member_list),
        "sources": sorted({m["source"] for m in member_list}),
        "members": member_list,
    }


def _member(m: dict) -> dict:
    return {
        "hop_id": m["hop_id"],
        "source": m["source"],
        "origin": m["origin"],
        "event_ts": m["event_ts"],
        "seq": m["seq"],
        "location_code": m["location_code"],
        "event_code": m["event_code"],
        "batch_id": m["batch_id"],
        "payload": json.loads(m["payload_json"]),
    }


def _divergence_matrix(members: list, fields: list) -> list:
    """逐分歧字段列出各方取值 —— 冲突双方可比的证明面。"""
    return [
        {
            "field": f,
            "values": [
                {"hop_id": m["hop_id"], "source": m["source"], "value": m.get(f)}
                for m in members
            ],
        }
        for f in fields
    ]
