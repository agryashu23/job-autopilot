"""Unit tests for the deterministic parts of the pipeline (no network)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import db  # noqa: E402
from autopilot.util import (external_apply_url, extract_hourly_rate,  # noqa: E402
                            is_contract_listing, is_excluded_company,
                            rate_below_floor)

CFG = {"search": {"exclude_companies": ["Hired", "Crossing Hurdles", "Jobs Ai"]}}


# ---------------------------------------------------------------- contract signal
def test_part_time_counts_as_contract_signal():
    assert is_contract_listing("Part-time Node.js developer", "")
    assert is_contract_listing("Backend dev", "this is a part time engagement")
    assert is_contract_listing("Backend dev", "", job_type="contract")


def test_full_time_only_is_not_contract():
    assert not is_contract_listing("Full Stack Engineer", "Full-time permanent role with benefits")


# ---------------------------------------------------------------- external apply
def test_external_apply_detects_offsite_url():
    assert external_apply_url("linkedin", "https://boards.greenhouse.io/acme/jobs/1") \
        == "https://boards.greenhouse.io/acme/jobs/1"


def test_external_apply_unwraps_linkedin_safety_redirect():
    wrapped = ("https://www.linkedin.com/safety/go?url=https%3A%2F%2Fjobs.lever.co%2Facme%2F1"
               "&trk=flagship")
    assert external_apply_url("linkedin", wrapped) == "https://jobs.lever.co/acme/1"


def test_external_apply_ignores_easy_apply_and_other_sources():
    assert external_apply_url("linkedin", "") == ""
    assert external_apply_url("linkedin", "https://www.linkedin.com/jobs/view/123") == ""
    assert external_apply_url("remotive", "https://acme.com/apply") == ""


# ---------------------------------------------------------------- pay
def test_strip_html_handles_entity_encoded_greenhouse_content():
    from autopilot.sources_ats import _strip_html
    encoded = ("&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;GitLab is the "
               "platform&amp;nbsp;for DevSecOps &amp;amp; more.&lt;/p&gt;&lt;/div&gt;")
    assert _strip_html(encoded) == "GitLab is the platform for DevSecOps & more."
    assert _strip_html("<p>Plain <b>HTML</b></p>") == "Plain HTML"
    assert _strip_html(None) == ""


def test_rate_extraction():
    assert extract_hourly_rate("Pay: Upto $55/hr, remote") == "Upto $55/hr"
    assert extract_hourly_rate("USD 20 - 60 per hour").lower().startswith("usd 20")
    assert extract_hourly_rate("competitive salary") == ""


def test_rate_below_floor_only_for_clear_usd_rates():
    assert rate_below_floor("$20/hr", 25)
    assert not rate_below_floor("$20 - $60 per hour", 25)
    assert not rate_below_floor("₹800/hour", 25)       # INR not comparable
    assert not rate_below_floor("", 25)
    assert not rate_below_floor("$20/hr", None)


# ---------------------------------------------------------------- company blocklist
def test_excluded_company_matches_whole_or_leading_name():
    assert is_excluded_company(CFG, "Hired")
    assert is_excluded_company(CFG, "Hired Inc.")
    assert is_excluded_company(CFG, "crossing hurdles")
    assert not is_excluded_company(CFG, "Hiredly Tech")
    assert not is_excluded_company(CFG, "")


# ---------------------------------------------------------------- db lifecycle
def _mem():
    return db.connect(":memory:")


def _job(con, title, status, age_days, decided_age_days=None):
    now = int(time.time())
    db.upsert_job(con, {"source": "linkedin", "title": title, "company": "c",
                        "status": status})
    jid = db.job_id("linkedin", "c", title)
    con.execute("UPDATE jobs SET discovered_at=? WHERE id=?", (now - age_days * 86400, jid))
    if decided_age_days is not None:
        con.execute("UPDATE jobs SET decided_at=? WHERE id=?",
                    (now - decided_age_days * 86400, jid))
    con.commit()
    return jid


def test_expire_stale_moves_old_approved_and_unreviewed():
    con = _mem()
    old_appr = _job(con, "old approved", "approved", 40, decided_age_days=30)
    fresh_appr = _job(con, "fresh approved", "approved", 3, decided_age_days=2)
    legacy_appr = _job(con, "legacy approved", "approved", 60)   # no decided_at
    old_q = _job(con, "old queued", "queued", 30)
    fresh_q = _job(con, "fresh queued", "queued", 2)

    out = db.expire_stale(con, approved_ttl_days=14, queue_ttl_days=21)
    assert out == {"approved": 2, "queued": 1}
    status = {r["title"]: r["status"] for r in con.execute("SELECT title, status FROM jobs")}
    assert status["old approved"] == "expired"
    assert status["legacy approved"] == "expired"
    assert status["fresh approved"] == "approved"
    assert status["old queued"] == "expired"
    assert status["fresh queued"] == "queued"
    note = con.execute("SELECT note FROM jobs WHERE id=?", (old_appr,)).fetchone()["note"]
    assert note.startswith("expired:")


def test_set_status_records_decision_time_and_rejects_unknown():
    con = _mem()
    jid = _job(con, "x", "queued", 1)
    db.set_status(con, jid, "approved")
    assert con.execute("SELECT decided_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    try:
        db.set_status(con, jid, "bogus")
        assert False, "should have raised"
    except ValueError:
        pass


def test_run_history_roundtrip():
    con = _mem()
    rid = db.start_run(con, "discover")
    db.finish_run(con, rid, True, "12 new, 4 queued", "line1\nline2")
    runs = db.recent_runs(con)
    assert runs[0]["id"] == rid and runs[0]["ok"] == 1
    assert db.run_log(con, rid) == "line1\nline2"


def test_stale_unfinished_run_is_closed_as_interrupted():
    con = _mem()
    now = int(time.time())
    con.execute("INSERT INTO runs (kind, started_at) VALUES ('discover', ?)", (now - 5 * 3600,))
    con.commit()
    assert db.close_stale_runs(con, now=now) == 1
    run = db.recent_runs(con)[0]
    assert run["ok"] == 0 and run["finished_at"] == now
    assert run["summary"].startswith("interrupted")


def test_live_run_is_not_closed_and_new_run_cleans_up():
    con = _mem()
    live = db.start_run(con, "discover")          # just started: must stay open
    assert db.close_stale_runs(con) == 0
    assert db.recent_runs(con)[0]["finished_at"] is None
    con.execute("UPDATE runs SET started_at=? WHERE id=?", (int(time.time()) - 3 * 3600, live))
    con.commit()
    db.start_run(con, "discover")                 # next run closes the dead one
    old = [r for r in db.recent_runs(con) if r["id"] == live][0]
    assert old["finished_at"] is not None and old["ok"] == 0
