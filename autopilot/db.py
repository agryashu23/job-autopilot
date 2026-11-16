"""SQLite store for discovered jobs, application state and run history."""
import hashlib
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"

STATUSES = ("new", "queued", "approved", "applied", "skipped", "failed",
            "needs_attention", "expired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,          -- sha1(source|company|title)
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    url         TEXT,
    description TEXT,
    is_easy_apply INTEGER DEFAULT 0,
    score       REAL DEFAULT 0,
    status      TEXT DEFAULT 'new',
    note        TEXT DEFAULT '',
    cover_note  TEXT DEFAULT '',
    discovered_at INTEGER,
    applied_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_status ON jobs(status);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- discover | match
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    ok          INTEGER,
    summary     TEXT DEFAULT '',
    log         TEXT DEFAULT ''
);
"""

MIGRATIONS = (
    "ALTER TABLE jobs ADD COLUMN rate TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN semantic REAL",
    "ALTER TABLE jobs ADD COLUMN fit REAL",
    "ALTER TABLE jobs ADD COLUMN fit_reason TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN match_score REAL",
    "ALTER TABLE jobs ADD COLUMN must_have TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN missing TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN apply_url TEXT DEFAULT ''",   # off-site application link
    "ALTER TABLE jobs ADD COLUMN decided_at INTEGER",          # when approved/skipped
)


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for mig in MIGRATIONS:
        try:
            con.execute(mig)
            con.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    return con


def job_id(source: str, company: str, title: str) -> str:
    raw = f"{source}|{(company or '').lower().strip()}|{(title or '').lower().strip()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def upsert_job(con, job: dict) -> bool:
    """Insert if new. Returns True if inserted, False if already known."""
    jid = job_id(job["source"], job.get("company", ""), job["title"])
    if con.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
        return False
    con.execute(
        """INSERT INTO jobs (id, source, title, company, location, url, description,
                             is_easy_apply, score, status, rate, apply_url, note, discovered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (jid, job["source"], job["title"], job.get("company", ""), job.get("location", ""),
         job.get("url", ""), (job.get("description") or "")[:20000],
         1 if job.get("is_easy_apply") else 0, job.get("score", 0),
         job.get("status", "new"), job.get("rate", ""), job.get("apply_url", ""),
         job.get("note", ""), int(time.time())),
    )
    return True


def set_status(con, jid: str, status: str, note: str = None):
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    now = int(time.time())
    if note is not None:
        con.execute("UPDATE jobs SET status=?, note=? WHERE id=?", (status, note, jid))
    else:
        con.execute("UPDATE jobs SET status=? WHERE id=?", (status, jid))
    if status in ("approved", "skipped"):
        con.execute("UPDATE jobs SET decided_at=? WHERE id=?", (now, jid))
    if status == "applied":
        con.execute("UPDATE jobs SET applied_at=? WHERE id=?", (now, jid))
    con.commit()


def applied_today(con, source: str) -> int:
    day_start = int(time.time()) // 86400 * 86400
    cur = con.execute(
        "SELECT COUNT(*) c FROM jobs WHERE source=? AND status='applied' AND applied_at>=?",
        (source, day_start))
    return cur.fetchone()["c"]


def status_counts(con) -> dict:
    return {r["status"]: r["c"] for r in
            con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")}


# ---------------------------------------------------------------- lifecycle
_EXPIRE_SQL = ("UPDATE jobs SET status='expired', "
               "note=CASE WHEN note='' THEN ? ELSE note || ' | ' || ? END ")


def expire_stale(con, approved_ttl_days: int, queue_ttl_days: int, now: int = None) -> dict:
    """Approved-but-never-applied jobs older than approved_ttl_days, and
    un-reviewed jobs older than queue_ttl_days, move to 'expired'. Postings
    that old are almost always closed; keeping them only hides fresh ones."""
    now = now or int(time.time())
    out = {"approved": 0, "queued": 0}
    if approved_ttl_days:
        msg = f"expired: approved more than {approved_ttl_days} days ago, never applied"
        out["approved"] = con.execute(
            _EXPIRE_SQL + "WHERE status='approved' AND COALESCE(decided_at, discovered_at) < ?",
            (msg, msg, now - approved_ttl_days * 86400)).rowcount
    if queue_ttl_days:
        msg = f"expired: not reviewed within {queue_ttl_days} days"
        out["queued"] = con.execute(
            _EXPIRE_SQL + "WHERE status IN ('queued','new') AND discovered_at < ?",
            (msg, msg, now - queue_ttl_days * 86400)).rowcount
    con.commit()
    return out


# ---------------------------------------------------------------- run history
def start_run(con, kind: str) -> int:
    cur = con.execute("INSERT INTO runs (kind, started_at) VALUES (?, ?)",
                      (kind, int(time.time())))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, ok: bool, summary: str, log: str = ""):
    con.execute("UPDATE runs SET finished_at=?, ok=?, summary=?, log=? WHERE id=?",
                (int(time.time()), 1 if ok else 0, summary[:2000], log[-60000:], run_id))
    con.commit()


def recent_runs(con, limit: int = 10) -> list:
    rows = con.execute(
        "SELECT id, kind, started_at, finished_at, ok, summary FROM runs "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def run_log(con, run_id: int) -> str:
    row = con.execute("SELECT log FROM runs WHERE id=?", (run_id,)).fetchone()
    return row["log"] if row else ""
