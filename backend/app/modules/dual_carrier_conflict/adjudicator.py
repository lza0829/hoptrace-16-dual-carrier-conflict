"""裁决（失败闭·路径A）：发现冲突 → 整组立即隔离，正式线保持冲突前态。

- 冲突组整组进 conflict_groups / conflict_members（双方源与载荷全留）；
- 既有 quarantine 表登记隔离行（group_ref 关联冲突组），member_json 每次
  从冲突成员表全量重建 —— 隔离刷新只增不减，绝不丢任一方载荷；
- 全部 INSERT OR IGNORE：批次重放幂等，不重复、不覆盖、不删除。
"""
import json

from app.modules.dual_carrier_conflict.detector import (
    AXIS_SEQ,
    PAYLOAD_FIELDS,
    ConflictGroup,
    divergent_fields,
    make_group_id,
    ts_within,
)

REASON = "dual_carrier_conflict"


def record_conflict_group(conn, group: ConflictGroup, members_with_origin: list,
                          batch_id: str, now: str, window_seconds: int = 0) -> str:
    """整组进隔离：冲突表挂双方 payload + quarantine 登记。返回组 id。"""
    gid = group.group_id
    conn.execute(
        """
        INSERT OR IGNORE INTO conflict_groups
          (id, waybill_id, batch_id, axis_type, axis_value, window_seconds, divergent_fields, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (gid, group.waybill_id, batch_id, group.axis_type, group.axis_value,
         window_seconds, json.dumps(group.divergent_fields, ensure_ascii=False), now),
    )
    add_members(conn, gid, members_with_origin, batch_id)
    _refresh_group_fields(conn, gid)
    _refresh_quarantine_row(conn, gid, group.waybill_id, batch_id, now)
    return gid


def add_members(conn, group_id: str, members_with_origin: list, batch_id: str) -> None:
    """成员只增不改：UNIQUE(group_id, hop_id) 保证重放不重复、载荷不丢。"""
    for hop, origin in members_with_origin:
        conn.execute(
            """
            INSERT OR IGNORE INTO conflict_members
              (group_id, hop_id, source, origin, event_ts, seq, location_code, event_code, batch_id, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                group_id,
                hop["id"],
                hop.get("source") or "unknown",
                origin,
                hop["event_ts"],
                hop.get("seq"),
                hop["location_code"],
                hop["event_code"],
                batch_id,
                json.dumps(_payload_of(hop), ensure_ascii=False, sort_keys=True),
            ),
        )


def join_group(conn, group_row, hop: dict, batch_id: str, now: str) -> str:
    """候选点并入既有冲突组：加成员、重算分歧字段、刷新隔离行（载荷只增不减）。"""
    gid = group_row["id"]
    add_members(conn, gid, [(hop, "incoming")], batch_id)
    _refresh_group_fields(conn, gid)
    _refresh_quarantine_row(conn, gid, group_row["waybill_id"], batch_id, now)
    return gid


def find_axis_matched_groups(conn, hop: dict, window_seconds: int = 0) -> list:
    """候选点落在既有冲突组的轴上（同运单 + 同序值 / 同 ts 值）→ 失败闭并入该组。"""
    rows = conn.execute(
        "SELECT * FROM conflict_groups WHERE waybill_id=?", (hop["waybill_id"],)
    ).fetchall()
    matched = []
    for g in rows:
        if g["axis_type"] == AXIS_SEQ:
            if hop.get("seq") is not None and str(hop["seq"]) == g["axis_value"]:
                matched.append(g)
        elif ts_within(str(hop["event_ts"]), g["axis_value"],
                       max(window_seconds, g["window_seconds"])):
            matched.append(g)
    return matched


def find_official_divergence(conn, hop: dict, window_seconds: int = 0) -> list:
    """候选点 vs 正式线：同运单同轴（同序或同 ts 窗）且非键字段分歧的正式点。

    完全一致的正式点不算分歧（由 INSERT OR IGNORE 幂等吸收）。
    """
    rows = conn.execute(
        "SELECT * FROM hops WHERE waybill_id=? AND channel='primary' AND retracted=0",
        (hop["waybill_id"],),
    ).fetchall()
    hits = []
    for r in rows:
        same_seq = hop.get("seq") is not None and r["seq"] == hop["seq"]
        same_ts = ts_within(str(r["event_ts"]), str(hop["event_ts"]), window_seconds)
        if not (same_seq or same_ts):
            continue
        div = [f for f in PAYLOAD_FIELDS if r[f] != hop.get(f)]
        if div:
            hits.append({"row": dict(r), "divergent_fields": div, "same_seq": same_seq})
    return hits


def group_against_official(hop: dict, hits: list) -> ConflictGroup:
    """候选点与正式线对撞成组：正式点 origin='official' 留证，正式线本身不动。"""
    same_seq = any(h["same_seq"] for h in hits)
    if same_seq:
        axis_type, axis_value = AXIS_SEQ, str(hop["seq"])
    else:
        axis_type, axis_value = "event_ts", str(hop["event_ts"])
    members = [h["row"] for h in hits] + [hop]
    div = sorted({f for h in hits for f in h["divergent_fields"]})
    return ConflictGroup(hop["waybill_id"], axis_type, axis_value, members, div)


def _refresh_group_fields(conn, group_id: str) -> None:
    """新成员并入后重算分歧字段（只可能变多）。"""
    members = conn.execute(
        "SELECT location_code, event_code FROM conflict_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    div = divergent_fields([dict(m) for m in members])
    conn.execute(
        "UPDATE conflict_groups SET divergent_fields=? WHERE id=?",
        (json.dumps(div, ensure_ascii=False), group_id),
    )


def _refresh_quarantine_row(conn, group_id: str, waybill_id: str, batch_id: str, now: str) -> None:
    """隔离行 upsert：member_json 从冲突成员表全量重建 —— 刷新不丢任一方载荷。"""
    members = conn.execute(
        "SELECT * FROM conflict_members WHERE group_id=? ORDER BY id", (group_id,)
    ).fetchall()
    member_json = json.dumps([_member_view(dict(m)) for m in members], ensure_ascii=False)
    conn.execute(
        "INSERT OR IGNORE INTO quarantine(waybill_id, reason, member_json, batch_id, group_ref, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (waybill_id, REASON, member_json, batch_id, group_id, now),
    )
    conn.execute(
        "UPDATE quarantine SET member_json=? WHERE group_ref=?", (member_json, group_id)
    )


def _payload_of(hop: dict) -> dict:
    return {
        "id": hop["id"],
        "waybill_id": hop["waybill_id"],
        "source": hop.get("source") or "unknown",
        "event_ts": hop["event_ts"],
        "seq": hop.get("seq"),
        "location_code": hop["location_code"],
        "event_code": hop["event_code"],
    }


def _member_view(m: dict) -> dict:
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


def group_id_for(waybill_id: str, axis_type: str, axis_value: str) -> str:
    return make_group_id(waybill_id, axis_type, axis_value)
