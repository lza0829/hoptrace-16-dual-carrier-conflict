"""dual_carrier_conflict — 双承运源冲突隔离。

同一运单从两个承运源来了同序（或同 event_ts 窗口）但地点/事件分歧的点：
- 落库形态（择一）：独立冲突表 conflict_groups + conflict_members，挂双方源与载荷；
- 裁决路径（择一）：失败闭路径A —— 发现冲突立即整组隔离，正式线保持冲突前态，
  冲突任一方都不得单独进正式线；无冲突点仍按既有规则写入。
"""
from app.modules.dual_carrier_conflict.detector import (
    PAYLOAD_FIELDS,
    ConflictGroup,
    detect_conflict_groups,
)
from app.modules.dual_carrier_conflict.loader import run_dual_source_batch
from app.modules.dual_carrier_conflict.proofs import (
    conflicts_for_waybill,
    get_conflict,
    list_conflicts,
)
from app.modules.dual_carrier_conflict.schema import ensure_schema

__all__ = [
    "PAYLOAD_FIELDS",
    "ConflictGroup",
    "detect_conflict_groups",
    "run_dual_source_batch",
    "list_conflicts",
    "get_conflict",
    "conflicts_for_waybill",
    "ensure_schema",
]
