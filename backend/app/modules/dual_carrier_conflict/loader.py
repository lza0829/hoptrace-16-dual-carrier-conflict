"""双源装载编排：识别 → 裁决（失败闭）→ 正式线写入 → 同源报告。

顺序（单事务内，由调用方管理）：
1. 校验 + 运单兜底；
2. 批内冲突组整组隔离（正式线保持冲突前态）；
3. 其余候选点：撞既有冲突组轴 → 并入隔离；撞正式线分歧 → 成组隔离、
   正式线不动；否则按既有规则 INSERT OR IGNORE 进正式线；
4. 报告三数（冲突组数 / 隔离点数 / 正式写入数）全部来自冲突表与 hops 表
   的批次作用域查询 —— 与落库同源。
"""
import json
import uuid
from datetime import datetime, timezone

from app.modules.dual_carrier_conflict import adjudicator as adj
from app.modules.dual_carrier_conflict.detector import detect_conflict_groups

REQUIRED_FIELDS = ("id", "waybill_id", "event_ts", "location_code", "event_code")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_dual_source_batch(conn, payload: dict, now: str | None = None) -> dict:
    now = now or utcnow()
    batch_id = payload.get("batch_id") or f"B-{uuid.uuid4().hex[:8]}"
    window = int(payload.get("conflict_window_seconds") or 0)
    hops = payload.get("hops", [])

    valid, rejected = [], []
    for h in hops:
        if all(h.get(f) not in (None, "") for f in REQUIRED_FIELDS):
            valid.append(h)
        else:
            rejected.append({"hop": h.get("id"), "reason": "缺少必填字段"})

    for h in valid:
        wid = h["waybill_id"]
        if not conn.execute("SELECT 1 FROM waybills WHERE id=?", (wid,)).fetchone():
            conn.execute(
                "INSERT INTO waybills(id, title, status_proj, created_at) VALUES (?,?,?,?)",
                (wid, h.get("waybill_title") or wid, "未知", now),
            )

    # 1) 批内冲突组：整组隔离，双方 payload 进冲突表，正式线不动
    quarantined_ids: set = set()
    touched_groups: set = set()
    for group in detect_conflict_groups(valid, window):
        gid = adj.record_conflict_group(
            conn, group, [(m, "incoming") for m in group.members], batch_id, now, window
        )
        touched_groups.add(gid)
        quarantined_ids.update(m["id"] for m in group.members)

    # 2) 候选点：并入既有冲突组 / 对撞正式线成组 / 正常写正式线
    deduped = 0
    affected_waybills: set = set()
    for h in valid:
        if h["id"] in quarantined_ids:
            continue
        matched = adj.find_axis_matched_groups(conn, h, window)
        if matched:
            for g in matched:
                touched_groups.add(adj.join_group(conn, g, h, batch_id, now))
            quarantined_ids.add(h["id"])
            continue
        hits = adj.find_official_divergence(conn, h, window)
        if hits:
            group = adj.group_against_official(h, hits)
            members = [(x["row"], "official") for x in hits] + [(h, "incoming")]
            gid = adj.record_conflict_group(conn, group, members, batch_id, now, window)
            touched_groups.add(gid)
            quarantined_ids.add(h["id"])
            continue
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO hops
              (id, waybill_id, event_ts, location_code, event_code, seq, channel, source, batch_id)
            VALUES (?,?,?,?,?,?, 'primary', ?, ?)
            """,
            (
                h["id"], h["waybill_id"], h["event_ts"], h["location_code"],
                h["event_code"], h.get("seq"), h.get("source") or "unknown", batch_id,
            ),
        )
        if cur.rowcount:
            affected_waybills.add(h["waybill_id"])
        else:
            deduped += 1

    report = _build_report(conn, batch_id, payload, window, len(hops), deduped,
                           rejected, sorted(touched_groups))
    report["affected_waybills"] = sorted(affected_waybills)
    return report


def _build_report(conn, batch_id: str, payload: dict, window: int,
                  n_input: int, deduped: int, rejected: list,
                  touched_groups: list) -> dict:
    """三数与冲突表、时间线同源：全部按 batch_id 作用域现查。"""
    groups = conn.execute(
        "SELECT * FROM conflict_groups WHERE batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()
    quarantined = conn.execute(
        "SELECT COUNT(DISTINCT hop_id) AS c FROM conflict_members"
        " WHERE batch_id=? AND origin='incoming'",
        (batch_id,),
    ).fetchone()["c"]
    written = conn.execute(
        "SELECT COUNT(*) AS c FROM hops WHERE batch_id=? AND channel='primary'",
        (batch_id,),
    ).fetchone()["c"]
    quarantine_rows = conn.execute(
        "SELECT COUNT(*) AS c FROM quarantine WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"]
    return {
        "batch_id": batch_id,
        "cutoff_ts": payload.get("cutoff_ts"),
        "conflict_window_seconds": window,
        "input": n_input,
        "written": written,
        "conflict_groups": len(groups),
        "quarantined": quarantined,
        "quarantine_rows": quarantine_rows,
        "deduped": deduped,
        "rejected": len(rejected),
        "rejected_detail": rejected,
        "groups": [_group_brief(conn, dict(g)) for g in groups],
        "groups_touched": touched_groups,
    }


def _group_brief(conn, g: dict) -> dict:
    members = conn.execute(
        "SELECT hop_id, source, origin, location_code, event_code, event_ts, seq"
        " FROM conflict_members WHERE group_id=? ORDER BY id",
        (g["id"],),
    ).fetchall()
    return {
        "group_id": g["id"],
        "waybill_id": g["waybill_id"],
        "axis_type": g["axis_type"],
        "axis_value": g["axis_value"],
        "divergent_fields": json.loads(g["divergent_fields"]),
        "members": [dict(m) for m in members],
    }
