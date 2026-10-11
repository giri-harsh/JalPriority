import sqlite3
import orjson
import os
from typing import Optional, List
from jp_b.config import settings
from jp_b.repositories.base import PlansRepository, LedgerRepository, EventsRepository, AuditRepository, WeightsRepository

def get_db():
    db_path = settings.b_sqlite_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS jp_b_plans (
            plan_id TEXT PRIMARY KEY,
            scenario_id TEXT,
            created_at TEXT,
            status TEXT,
            data TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_plans_scenario ON jp_b_plans (scenario_id, created_at);
        
        CREATE TABLE IF NOT EXISTS jp_b_ledger (
            pk TEXT,
            sk INTEGER,
            service_area_id TEXT,
            data TEXT,
            PRIMARY KEY (pk, sk)
        );
        
        CREATE TABLE IF NOT EXISTS jp_b_events (
            event_id TEXT PRIMARY KEY,
            scenario_id TEXT,
            ts TEXT,
            name TEXT,
            data TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_scenario_ts ON jp_b_events (scenario_id, ts);
        
        CREATE TABLE IF NOT EXISTS jp_b_audit (
            audit_id TEXT PRIMARY KEY,
            ts TEXT,
            plan_id TEXT,
            data TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON jp_b_audit (ts);
        
        CREATE TABLE IF NOT EXISTS jp_b_weights (
            service_area_id TEXT PRIMARY KEY,
            data TEXT
        );
    """)
    conn.commit()

init_db()

class SQLitePlansRepository(PlansRepository):
    def __init__(self):
        self.conn = get_db()

    def save(self, plan: dict) -> None:
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO jp_b_plans (plan_id, scenario_id, created_at, status, data) VALUES (?, ?, ?, ?, ?)",
            (plan["plan_id"], plan["scenario_id"], plan["created_at"], plan["status"], orjson.dumps(plan).decode('utf-8'))
        )
        self.conn.commit()

    def get(self, plan_id: str) -> Optional[dict]:
        c = self.conn.cursor()
        c.execute("SELECT data FROM jp_b_plans WHERE plan_id = ?", (plan_id,))
        row = c.fetchone()
        return orjson.loads(row["data"]) if row else None

    def list_by_scenario(self, scenario_id: Optional[str] = None, status: Optional[str] = None, latest: bool = False) -> List[dict]:
        c = self.conn.cursor()
        query = "SELECT data FROM jp_b_plans WHERE 1=1"
        params = []
        if scenario_id:
            query += " AND scenario_id = ?"
            params.append(scenario_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        if latest:
            query += " LIMIT 1"
            
        c.execute(query, params)
        return [orjson.loads(row["data"]) for row in c.fetchall()]

class SQLiteLedgerRepository(LedgerRepository):
    def __init__(self):
        self.conn = get_db()

    def save(self, service_area_id: str, area_id: str, window_index: int, data: dict) -> None:
        c = self.conn.cursor()
        pk = f"{service_area_id}#{area_id}"
        c.execute(
            "INSERT OR REPLACE INTO jp_b_ledger (pk, sk, service_area_id, data) VALUES (?, ?, ?, ?)",
            (pk, window_index, service_area_id, orjson.dumps(data).decode('utf-8'))
        )
        self.conn.commit()

    def get_all_for_service_area(self, service_area_id: str) -> List[dict]:
        c = self.conn.cursor()
        c.execute("SELECT data FROM jp_b_ledger WHERE service_area_id = ?", (service_area_id,))
        return [orjson.loads(row["data"]) for row in c.fetchall()]

    def replace_window(self, service_area_id: str, window_index: int, rows: List[dict]) -> None:
        c = self.conn.cursor()
        # Delete existing entries for this window and service area
        c.execute("DELETE FROM jp_b_ledger WHERE service_area_id = ? AND sk = ?", (service_area_id, window_index))
        for data in rows:
            pk = f"{service_area_id}#{data['area_id']}"
            c.execute(
                "INSERT INTO jp_b_ledger (pk, sk, service_area_id, data) VALUES (?, ?, ?, ?)",
                (pk, window_index, service_area_id, orjson.dumps(data).decode('utf-8'))
            )
        self.conn.commit()

class SQLiteEventsRepository(EventsRepository):
    def __init__(self):
        self.conn = get_db()

    def save(self, event: dict) -> None:
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO jp_b_events (event_id, scenario_id, ts, name, data) VALUES (?, ?, ?, ?, ?)",
            (event["event_id"], event.get("scenario_id"), event["ts"], event["name"], orjson.dumps(event).decode('utf-8'))
        )
        self.conn.commit()

    def list_events(self, scenario_id: Optional[str] = None, name: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        c = self.conn.cursor()
        query = "SELECT data FROM jp_b_events WHERE 1=1"
        params = []
        if scenario_id:
            query += " AND scenario_id = ?"
            params.append(scenario_id)
        if name:
            query += " AND name = ?"
            params.append(name)
        query += " ORDER BY ts DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        c.execute(query, params)
        return [orjson.loads(row["data"]) for row in c.fetchall()]

class SQLiteAuditRepository(AuditRepository):
    def __init__(self):
        self.conn = get_db()

    def save(self, entry: dict) -> None:
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO jp_b_audit (audit_id, ts, plan_id, data) VALUES (?, ?, ?, ?)",
            (entry["audit_id"], entry["ts"], entry.get("plan_id"), orjson.dumps(entry).decode('utf-8'))
        )
        self.conn.commit()

    def list_entries(self, plan_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        c = self.conn.cursor()
        query = "SELECT data FROM jp_b_audit WHERE 1=1"
        params = []
        if plan_id:
            query += " AND plan_id = ?"
            params.append(plan_id)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        
        c.execute(query, params)
        return [orjson.loads(row["data"]) for row in c.fetchall()]

class SQLiteWeightsRepository(WeightsRepository):
    def __init__(self):
        self.conn = get_db()

    def save(self, service_area_id: str, data: dict) -> None:
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO jp_b_weights (service_area_id, data) VALUES (?, ?)",
            (service_area_id, orjson.dumps(data).decode('utf-8'))
        )
        self.conn.commit()

    def get(self, service_area_id: str) -> Optional[dict]:
        c = self.conn.cursor()
        c.execute("SELECT data FROM jp_b_weights WHERE service_area_id = ?", (service_area_id,))
        row = c.fetchone()
        return orjson.loads(row["data"]) if row else None
