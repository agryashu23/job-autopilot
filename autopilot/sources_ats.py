"""Phase-2 sources: Remotive API + Greenhouse/Lever public ATS boards.

All of these are official, intentionally-public JSON endpoints — no login,
no key, no scraping. Unknown ATS company tokens just 404 and are skipped;
extend the lists in config.yaml (ats_companies) freely.
"""
import re

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Accept": "application/json"}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


# ---------------------------------------------------------------- remotive
def fetch_remotive(cfg):
    """https://remotive.com/api — remote-only board with a job_type field."""
    jobs = []
    seen = set()
    for term in ["node.js", "full stack", "backend", "react", "python"]:
        try:
            r = requests.get("https://remotive.com/api/remote-jobs",
                             params={"search": term, "limit": 50},
                             headers=HDRS, timeout=30)
            r.raise_for_status()
            data = r.json().get("jobs", [])
        except Exception as e:
            print(f"  remotive '{term}': ERROR {e}")
            continue
        for j in data:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            jobs.append({
                "source": "remotive",
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "") or "Remote",
                "url": j.get("url", ""),
                "description": _strip_html(j.get("description", ""))[:8000] +
                               " salary: " + (j.get("salary") or ""),
                "job_type": j.get("job_type", ""),   # 'contract', 'freelance', 'full_time'...
                "is_easy_apply": False,
            })
    print(f"  remotive: {len(jobs)} rows")
    return jobs


# ---------------------------------------------------------------- greenhouse
def _greenhouse(token):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "true"}, headers=HDRS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "source": "greenhouse",
            "title": j.get("title", ""),
            "company": token,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "description": _strip_html(j.get("content", ""))[:8000],
            "is_easy_apply": False,
        })
    return out


# ---------------------------------------------------------------- lever
def _lever(token):
    r = requests.get(f"https://api.lever.co/v0/postings/{token}",
                     params={"mode": "json"}, headers=HDRS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        out.append({
            "source": "lever",
            "title": j.get("text", ""),
            "company": token,
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "description": (j.get("descriptionPlain") or "")[:8000] +
                           " commitment: " + (cats.get("commitment") or ""),
            "job_type": cats.get("commitment", ""),
            "is_easy_apply": False,
        })
    return out


def fetch_ats(cfg):
    jobs = []
    registry = cfg.get("ats_companies", {}) or {}
    for token in registry.get("greenhouse", []) or []:
        try:
            res = _greenhouse(token)
            if res is None:
                print(f"  greenhouse '{token}': no board (404), remove from config")
            else:
                jobs.extend(res)
        except Exception as e:
            print(f"  greenhouse '{token}': ERROR {e}")
    for token in registry.get("lever", []) or []:
        try:
            res = _lever(token)
            if res is None:
                print(f"  lever '{token}': no board (404), remove from config")
            else:
                jobs.extend(res)
        except Exception as e:
            print(f"  lever '{token}': ERROR {e}")
    print(f"  ats boards: {len(jobs)} rows total")
    return jobs
