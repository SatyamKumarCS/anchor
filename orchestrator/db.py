import sqlite3
import os
import json
from datetime import datetime, timezone

DB_PATH = os.environ.get("ORCHESTRATOR_DB", "state.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'IDLE',
            active_color TEXT NOT NULL DEFAULT 'blue',
            config TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deployment_id INTEGER NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (deployment_id) REFERENCES deployments(id)
        );
    """)
    conn.commit()
    conn.close()


def create_deployment(version: str, config: dict) -> int:
    """Create a new deployment record. Returns deployment ID."""
    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO deployments (version, state, active_color, config, started_at) VALUES (?, ?, ?, ?, ?)",
        (version, "IDLE", "blue", json.dumps(config), datetime.now(timezone.utc).isoformat()),
    )
    deployment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return deployment_id


def update_deployment_state(deployment_id: int, state: str, active_color: str | None = None):
    """Update FSM state for a deployment."""
    conn = _get_conn()
    if active_color:
        conn.execute(
            "UPDATE deployments SET state = ?, active_color = ? WHERE id = ?",
            (state, active_color, deployment_id),
        )
    else:
        conn.execute(
            "UPDATE deployments SET state = ? WHERE id = ?",
            (state, deployment_id),
        )
    conn.commit()
    conn.close()


def finish_deployment(deployment_id: int, final_state: str, active_color: str):
    """Mark a deployment as finished."""
    conn = _get_conn()
    conn.execute(
        "UPDATE deployments SET state = ?, active_color = ?, finished_at = ? WHERE id = ?",
        (final_state, active_color, datetime.now(timezone.utc).isoformat(), deployment_id),
    )
    conn.commit()
    conn.close()


def log_event(deployment_id: int, from_state: str, to_state: str, reason: str = ""):
    """Log a state transition event."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO events (deployment_id, from_state, to_state, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
        (deployment_id, from_state, to_state, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_last_deployment() -> dict | None:
    """Get the most recent deployment."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM deployments ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_last_events(limit: int = 5) -> list[dict]:
    """Get the most recent events."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_deployment_history() -> list[dict]:
    """Get all deployments."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM deployments ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_events_for_deployment(deployment_id: int) -> list[dict]:
    """Get all events for a specific deployment."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE deployment_id = ? ORDER BY id ASC", (deployment_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
