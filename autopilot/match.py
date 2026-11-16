"""AI matching: local embeddings (nomic-embed-text via Ollama) rank every
job against the user's profile; a qwen2.5 verify pass reads the top
candidates and scores real fit with a reason. 100% local, zero tokens.

    python run.py match      (also runs automatically after discover)
"""
import json
import math
import re
from pathlib import Path

import requests

from . import db, features

ROOT = Path(__file__).resolve().parent.parent
OLLAMA = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = "qwen2.5:3b"

VERIFY_MIN_SEMANTIC = 0.50   # only LLM-verify jobs at least this similar
VERIFY_BATCH = 30            # max LLM verifications per run (keeps runs quick)
FIT_QUEUE = 6                # fit >= this -> queued
FIT_DEMOTE = 3               # fit <= this -> demoted out of the queue
QUEUE_SCORE = 55             # match_score >= this -> promoted into the queue
DEMOTE_SCORE = 40            # match_score < this -> pushed out of the queue


def _ollama_ok():
    try:
        return requests.get(f"{OLLAMA}/api/tags", timeout=3).ok
    except Exception:
        return False


def _embed(texts, kind="document"):
    """nomic-embed-text expects a task prefix; using the right one measurably
    improves retrieval quality ('search_query' for the thing you're matching
    FROM, 'search_document' for the corpus you're matching AGAINST)."""
    prefix = "search_query: " if kind == "query" else "search_document: "
    r = requests.post(f"{OLLAMA}/api/embed",
                      json={"model": EMBED_MODEL,
                            "input": [prefix + t for t in texts]}, timeout=120)
    r.raise_for_status()
    return r.json()["embeddings"]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- field vectors
# Each profile facet is embedded separately, then compared against the matching
# job facet. Blending several focused similarities beats one blurry whole-profile
# similarity, which is what made earlier results feel random.
FIELD_WEIGHTS = {
    ("role", "title"): 0.30,     # my job title vs their job title
    ("skills", "body"): 0.30,    # my tech stack vs their requirements
    ("experience", "body"): 0.15,  # what I actually did vs the role
    ("role", "body"): 0.10,      # catches titles hidden in the description
}
SKILL_OVERLAP_WEIGHT = 0.15      # exact skill-token overlap (not embeddings)


def _profile_fields(p: dict) -> dict:
    skills = [s for s in p.get("skills", []) if isinstance(s, str)]
    exp_bits = []
    for e in p.get("experience", []):
        if isinstance(e, dict):
            exp_bits.append(f"{e.get('title','')} at {e.get('company','')}. "
                            f"{(e.get('description') or '')[:400]}")
    prefs = p.get("preferences", {}) or {}
    role = (f"{p.get('title') or 'Software Engineer'}. "
            f"Seeking {', '.join(prefs.get('job_types', []))} "
            f"{', '.join(prefs.get('remote_types', []))} work.")
    return {
        "role": role,
        "skills": "Technical skills: " + ", ".join(skills),
        "experience": " ".join(exp_bits)[:2000] or p.get("summary", ""),
    }


def _job_fields(title: str, description: str) -> dict:
    return {"title": title or "", "body": (description or "")[:2000]}


def _numeric_adjust(profile: dict, job_row) -> tuple[float, list]:
    """Numeric facts are compared ARITHMETICALLY, never as vectors.
    Returns (delta in score points, notes)."""
    delta, notes = 0.0, []
    text = f"{job_row['title']} {job_row['description'] or ''}"

    # years of experience required vs mine
    my_years = profile.get("years_experience")
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", text, re.I)
    if m and my_years:
        try:
            need = float(m.group(1))
            gap = need - float(my_years)
            if gap >= 4:
                delta -= 22; notes.append(f"wants {need:.0f}y, you have {my_years}")
            elif gap >= 2:
                delta -= 10; notes.append(f"wants {need:.0f}y")
            elif gap <= 0:
                delta += 4
        except ValueError:
            pass

    # pay floor vs advertised rate
    prefs = profile.get("preferences", {}) or {}
    floor = prefs.get("salary_min")
    rate_txt = job_row["rate"] if "rate" in job_row.keys() else ""
    if floor and rate_txt:
        nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", rate_txt)]
        if nums:
            top = max(nums)
            if top >= float(floor):
                delta += 8; notes.append(f"pays {rate_txt}")
            else:
                delta -= 12; notes.append(f"{rate_txt} below your floor")
    elif rate_txt:
        delta += 5; notes.append(f"pays {rate_txt}")
    return delta, notes


def _profile():
    p = ROOT / "profile.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _profile_text(p):
    skills = ", ".join(s if isinstance(s, str) else str(s) for s in p.get("skills", []))
    exp = "; ".join(e.get("title", "") + " at " + e.get("company", "")
                    for e in p.get("experience", []) if isinstance(e, dict))
    return (f"{p.get('title','')}. {p.get('summary','')} "
            f"Skills: {skills}. Experience: {exp}. "
            f"{p.get('years_experience') or ''} years experience. "
            f"Looking for: {', '.join(p.get('preferences',{}).get('job_types', []))} "
            f"{', '.join(p.get('preferences',{}).get('remote_types', []))} work.")


# The LLM ONLY extracts what the job requires (easy for a 3B model).
# Fit is then computed deterministically in Python — no LLM judgment.
EXTRACT_PROMPT = """Read this job posting and extract what it requires.

JOB POSTING:
Title: {title}
Description: {desc}

Respond ONLY with JSON:
{{"primary": "<the ONE main language or framework this job centers on>",
  "stack": ["<all technologies the posting asks for>"],
  "min_years": <required years of experience as a number, or null>}}"""

# normalization aliases: LLM/job wording -> canonical
_ALIASES = {
    "nodejs": "node.js", "node": "node.js", "node js": "node.js",
    "reactjs": "react", "react.js": "react", "react js": "react",
    "ts": "typescript", "js": "javascript", "es6": "javascript",
    "expressjs": "express", "express.js": "express",
    "postgres": "postgresql", "mongo": "mongodb",
    "golang": "go", "vuejs": "vue", "vue.js": "vue",
    "next.js": "nextjs", "amazon web services": "aws",
    "restful apis": "rest", "rest apis": "rest", "rest api": "rest",
    "websockets": "websocket", "ci/cd": "cicd",
}
# skills the candidate implicitly has, given an explicit one
_IMPLIES = {
    "node.js": ["javascript"],
    "typescript": ["javascript"],
    "react": ["javascript", "html", "css"],
    "express": ["node.js", "rest"],
    "mongodb": ["nosql"],
}


def _norm(s):
    s = re.sub(r"[^a-z0-9./+# ]", "", str(s).lower()).strip()
    return _ALIASES.get(s, s)


def _candidate_skills(profile):
    skills = set()
    for s in profile.get("skills", []):
        if not isinstance(s, str):
            continue
        # a profile entry like "Backend Node.js" carries multiple tokens
        for part in re.split(r"[,/|]| and ", s):
            n = _norm(part)
            if n:
                skills.add(n)
    text = _norm(profile.get("title", "") + " " + profile.get("summary", ""))
    for known in list(_ALIASES.values()) + ["python", "flutter", "docker", "redis",
                                            "rabbitmq", "couchdb", "aws", "lambda"]:
        if known in text:
            skills.add(known)
    for skill in list(skills):
        for implied in _IMPLIES.get(skill, []):
            skills.add(implied)
    return skills


def _has(cand, req):
    """Candidate has a required skill if any candidate token contains it or vice versa."""
    return any(req in c or c in req for c in cand if len(req) > 1)


def _compute_fit(cand, extracted, cand_years):
    stack = [_norm(s) for s in (extracted.get("stack") or [])][:12]
    stack = [s for s in stack if s]
    primary = _norm(extracted.get("primary") or (stack[0] if stack else ""))
    matched = [s for s in stack if _has(cand, s)]
    missing = [s for s in stack if not _has(cand, s)]
    coverage = len(matched) / len(stack) if stack else 0.0

    fit = (6 if primary and _has(cand, primary) else 1) + round(4 * coverage)
    min_years = extracted.get("min_years")
    try:
        if min_years and cand_years and float(min_years) > float(cand_years) + 1:
            fit -= 2
    except (TypeError, ValueError):
        pass
    fit = max(0, min(10, fit))

    if not stack:
        reason = "posting lists no clear tech stack"
    else:
        reason = f"has {', '.join(matched[:5]) or 'none of the stack'}"
        if missing:
            reason += f"; missing {', '.join(missing[:4])}"
    return fit, reason


def semantic_pass(con, profile_vec):
    rows = con.execute(
        "SELECT id, title, description FROM jobs "
        "WHERE semantic IS NULL AND status IN ('new','queued')"
    ).fetchall()
    if not rows:
        return 0
    done = 0
    for i in range(0, len(rows), 16):
        batch = rows[i:i + 16]
        texts = [(r["title"] or "") + "\n" + (r["description"] or "")[:1500] for r in batch]
        try:
            vecs = _embed(texts)
        except Exception as e:
            print(f"  embed batch failed: {e}")
            continue
        for r, v in zip(batch, vecs):
            sim = _cosine(profile_vec, v)
            con.execute("UPDATE jobs SET semantic=? WHERE id=?", (round(sim, 4), r["id"]))
            done += 1
        con.commit()
    return done


def field_match_pass(con, profile):
    """Weighted feature scoring: embedding similarities per profile facet plus
    must-have coverage, experience curve, pay and seniority -> 0-100 score."""
    from .util import has_skill_match, load_config, passes_filters
    cfg = load_config()
    weights = (cfg.get("scoring", {}) or {}).get("weights", {}) or {}
    pf = _profile_fields(profile)
    keys = list(pf.keys())
    pvecs = dict(zip(keys, _embed([pf[k] for k in keys], kind="query")))
    cand_skills = features.profile_techs(profile)
    print(f"  your canonical stack ({len(cand_skills)}): "
          f"{', '.join(sorted(cand_skills)[:18])}")

    rows = con.execute(
        "SELECT * FROM jobs WHERE match_score IS NULL AND status IN ('new','queued')"
    ).fetchall()
    if not rows:
        return 0, 0, 0
    scored = promoted = demoted = 0

    for i in range(0, len(rows), 12):
        batch = rows[i:i + 12]
        jf = [_job_fields(r["title"], r["description"]) for r in batch]
        try:
            tvecs = _embed([f["title"] for f in jf])
            bvecs = _embed([f["body"] for f in jf])
        except Exception as e:
            print(f"  embed batch failed: {e}")
            continue

        for r, tv, bv in zip(batch, tvecs, bvecs):
            def rescale(c):   # cosine sits ~0.35-0.80 on this model
                return max(0.0, min(1.0, (c - 0.35) / 0.45))
            sims = {
                "role_title": rescale(_cosine(pvecs["role"], tv)),
                "skills_body": rescale(_cosine(pvecs["skills"], bv)),
                "experience_body": rescale(_cosine(pvecs["experience"], bv)),
            }
            job = {"title": r["title"], "description": r["description"] or "",
                   "rate": r["rate"] if "rate" in r.keys() else "",
                   "salary_min": (profile.get("preferences") or {}).get("salary_min")}
            score, exp = features.score(cand_skills, profile.get("years_experience"),
                                        job, sims, weights)
            reason = features.reason_text(exp, job["rate"])

            con.execute(
                "UPDATE jobs SET match_score=?, fit=?, fit_reason=?, must_have=?, missing=? "
                "WHERE id=?",
                (round(score, 1), round(score / 10, 1), reason,
                 ", ".join(exp["must"][:12]), ", ".join(exp["missing"][:12]), r["id"]))
            scored += 1
            # Promotion must still respect the hard filters: a high embedding
            # similarity on a recruiting or VBScript role is noise, not fit.
            eligible = (passes_filters(cfg, r["title"])
                        and has_skill_match(cfg, r["title"], r["description"] or ""))
            if score >= QUEUE_SCORE and r["status"] == "new" and eligible:
                con.execute("UPDATE jobs SET status='queued' WHERE id=?", (r["id"],))
                promoted += 1
            elif score < DEMOTE_SCORE and r["status"] == "queued":
                con.execute("UPDATE jobs SET status='new' WHERE id=?", (r["id"],))
                demoted += 1
        con.commit()
    return scored, promoted, demoted


def verify_pass(con, profile):
    cand = _candidate_skills(profile)
    cand_years = profile.get("years_experience")
    rows = con.execute(
        "SELECT id, title, company, description, status FROM jobs "
        "WHERE fit IS NULL AND semantic >= ? AND status IN ('new','queued') "
        "ORDER BY semantic DESC LIMIT ?",
        (VERIFY_MIN_SEMANTIC, VERIFY_BATCH),
    ).fetchall()
    verified = promoted = demoted = 0
    for r in rows:
        prompt = EXTRACT_PROMPT.format(title=r["title"],
                                       desc=(r["description"] or "")[:3000])
        try:
            resp = requests.post(f"{OLLAMA}/api/generate",
                                 json={"model": LLM_MODEL, "prompt": prompt, "stream": False,
                                       "format": "json",
                                       "options": {"temperature": 0.0}},
                                 timeout=180)
            resp.raise_for_status()
            extracted = json.loads(resp.json()["response"])
            fit, reason = _compute_fit(cand, extracted, cand_years)
        except Exception as e:
            print(f"  verify failed for {r['title'][:40]}: {e}")
            continue
        con.execute("UPDATE jobs SET fit=?, fit_reason=? WHERE id=?", (fit, reason, r["id"]))
        verified += 1
        if fit >= FIT_QUEUE and r["status"] == "new":
            con.execute("UPDATE jobs SET status='queued' WHERE id=?", (r["id"],))
            promoted += 1
        elif fit <= FIT_DEMOTE and r["status"] == "queued":
            con.execute("UPDATE jobs SET status='new' WHERE id=?", (r["id"],))
            demoted += 1
        con.commit()
        print(f"  fit {fit}/10  {r['title'][:50]} — {reason[:70]}")
    return verified, promoted, demoted


def run_match():
    if not _ollama_ok():
        print("Ollama not running — skipping AI match (start Ollama and run: python run.py match)")
        return
    profile = _profile()
    if not profile:
        print("No profile.json yet — upload your resume in the web app first.")
        return
    con = db.connect()
    ptext = _profile_text(profile)
    try:
        pvec = _embed([ptext])[0]
    except Exception as e:
        print(f"Embedding model not ready ({e}). Run: ollama pull {EMBED_MODEL}")
        return
    n = semantic_pass(con, pvec)
    print(f"Semantic pass: {n} jobs embedded (overall similarity).")
    s, p, d = field_match_pass(con, profile)
    print(f"Field match: {s} jobs scored, {p} promoted to queue, {d} demoted.")
    top = con.execute(
        "SELECT match_score, title, fit_reason FROM jobs "
        "WHERE status='queued' ORDER BY match_score DESC LIMIT 8").fetchall()
    if top:
        print("\nTop of your queue now:")
        for r in top:
            print(f"  {r['match_score']:.0f}  {r['title'][:48]} — {(r['fit_reason'] or '')[:60]}")
