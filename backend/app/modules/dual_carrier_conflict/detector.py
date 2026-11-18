"""冲突识别：同运单 + 同序（或同 event_ts 窗口）且非键字段（地点/事件）分歧。

键（轴）字段：waybill_id + seq、waybill_id + event_ts 窗口。
非键（载荷）字段：location_code、event_code —— 组内取值不一致即分歧。
完全一致的点不算冲突（去重问题，由正式线唯一约束幂等吸收）。
"""
import re
from dataclasses import dataclass
from datetime import datetime

AXIS_SEQ = "seq"
AXIS_TS = "event_ts"
PAYLOAD_FIELDS = ("location_code", "event_code")


@dataclass
class ConflictGroup:
    waybill_id: str
    axis_type: str          # 'seq' | 'event_ts'
    axis_value: str         # 序值 或 窗口内最早 event_ts
    members: list           # hop dict 列表（≥2，按 source/id 排序）
    divergent_fields: list  # 分歧的非键字段

    @property
    def group_id(self) -> str:
        return make_group_id(self.waybill_id, self.axis_type, self.axis_value)


def make_group_id(waybill_id: str, axis_type: str, axis_value: str) -> str:
    """确定性组 id：同自然键（运单+轴+轴值）永远同组，跨批次可并入。"""
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", str(axis_value))
    return f"CG-{waybill_id}-{axis_type}-{slug}"


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def ts_within(a: str, b: str, window_seconds: int) -> bool:
    if a == b:
        return True
    if window_seconds <= 0:
        return False
    try:
        return abs((parse_ts(a) - parse_ts(b)).total_seconds()) <= window_seconds
    except ValueError:
        return False


def divergent_fields(members: list) -> list:
    return [f for f in PAYLOAD_FIELDS if len({m.get(f) for m in members}) > 1]


def _sorted(members: list) -> list:
    return sorted(members, key=lambda m: (str(m.get("source") or ""), str(m.get("id"))))


def detect_conflict_groups(hops: list, window_seconds: int = 0) -> list:
    """批内冲突识别：同序轴 + 同 event_ts 窗口轴，返回确定性排序的冲突组。"""
    groups: list[ConflictGroup] = []

    by_seq: dict = {}
    for h in hops:
        if h.get("seq") is None:
            continue
        by_seq.setdefault((h["waybill_id"], h["seq"]), []).append(h)
    for (wid, seq), members in sorted(by_seq.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(members) < 2:
            continue
        div = divergent_fields(members)
        if div:
            groups.append(ConflictGroup(wid, AXIS_SEQ, str(seq), _sorted(members), div))

    for wid, axis_value, members in _iter_ts_clusters(hops, window_seconds):
        div = divergent_fields(members)
        if div:
            groups.append(ConflictGroup(wid, AXIS_TS, axis_value, _sorted(members), div))

    return groups


def _iter_ts_clusters(hops: list, window_seconds: int):
    """同运单按 event_ts 排序后贪心聚簇（相邻间隔 ≤ 窗口），簇内 ≥2 点即候选组。

    轴值取簇内最早 event_ts —— 同输入必同簇同轴值，保证组 id 幂等。
    """
    by_waybill: dict = {}
    for h in hops:
        by_waybill.setdefault(h["waybill_id"], []).append(h)
    for wid in sorted(by_waybill):
        pts = sorted(by_waybill[wid], key=lambda h: (str(h["event_ts"]), str(h["id"])))
        cluster: list = []
        last = None
        for h in pts:
            if last is not None and not ts_within(str(h["event_ts"]), last, window_seconds):
                yield from _emit(wid, cluster)
                cluster = []
            cluster.append(h)
            last = str(h["event_ts"])
        yield from _emit(wid, cluster)


def _emit(wid: str, cluster: list):
    if len(cluster) >= 2:
        yield wid, str(cluster[0]["event_ts"]), cluster
