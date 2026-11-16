"""Job Autopilot web app — FastAPI backend serving the React frontend.

    python run.py web        (http://localhost:5580)

Single-user, local-only: profile lives in profile.json, jobs in jobs.db.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopilot import db                                            # noqa: E402
from autopilot.util import cover_note, load_answers, load_config    # noqa: E402
from server import ollama_client, resume_parse, validate_links      # noqa: E402

PROFILE_PATH = ROOT / "profile.json"
WEB_DIST = ROOT / "web" / "dist"
LOGS = ROOT / "logs"
DISCOVER_LOG = LOGS / "discover-latest.log"

app = FastAPI(title="Job Autopilot")

# The Chrome extension (chrome-extension:// origin) and the Vite dev server
# need cross-origin access. Nothing else does: with "*" any website open in
# the browser could read your phone, email and answer bank from this server.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://[a-z]+|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ status
@app.get("/api/status")
def status():
    return {
        "ollama_running": ollama_client.available(),
        "model_ready": ollama_client.model_ready(),
        "model": ollama_client.MODEL,
        "profile_exists": PROFILE_PATH.exists(),
    }


# ------------------------------------------------------------------ profile
def _sync_total_years(profile: dict) -> dict:
    """Total years must not be lower than any single-skill answer."""
    per_skill = []
    for a in load_answers():
        if a.get("type") != "number":
            continue
        if any(p in m.lower() for m in a["match"]
               for p in ("total years", "total experience", "overall experience",
                         "years of work experience")):
            continue
        try:
            per_skill.append(float(a["value"]))
        except (TypeError, ValueError):
            pass
    try:
        current = float(profile.get("years_experience") or 0)
    except (TypeError, ValueError):
        current = 0.0
    if per_skill and current < max(per_skill):
        highest = max(per_skill)
        profile["years_experience"] = int(highest) if highest.is_integer() else highest
    return profile


@app.get("/api/profile")
def get_profile():
    if PROFILE_PATH.exists():
        return _sync_total_years(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
    return resume_parse.empty_profile()


@app.put("/api/profile")
def save_profile(profile: dict):
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    return {"saved": True}


_refine_jobs = {}   # refine_id -> {"status": "running"|"done", "profile": dict}


@app.post("/api/links/validate")
def links_validate(body: dict):
    """{github, linkedin, website} -> per-field {status, url, message}."""
    out = {}
    if "github" in body:
        out["github"] = validate_links.check_github(body.get("github") or "")
    if "linkedin" in body:
        out["linkedin"] = validate_links.check_linkedin(body.get("linkedin") or "")
    if body.get("website"):
        out["website"] = validate_links.check_url(body["website"])
    return out


@app.post("/api/resume/parse")
async def parse_resume(file: UploadFile = File(...)):
    """Returns the instant heuristic parse; the slow local-LLM refinement
    runs in a background thread — poll /api/resume/refine/{id}."""
    import threading
    import uuid

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF resumes only for now")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        profile, text = resume_parse.parse_resume_fast(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if PROFILE_PATH.exists():
        old = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        if old.get("preferences"):
            profile["preferences"] = old["preferences"]

    if ollama_client.available() and ollama_client.model_ready():
        rid = uuid.uuid4().hex[:12]
        _refine_jobs[rid] = {"status": "running", "profile": None}

        def _do_refine(base, txt, job_id):
            refined = resume_parse.llm_refine(base, txt)
            _refine_jobs[job_id] = {"status": "done", "profile": refined}

        threading.Thread(target=_do_refine, args=(dict(profile), text, rid),
                         daemon=True).start()
        profile["refine_id"] = rid
    return profile


@app.get("/api/resume/refine/{rid}")
def refine_status(rid: str):
    job = _refine_jobs.get(rid)
    if not job:
        raise HTTPException(404, "unknown refine id")
    return job


# ------------------------------------------------------------------ discovery runs
_discover_proc = {"proc": None, "log": None}


def _running() -> bool:
    p = _discover_proc["proc"]
    return bool(p and p.poll() is None)


@app.post("/api/jobs/discover")
def trigger_discover():
    if _running():
        return {"started": False, "running": True}
    LOGS.mkdir(exist_ok=True)
    if _discover_proc["log"]:
        _discover_proc["log"].close()
    logf = open(DISCOVER_LOG, "w", encoding="utf-8")
    _discover_proc["log"] = logf
    _discover_proc["proc"] = subprocess.Popen(
        [sys.executable, str(ROOT / "run.py"), "discover"], cwd=ROOT,
        stdout=logf, stderr=subprocess.STDOUT)
    return {"started": True, "running": True}


@app.get("/api/jobs/discover")
def discover_status():
    return {"running": _running()}


def _tail(path: Path, n: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


@app.get("/api/runs")
def runs():
    """Recent discovery runs. While a run is in progress the live log file is
    shown; otherwise the last run's stored log."""
    con = db.connect()
    recent = db.recent_runs(con, 10)
    running = _running()
    if running:
        latest_log = _tail(DISCOVER_LOG)
    elif recent:
        latest_log = db.run_log(con, recent[0]["id"])
        if not latest_log:            # run crashed before it could log itself
            latest_log = _tail(DISCOVER_LOG)
    else:
        latest_log = _tail(DISCOVER_LOG)
    return {"running": running, "runs": recent, "latest_log": latest_log}


@app.get("/api/runs/{run_id}/log")
def run_log(run_id: int):
    con = db.connect()
    return {"id": run_id, "log": db.run_log(con, run_id)}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
def list_jobs(status: str = "queued", limit: int = 200):
    con = db.connect()
    rows = con.execute(
        "SELECT * FROM jobs WHERE status=? "
        "ORDER BY (match_score IS NULL), match_score DESC, "
        "is_easy_apply DESC, (rate != '') DESC, score DESC, discovered_at DESC "
        "LIMIT ?", (status, limit),
    ).fetchall()
    return {"jobs": [dict(r) for r in rows], "counts": db.status_counts(con)}


@app.post("/api/jobs/{jid}/decide")
def decide(jid: str, body: dict):
    decision = body.get("decision")
    if decision not in ("approved", "skipped", "applied"):
        raise HTTPException(400, "decision must be approved|skipped|applied")
    con = db.connect()
    row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    if decision == "approved":
        cfg = load_config()
        con.execute("UPDATE jobs SET cover_note=? WHERE id=?",
                    (cover_note(cfg, row["title"], row["company"]), jid))
    db.set_status(con, jid, decision)
    return {"ok": True}


@app.get("/api/jobs/{jid}/apply-kit")
def apply_kit(jid: str):
    """Everything the user needs to apply manually: link, cover note, answers."""
    con = db.connect()
    row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    cfg = load_config()
    answers = load_answers()
    return {
        "url": row["apply_url"] or row["url"],
        "listing_url": row["url"],
        "note": row["note"],
        "cover_note": row["cover_note"] or cover_note(cfg, row["title"], row["company"]),
        "answers": [{"questions": a["match"], "value": a["value"]} for a in answers],
        "resume_path": cfg["profile"]["resume_path"],
    }


# ------------------------------------------------------------------ marketplaces
# Talent marketplaces for contract/freelance work. They don't post scrapeable
# listings — you sign up ONCE (vetting test / AI interview) and they match you
# to hourly contracts on an ongoing basis. Roughly in order of fit.
MARKETPLACES = [
    ("Mercor", "https://mercor.com", "AI-matched contract work (US AI-lab data/eng gigs, $20-60/hr). Upload resume, one AI interview."),
    ("Braintrust", "https://www.usebraintrust.com", "0% fee talent network; US clients, hourly contracts."),
    ("Turing", "https://www.turing.com", "Long-term remote contracts with US companies; skill tests once."),
    ("Arc.dev", "https://arc.dev", "Vetted freelance developers; remote contract + hourly."),
    ("Lemon.io", "https://lemon.io", "Startups hiring vetted devs, hourly, fast matching."),
    ("Toptal", "https://www.toptal.com", "Premium rates; hard screening (~3% pass). Attempt after a few wins elsewhere."),
    ("Gun.io", "https://gun.io", "Freelance dev network, US clients."),
    ("A.Team", "https://a.team", "Team-based product gigs, senior-leaning."),
    ("Contra", "https://contra.com", "Commission-free freelance profile + gigs."),
    ("Upwork", "https://www.upwork.com", "Volume marketplace; filter Hourly + Expert. Your CouchDB/offline-sync niche searches well here."),
    ("Wellfound", "https://wellfound.com/jobs", "Startup jobs incl. contract; set profile to open-to-contract."),
    ("Crossover", "https://www.crossover.com", "Full-time-ish remote contracts, standardized testing."),
]


@app.get("/api/marketplaces")
def marketplaces():
    return {"marketplaces": [{"name": n, "url": u, "blurb": b} for n, u, b in MARKETPLACES]}


# ------------------------------------------------------------------ extension
@app.get("/api/autofill-data")
def autofill_data():
    """Everything the Chrome extension needs to fill application forms."""
    profile = get_profile()
    answers = load_answers()
    name = profile.get("name", "") or ""
    return {
        "profile": {
            "name": name,
            "first_name": name.split(" ")[0],
            "last_name": " ".join(name.split(" ")[1:]),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "location": profile.get("location", ""),
            "github": profile.get("github", ""),
            "linkedin": profile.get("linkedin", ""),
            "summary": profile.get("summary", ""),
            "years_experience": profile.get("years_experience"),
            "skills": profile.get("skills", []),
        },
        "answers": [{"match": a["match"], "type": a["type"], "value": a["value"]}
                    for a in answers],
        "resume_path": load_config()["profile"]["resume_path"],
    }


# ------------------------------------------------------------------ frontend
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = WEB_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
