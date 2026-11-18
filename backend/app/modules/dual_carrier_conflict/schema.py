"""冲突落库形态（择一）：独立冲突表。

conflict_groups  — 冲突组（同运单 + 同序/同 event_ts 窗口 + 非键字段分歧）
conflict_members — 双方（或多方）成员，挂源与完整 payload；只增不改，重放不丢。

另对基座表做列迁移：hops.source / hops.batch_id（正式线带来源与批次），
quarantine.batch_id / quarantine.group_ref（隔离行关联冲突组，幂等去重）。
"""

DDL = """
CREATE TABLE IF NOT EXISTS conflict_groups (
  id TEXT PRIMARY KEY,
  waybill_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  axis_type TEXT NOT NULL,
  axis_value TEXT NOT NULL,
  window_seconds INTEGER NOT NULL DEFAULT 0,
  divergent_fields TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL REFERENCES conflict_groups(id),
  hop_id TEXT NOT NULL,
  source TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'incoming',
  event_ts TEXT NOT NULL,
  seq INTEGER,
  location_code TEXT NOT NULL,
  event_code TEXT NOT NULL,
  batch_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(group_id, hop_id)
);
"""

COLUMN_MIGRATIONS = [
    ("hops", "source", "ALTER TABLE hops ADD COLUMN source TEXT NOT NULL DEFAULT 'seed'"),
    ("hops", "batch_id", "ALTER TABLE hops ADD COLUMN batch_id TEXT"),
    ("quarantine", "batch_id", "ALTER TABLE quarantine ADD COLUMN batch_id TEXT"),
    ("quarantine", "group_ref", "ALTER TABLE quarantine ADD COLUMN group_ref TEXT"),
]

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_quarantine_group_ref "
    "ON quarantine(group_ref) WHERE group_ref IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_conflict_groups_waybill ON conflict_groups(waybill_id)",
    "CREATE INDEX IF NOT EXISTS ix_conflict_members_group ON conflict_members(group_id)",
    "CREATE INDEX IF NOT EXISTS ix_conflict_members_batch ON conflict_members(batch_id)",
    "CREATE INDEX IF NOT EXISTS ix_hops_batch ON hops(batch_id)",
]


def ensure_schema(conn) -> None:
    """建冲突表 + 基座列迁移。幂等，可重复调用。"""
    conn.executescript(DDL)
    existing_tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, column, ddl in COLUMN_MIGRATIONS:
        if table not in existing_tables:
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)
    for ddl in INDEXES:
        conn.execute(ddl)
