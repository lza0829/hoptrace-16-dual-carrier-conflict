import json
import uuid
from pathlib import Path

from app import db
from app.modules.dual_carrier_conflict import run_dual_source_batch
from app.services import timeline as timeline_svc


def run_batch(path: Path | None = None) -> dict:
    """双源装载：同运单同序/同 event_ts 窗且地点或事件分歧的点整组进隔离，
    正式线保持冲突前态；无冲突点按既有规则写入正式线并重算投影。"""
    path = path or db.FIXTURES_DIR / "ingest_batch.json"
    if not path.exists():
        return {"ok": False, "error": f"缺少 {path.name}"}
    payload = json.loads(path.read_text(encoding="utf-8"))

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        report = run_dual_source_batch(conn, payload)
        for wid in report.get("affected_waybills", []):
            timeline_svc.refresh_projection(conn, wid)
        conn.execute(
            "INSERT OR REPLACE INTO ingest_batches(id, cutoff_ts, created_at, report_json)"
            " VALUES (?,?,?,?)",
            (
                report["batch_id"] or f"B-{uuid.uuid4().hex[:8]}",
                payload.get("cutoff_ts"),
                db.utcnow(),
                json.dumps(report, ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"ok": True, "report": report}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()
