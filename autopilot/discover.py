"""Job discovery: LinkedIn (jobspy public search), Naukri (via the debug
Chrome session), RemoteOK, WeWorkRemotely, Remotive and Greenhouse/Lever
boards. Only Naukri needs a login. Every run is recorded in the `runs`
table with its full log, and stale jobs are expired at the end."""
import contextlib
import io
import json
import random
import re
import sys
import time
import traceback
from pathlib import Path

import feedparser
import requests

from . import db
from .util import (external_apply_url, extract_hourly_rate, has_skill_match,
                   is_contract_listing, is_excluded_company, is_remote_listing,
                   load_config, passes_filters, rate_below_floor, score_job)

ROOT = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class _Tee(io.TextIOBase):
    """Write to the real stdout and keep a copy for the run log."""

    def __init__(self, real):
        self.real = real
        self.buf = io.StringIO()

    def write(self, s):
        try:
            self.real.write(s)
        except UnicodeEncodeError:
            # console without UTF-8: degrade the display, never the run
            enc = getattr(self.real, "encoding", None) or "ascii"
            self.real.write(s.encode(enc, errors="replace").decode(enc))
        self.buf.write(s)
        return len(s)

    def flush(self):
        self.real.flush()


def _cell(row, key) -> str:
    """pandas cell -> str; NaN/None -> ''."""
    v = row.get(key)
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v)


# ---------------------------------------------------------------- linkedin
def fetch_linkedin(cfg):
    from jobspy import scrape_jobs  # heavy import; keep local
    jobs = []
    easy_only = bool(cfg["sources"].get("linkedin_easy_apply_only"))
    for term in cfg["search"]["terms"]:
        kwargs = dict(
            site_name=["linkedin"],
            search_term=term,
            location="India",
            is_remote=cfg["search"]["must_be_remote"],
            hours_old=cfg["search"]["max_age_days"] * 24,
            results_wanted=40,
            linkedin_fetch_description=True,   # also yields job_url_direct
        )
        if easy_only:
            kwargs["easy_apply"] = True  # LinkedIn guest search f_AL filter
        try:
            df = scrape_jobs(**kwargs)
        except TypeError:
            kwargs.pop("easy_apply", None)
            easy_only = False
            df = scrape_jobs(**kwargs)
        except Exception as e:
            print(f"  linkedin '{term}': ERROR {e}")
            continue
        for _, r in df.iterrows():
            # The f_AL filter leaks aggregator posts whose Apply button leaves
            # LinkedIn. jobspy exposes that destination; use it to label the
            # post correctly NOW instead of discovering it at apply time.
            ext = external_apply_url("linkedin", _cell(r, "job_url_direct"))
            jobs.append({
                "source": "linkedin",
                "title": _cell(r, "title"),
                "company": _cell(r, "company"),
                "location": _cell(r, "location"),
                "url": _cell(r, "job_url"),
                "description": _cell(r, "description"),
                "job_type": _cell(r, "job_type"),
                "is_easy_apply": easy_only and not ext,
                "apply_url": ext,
                "note": f"external application → {ext[:200]}" if ext else "",
            })
        print(f"  linkedin '{term}': {len(df)} rows")
    return jobs


# ---------------------------------------------------------------- naukri
def fetch_naukri(cfg):
    """Naukri blocks direct API calls (Akamai), so we drive the real
    debug-Chrome session to the search page and capture the JSON response
    the site itself fetches. Requires `python run.py chrome` to be running."""
    from .browser import attach
    jobs = []
    try:
        pw, browser, context = attach(cfg)
    except RuntimeError as e:
        print(f"  naukri: SKIPPED — {str(e).splitlines()[0]}")
        return jobs

    page = context.new_page()
    try:
        for term in cfg["search"]["terms"]:
            seo = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
            url = (f"https://www.naukri.com/{seo}-jobs"
                   f"?wfhType=2&jobAge={cfg['search']['max_age_days']}")
            try:
                with page.expect_response(
                    lambda r: "jobapi/v3/search" in r.url and r.status == 200,
                    timeout=30000,
                ) as resp_info:
                    page.goto(url, timeout=45000)
                details = resp_info.value.json().get("jobDetails", [])
            except Exception as e:
                print(f"  naukri '{term}': ERROR {type(e).__name__} {e}")
                continue
            for j in details:
                placeholders = {p.get("type"): p.get("label")
                                for p in j.get("placeholders", [])}
                jobs.append({
                    "source": "naukri",
                    "title": j.get("title", ""),
                    "company": j.get("companyName", ""),
                    "location": placeholders.get("location", ""),
                    "url": "https://www.naukri.com" + j.get("jdURL", ""),
                    "description": (j.get("jobDescription", "") or "") + " " +
                                   " ".join((j.get("tagsAndSkills", "") or "").split(",")),
                    "is_easy_apply": True,
                })
            print(f"  naukri '{term}': {len(details)} rows")
            time.sleep(random.uniform(3, 7))
    finally:
        page.close()
        pw.stop()
    return jobs


# ---------------------------------------------------------------- remoteok
def fetch_remoteok(cfg):
    jobs = []
    try:
        resp = requests.get("https://remoteok.com/api",
                            headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
        data = resp.json()[1:]  # first element is the legal notice
    except Exception as e:
        print(f"  remoteok: ERROR {e}")
        return jobs
    wanted = re.compile(r"node|react|javascript|typescript|python|backend|full.?stack", re.I)
    for j in data:
        blob = f"{j.get('position','')} {' '.join(j.get('tags', []))}"
        if not wanted.search(blob):
            continue
        jobs.append({
            "source": "remoteok",
            "title": j.get("position", ""),
            "company": j.get("company", ""),
            "location": j.get("location", "") or "Remote",
            "url": j.get("url", ""),
            "description": j.get("description", ""),
            "is_easy_apply": False,
        })
    print(f"  remoteok: {len(jobs)} relevant rows")
    return jobs


# ---------------------------------------------------------------- wwr
WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
]


def fetch_weworkremotely(cfg):
    jobs = []
    for feed_url in WWR_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  wwr: ERROR {e}")
            continue
        for e in feed.entries:
            title = e.get("title", "")          # "Company: Job Title"
            company, _, role = title.partition(":")
            jobs.append({
                "source": "weworkremotely",
                "title": role.strip() or title,
                "company": company.strip(),
                "location": "Remote",
                "url": e.get("link", ""),
                "description": re.sub(r"<[^>]+>", " ", e.get("summary", "")),
                "is_easy_apply": False,
            })
    print(f"  weworkremotely: {len(jobs)} rows")
    return jobs


# ---------------------------------------------------------------- main
def _apply_profile_prefs(cfg):
    """profile.json preferences (from the web app) override config filters."""
    p = ROOT / "profile.json"
    if not p.exists():
        return cfg
    try:
        prefs = json.loads(p.read_text(encoding="utf-8")).get("preferences", {}) or {}
    except Exception:
        return cfg
    if prefs.get("job_types"):
        # contract_only unless the user explicitly opted into full-time
        cfg["search"]["contract_only"] = "full-time" not in prefs["job_types"]
    if prefs.get("remote_types"):
        cfg["search"]["strict_remote"] = prefs["remote_types"] == ["remote"]
    if (prefs.get("salary_min") and (prefs.get("salary_currency") or "USD") == "USD"
            and (prefs.get("salary_period") or "hour") == "hour"):
        cfg["search"]["rate_floor_usd_hr"] = prefs["salary_min"]
    for kw in prefs.get("keywords") or []:
        cfg["scoring"]["keywords"].setdefault(kw.lower(), 3)
    print(f"[prefs] contract_only={cfg['search']['contract_only']} "
          f"strict_remote={cfg['search']['strict_remote']} "
          f"rate_floor=${cfg['search'].get('rate_floor_usd_hr') or 0}/hr "
          f"(+{len(prefs.get('keywords') or [])} keywords from profile)")
    return cfg


def _discover(con, cfg) -> dict:
    from .sources_ats import fetch_remotive, fetch_ats
    sources = cfg["sources"]
    fetchers = {
        "linkedin": fetch_linkedin,
        "naukri": fetch_naukri,
        "remoteok": fetch_remoteok,
        "weworkremotely": fetch_weworkremotely,
        "remotive": fetch_remotive,
        "ats": fetch_ats,
    }
    strict_remote = bool(cfg["search"].get("strict_remote"))
    contract_only = bool(cfg["search"].get("contract_only"))
    floor = cfg["search"].get("rate_floor_usd_hr")
    c = dict(new=0, queued=0, external=0, below_floor=0,
             dropped_remote=0, dropped_contract=0, dropped_company=0)

    for name, fn in fetchers.items():
        if not sources.get(name):
            continue
        print(f"[{name}] fetching...")
        for job in fn(cfg):
            if not passes_filters(cfg, job["title"]):
                continue
            if is_excluded_company(cfg, job.get("company", "")):
                c["dropped_company"] += 1
                continue
            if strict_remote and not is_remote_listing(
                    job["source"], job["title"], job.get("location", ""),
                    job.get("description", "")):
                c["dropped_remote"] += 1
                continue
            if contract_only and not is_contract_listing(
                    job["title"], job.get("description", ""), job.get("job_type", "")):
                c["dropped_contract"] += 1
                continue
            job["score"] = score_job(cfg, job["title"], job["description"])
            job["rate"] = extract_hourly_rate(job["title"] + " " + job["description"])
            queue_ok = (job["score"] >= cfg["scoring"]["min_score_to_queue"]
                        and has_skill_match(cfg, job["title"], job["description"]))
            below = queue_ok and rate_below_floor(job["rate"], floor)
            if below:
                queue_ok = False
                msg = f"rate {job['rate']} is below your ${floor}/hr floor"
                job["note"] = f"{job['note']} | {msg}" if job.get("note") else msg
            job["status"] = "queued" if queue_ok else "new"
            if db.upsert_job(con, job):
                c["new"] += 1
                c["queued"] += job["status"] == "queued"
                c["external"] += bool(job.get("apply_url"))
                c["below_floor"] += below
        con.commit()
    return c


def _refilter(con, cfg) -> int:
    """Apply the CURRENT title/company blocklists to jobs already waiting for
    review, so editing config.yaml cleans the queue without a reset."""
    n = 0
    rows = con.execute(
        "SELECT id, title, company FROM jobs WHERE status IN ('queued','new')").fetchall()
    for r in rows:
        if not passes_filters(cfg, r["title"]):
            why = "title matches exclude_title_keywords"
        elif is_excluded_company(cfg, r["company"]):
            why = "company is on exclude_companies"
        else:
            continue
        con.execute("UPDATE jobs SET status='skipped', "
                    "note=CASE WHEN note='' THEN ? ELSE note || ' | ' || ? END WHERE id=?",
                    (why, why, r["id"]))
        n += 1
    con.commit()
    return n


def run_discovery():
    cfg = _apply_profile_prefs(load_config())
    con = db.connect()
    run_id = db.start_run(con, "discover")
    tee = _Tee(sys.stdout)
    ok, summary = False, ""
    with contextlib.redirect_stdout(tee):
        try:
            c = _discover(con, cfg)
            life = cfg.get("lifecycle", {}) or {}
            exp = db.expire_stale(con, life.get("approved_ttl_days", 14),
                                  life.get("queue_ttl_days", 21))
            print(f"\nDiscovery done: {c['new']} new jobs stored, {c['queued']} queued for "
                  f"review, {c['external']} flagged external-apply.")
            print(f"Hard-filtered out: {c['dropped_remote']} not-remote, "
                  f"{c['dropped_contract']} not-contract, {c['dropped_company']} aggregator "
                  f"reposts; {c['below_floor']} parked below your rate floor.")
            if exp["approved"] or exp["queued"]:
                print(f"Expired: {exp['approved']} stale approvals, "
                      f"{exp['queued']} stale un-reviewed jobs.")
            refiltered = _refilter(con, cfg)
            if refiltered:
                print(f"Re-filtered: {refiltered} waiting jobs now fail the blocklists → skipped.")
            summary = (f"{c['new']} new · {c['queued']} queued · {c['external']} external · "
                       f"{exp['approved'] + exp['queued']} expired · {refiltered} re-filtered")
            ok = True
            try:
                from .match import run_match
                print("\n[AI match]")
                run_match()
            except Exception as e:
                print(f"AI match skipped: {e}")
            print("Next: python run.py web  →  http://localhost:5580")
        except Exception:
            tb = traceback.format_exc()
            print(tb)
            summary = "failed: " + tb.strip().splitlines()[-1][:200]
        finally:
            db.finish_run(con, run_id, ok, summary, tee.buf.getvalue())
