"""dual_carrier_conflict 端到端 + 单元测试。

夹具 B-DUAL-01 预期：
- 冲突组1（WB-1001 同序轴 seq=4）：H-A1 vs H-B1 地点+事件分歧 → 整组隔离；
- 冲突组2（WB-1003 同 ts 窗轴 15:00）：H-A4 vs H-B4 地点分歧 → 整组隔离；
- 对照（WB-1002 seq=2）：H-A2 == H-B2 双源一致 → 写 1 条 + 去重 1 条；
- 单边点 H-B3 / H-A3 正常入正式线。
"""
import json

from fastapi.testclient import TestClient

from app.db import FIXTURES_DIR, connect
from app.main import app
from app.modules.dual_carrier_conflict import detect_conflict_groups
from app.services import ingest as ingest_svc


def _rows(sql, args=()):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_fixture_batch_conflicts_and_control(fresh_db):
    result = ingest_svc.run_batch()
    assert result["ok"], result
    report = result["report"]

    # 报告三数：冲突组 2、隔离点 4、正式写入 3（双源一致去重 1）
    assert report["conflict_groups"] == 2
    assert report["quarantined"] == 4
    assert report["written"] == 3
    assert report["deduped"] == 1
    assert report["input"] == 8

    # 报告与冲突表同源
    groups = _rows("SELECT * FROM conflict_groups ORDER BY id")
    assert len(groups) == 2
    members = _rows("SELECT * FROM conflict_members")
    assert len(members) == 4
    assert {m["hop_id"] for m in members} == {"H-A1", "H-B1", "H-A4", "H-B4"}
    assert {m["source"] for m in members} == {"carrier_alpha", "carrier_beta"}
    # 双方 payload 全留
    for m in members:
        payload = json.loads(m["payload_json"])
        assert payload["location_code"] and payload["event_code"]

    # 冲突任一方都未进正式线；正式线保持冲突前态
    wb1 = _rows(
        "SELECT * FROM hops WHERE waybill_id='WB-1001' AND channel='primary' ORDER BY event_ts"
    )
    assert [h["id"] for h in wb1] == ["H-1", "H-2", "H-3"]
    assert wb1[-1]["event_code"] == "ARRIVED_HUB"
    wb1_status = _rows("SELECT status_proj FROM waybills WHERE id='WB-1001'")[0]
    assert wb1_status["status_proj"] == "到站"

    # 无冲突对照：双源一致只写一条；单边点正常写入
    wb2 = _rows("SELECT * FROM hops WHERE waybill_id='WB-1002' AND channel='primary'")
    assert {h["id"] for h in wb2} == {"H-4", "H-A2", "H-B3"}
    wb3 = _rows("SELECT * FROM hops WHERE waybill_id='WB-1003' AND channel='primary'")
    assert [h["id"] for h in wb3] == ["H-A3"]

    # 隔离表登记：两组各一行，member_json 含双方
    quarantine = _rows("SELECT * FROM quarantine WHERE reason='dual_carrier_conflict'")
    assert len(quarantine) == 2
    for q in quarantine:
        member_ids = {m["hop_id"] for m in json.loads(q["member_json"])}
        assert len(member_ids) == 2
        assert q["group_ref"]


def test_replay_idempotent_no_payload_loss(fresh_db):
    r1 = ingest_svc.run_batch()["report"]
    r2 = ingest_svc.run_batch()["report"]
    # 三数同源：重放后不变
    assert (r1["conflict_groups"], r1["quarantined"], r1["written"]) == (2, 4, 3)
    assert (r2["conflict_groups"], r2["quarantined"], r2["written"]) == (2, 4, 3)
    # 隔离刷新不丢任一方载荷：成员仍 4 条、隔离行仍 2 条
    assert len(_rows("SELECT * FROM conflict_members")) == 4
    assert len(_rows("SELECT * FROM quarantine")) == 2
    assert len(_rows("SELECT * FROM hops WHERE channel='primary'")) == 4 + 3  # 种子4 + 新写3


def test_conflict_against_official_line_keeps_prestate(fresh_db):
    """入批点与正式线同序分歧 → 入批点隔离留证，正式线保持冲突前态。"""
    batch = {
        "batch_id": "B-VS-OFFICIAL",
        "hops": [
            {
                "id": "H-X1",
                "waybill_id": "WB-1001",
                "source": "carrier_beta",
                "event_ts": "2026-11-17T18:05:00Z",
                "location_code": "HZ-HUB",
                "event_code": "EXCEPTION",
                "seq": 3,
            }
        ],
    }
    path = FIXTURES_DIR / "batch_vs_official.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    report = ingest_svc.run_batch(path)["report"]
    assert report["conflict_groups"] == 1
    assert report["quarantined"] == 1
    assert report["written"] == 0

    # 正式线不动：H-3 仍是 ARRIVED_HUB
    wb1 = _rows("SELECT * FROM hops WHERE waybill_id='WB-1001' AND channel='primary'")
    assert [h["id"] for h in wb1] == ["H-1", "H-2", "H-3"]
    # 冲突组挂双方：正式点 origin=official 留证 + 入批点 origin=incoming
    members = _rows("SELECT * FROM conflict_members ORDER BY id")
    assert [(m["hop_id"], m["origin"]) for m in members] == [("H-3", "official"), ("H-X1", "incoming")]


def test_new_point_joins_existing_group(fresh_db):
    """后续批次落在既有冲突组的轴上 → 失败闭并入该组，不进正式线。"""
    ingest_svc.run_batch()
    batch = {
        "batch_id": "B-JOIN",
        "hops": [
            {
                "id": "H-C1",
                "waybill_id": "WB-1001",
                "source": "carrier_gamma",
                "event_ts": "2026-11-17T20:00:00Z",
                "location_code": "HZ-STATION",
                "event_code": "DELIVERED",
                "seq": 4,
            }
        ],
    }
    path = FIXTURES_DIR / "batch_join.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    report = ingest_svc.run_batch(path)["report"]
    assert report["quarantined"] == 1
    assert report["written"] == 0

    gid = "CG-WB-1001-seq-4"
    members = _rows("SELECT * FROM conflict_members WHERE group_id=?", (gid,))
    assert {m["hop_id"] for m in members} == {"H-A1", "H-B1", "H-C1"}
    # 正式线仍无 seq=4 的点
    assert not _rows("SELECT * FROM hops WHERE waybill_id='WB-1001' AND seq=4")
    # 隔离行 member_json 刷新后三方载荷俱在
    q = _rows("SELECT member_json FROM quarantine WHERE group_ref=?", (gid,))[0]
    assert {m["hop_id"] for m in json.loads(q["member_json"])} == {"H-A1", "H-B1", "H-C1"}


def test_detector_unit():
    hop = dict(waybill_id="W", event_ts="2026-01-01T10:00:00Z",
               location_code="A", event_code="E1", seq=1)
    # 完全一致 → 非冲突（去重问题）
    identical = [dict(hop, id="1", source="s1"), dict(hop, id="2", source="s2")]
    assert detect_conflict_groups(identical) == []
    # 同源分歧也算冲突
    divergent = [dict(hop, id="1", source="s1"), dict(hop, id="2", source="s1", location_code="B")]
    groups = detect_conflict_groups(divergent)
    assert len(groups) == 2  # 同序 + 同 ts 两轴各成一组
    assert {g.axis_type for g in groups} == {"seq", "event_ts"}
    # ts 窗口聚簇：窗内分歧成组，轴值取最早 ts
    windowed = [
        dict(hop, id="1", source="s1", seq=None),
        dict(hop, id="2", source="s2", seq=None,
             event_ts="2026-01-01T10:04:00Z", location_code="B"),
    ]
    assert detect_conflict_groups(windowed, window_seconds=0) == []
    groups = detect_conflict_groups(windowed, window_seconds=300)
    assert len(groups) == 1
    assert groups[0].axis_value == "2026-01-01T10:00:00Z"
    # seq 缺失的点不参与同序轴
    no_seq = [dict(hop, id="1", source="s1", seq=None),
              dict(hop, id="2", source="s2", seq=None, location_code="B")]
    assert {g.axis_type for g in detect_conflict_groups(no_seq)} == {"event_ts"}


def test_api_surface(fresh_db):
    with TestClient(app) as client:
        assert client.post("/api/ingest/run").json()["ok"]

        groups = client.get("/api/conflicts").json()
        assert len(groups) == 2
        by_id = {g["id"]: g for g in groups}
        g1 = by_id["CG-WB-1001-seq-4"]
        assert g1["axis_type"] == "seq"
        assert g1["sources"] == ["carrier_alpha", "carrier_beta"]
        assert {m["hop_id"] for m in g1["members"]} == {"H-A1", "H-B1"}

        proof = client.get("/api/conflicts/CG-WB-1001-seq-4").json()
        assert proof["quarantine"]["reason"] == "dual_carrier_conflict"
        fields = {d["field"] for d in proof["divergence"]}
        assert fields == {"location_code", "event_code"}
        for d in proof["divergence"]:
            assert len({v["value"] for v in d["values"]}) == 2  # 双方取值可比

        assert client.get("/api/conflicts/CG-NOPE").status_code == 404

        timeline = client.get("/api/waybills/WB-1001/timeline").json()
        assert [h["id"] for h in timeline["hops"]] == ["H-1", "H-2", "H-3"]
        assert [c["id"] for c in timeline["conflicts"]] == ["CG-WB-1001-seq-4"]

        overview = client.get("/api/overview").json()
        assert overview["quarantine"] == 2
