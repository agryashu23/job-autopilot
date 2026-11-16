"""Config loading, answer-bank matching, scoring and hard filters."""
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _personal_or_example(name: str) -> Path:
    """Personal files (config.yaml, answers.yaml) are git-ignored. A fresh
    clone runs on the committed *.example.* templates until you copy them."""
    real = ROOT / name
    if real.exists():
        return real
    stem, ext = name.rsplit(".", 1)
    return ROOT / f"{stem}.example.{ext}"


def load_config() -> dict:
    with open(_personal_or_example("config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- answer bank
_TOTAL_PATTERNS = ("total years", "total experience", "overall experience",
                   "years of work experience")


def load_answers() -> list:
    with open(_personal_or_example("answers.yaml"), encoding="utf-8") as f:
        answers = yaml.safe_load(f)["answers"]
    return _enforce_total_years(answers)


def _enforce_total_years(answers: list) -> list:
    """Total years of experience can never be less than any single-skill
    answer (a recruiter reading '3 yrs React, 2.5 yrs total' sees a lie)."""
    total_entry = None
    per_skill = []
    for a in answers:
        if a.get("type") != "number":
            continue
        is_total = any(p in m.lower() for m in a["match"] for p in _TOTAL_PATTERNS)
        if is_total:
            total_entry = a
        else:
            try:
                per_skill.append(float(a["value"]))
            except (TypeError, ValueError):
                pass
    if total_entry and per_skill:
        try:
            current = float(total_entry["value"])
        except (TypeError, ValueError):
            current = 0.0
        highest = max(per_skill)
        if current < highest:
            total_entry["value"] = int(highest) if highest.is_integer() else highest
    return answers


def find_answer(answers: list, question: str):
    """Return the first answer entry whose any `match` substring occurs in the question."""
    q = question.lower()
    for entry in answers:
        for pat in entry["match"]:
            if pat.lower() in q:
                return entry
    return None


# ---------------------------------------------------------------- pay signals
# "$40/hr", "USD 20 - 60 per hour", "₹800/hour", "Upto $55/hr" ...
_RATE_RE = re.compile(
    r"((?:up\s?to\s+)?(?:\$|usd\s?|₹|inr\s?)\s?\d{1,4}(?:[.,]\d{3})*"
    r"(?:\s?(?:-|–|to)\s?(?:\$|usd\s?|₹|inr\s?)?\d{1,4}(?:[.,]\d{3})*)?"
    r"\s?(?:/|per\s?)\s?(?:hour|hr))", re.I)
_HOURLY_HINT = re.compile(r"\b(hourly|per[- ]hour|hourly rate|paid hourly)\b", re.I)
_FIXED_HINT = re.compile(
    r"\b(fixed[- ](price|bid|cost|budget)|project[- ]based pay|per[- ]project|"
    r"milestone[- ]based|lump[- ]sum|deliverable[- ]based)\b", re.I)
_INR_RE = re.compile(r"₹|\binr\b|\brs\.?", re.I)


def extract_hourly_rate(text: str) -> str:
    m = _RATE_RE.search(text or "")
    return m.group(1).strip() if m else ""


def rate_below_floor(rate_text: str, floor_usd) -> bool:
    """True only when an advertised USD rate is clearly below the floor.
    INR rates and missing rates are never judged (not comparable)."""
    if not rate_text or not floor_usd:
        return False
    if _INR_RE.search(rate_text):
        return False
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", rate_text)]
    return bool(nums) and max(nums) < float(floor_usd)


# ---------------------------------------------------------------- scoring
def _word(kw: str) -> str:
    return r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])"


def score_job(cfg: dict, title: str, description: str) -> float:
    s_cfg = cfg["scoring"]
    title_l = (title or "").lower()
    text = title_l + " " + (description or "").lower()
    score = 0.0
    for kw, weight in s_cfg["keywords"].items():
        if re.search(_word(kw), text):
            score += weight
            if re.search(_word(kw), title_l):
                score += s_cfg["title_bonus"]
    if re.search(r"\b(contract|freelance|hourly|c2c|corp.to.corp|part[- ]time)\b", text):
        score += s_cfg["contract_bonus"]
    if _RATE_RE.search(text) or _HOURLY_HINT.search(text) or _FIXED_HINT.search(text):
        score += s_cfg.get("hourly_bonus", 6)
    return score


def has_skill_match(cfg: dict, title: str, description: str) -> bool:
    """At least one configured tech keyword must appear."""
    text = f"{title or ''} {description or ''}".lower()
    return any(re.search(_word(kw), text) for kw in cfg["scoring"]["keywords"])


# ---------------------------------------------------------------- hard filters
def passes_filters(cfg: dict, title: str) -> bool:
    title_l = (title or "").lower()
    return not any(bad.lower() in title_l for bad in cfg["search"]["exclude_title_keywords"])


def is_excluded_company(cfg: dict, company: str) -> bool:
    """Aggregators that repost other companies' jobs as their own (Hired,
    Crossing Hurdles, ...). Matches the whole name or a leading word, so
    'Hired' blocks 'Hired Inc' but not 'Hiredly'."""
    c = re.sub(r"\s+", " ", (company or "").lower()).strip(" .,")
    if not c:
        return False
    for bad in cfg["search"].get("exclude_companies", []) or []:
        b = bad.lower().strip()
        if b and re.fullmatch(re.escape(b) + r"( .*)?", c):
            return True
    return False


ALWAYS_REMOTE_SOURCES = {"remoteok", "weworkremotely"}

_REMOTE_NEG = re.compile(
    r"\b(on[- ]?site|onsite|work from office|wfo|hybrid|in[- ]?office|"
    r"office[- ]based|relocat(e|ion) required)\b", re.I)
_REMOTE_NEG_OVERRIDE = re.compile(r"\b(fully remote|100%\s*remote|remote[- ]first)\b", re.I)
_REMOTE_POS = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed team)\b", re.I)

_CONTRACT_RE = re.compile(
    r"\b(contract|contractor|contractual|freelance|freelancer|hourly|c2c|"
    r"corp[- ]to[- ]corp|consultant|consulting|temporary|temp[- ]to[- ]hire|"
    r"per[- ]hour|short[- ]term|project[- ]based|gig|part[- ]?time)\b", re.I)


def is_remote_listing(source: str, title: str, location: str, description: str) -> bool:
    if source in ALWAYS_REMOTE_SOURCES:
        return True
    text = f"{title or ''} {location or ''} {description or ''}"
    if _REMOTE_NEG.search(text) and not _REMOTE_NEG_OVERRIDE.search(text):
        return False
    return bool(_REMOTE_POS.search(text))


def is_contract_listing(title: str, description: str, job_type: str = "") -> bool:
    """Contract / freelance / hourly / part-time signal in title, type or body."""
    return bool(_CONTRACT_RE.search(f"{title or ''} {job_type or ''} {description or ''}"))


_LINKEDIN_HOST = re.compile(r"(^|\.)linkedin\.com$", re.I)


def external_apply_url(source: str, direct_url: str) -> str:
    """For LinkedIn posts, the direct-apply URL jobspy extracts. If it leaves
    linkedin.com the post is NOT Easy Apply whatever the search filter said.
    Returns the off-site URL (unwrapping LinkedIn's safety redirect) or ''."""
    if source != "linkedin" or not direct_url:
        return ""
    try:
        parsed = urlparse(direct_url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    if _LINKEDIN_HOST.search(host):
        wrapped = parse_qs(parsed.query).get("url", [""])[0]
        if wrapped and not _LINKEDIN_HOST.search(urlparse(wrapped).hostname or ""):
            return wrapped
        return ""
    return direct_url


# ---------------------------------------------------------------- misc
def human_pause(cfg: dict, label: str = ""):
    lo = cfg["apply"]["min_delay_seconds"]
    hi = cfg["apply"]["max_delay_seconds"]
    t = random.uniform(lo, hi)
    print(f"  [pause {t:.0f}s {label}]")
    time.sleep(t)


def cover_note(cfg: dict, job_title: str, company: str) -> str:
    p = cfg["profile"]
    hrs = p.get("availability_hours_per_week")
    avail = f"can commit up to {hrs} hrs/week" if hrs else "can start immediately"
    # profile.cover_note in config.yaml is a template; placeholders below.
    template = p.get("cover_note") or (
        "Hi, I'm {name}, a full-stack engineer with {years}+ years shipping production "
        "systems end to end. The {job_title} role at {company} matches my stack directly. "
        "I'm looking for contract, part-time or freelance engagements and {avail}. "
        "GitHub: {github}"
    )
    return " ".join(template.split()).format(
        name=p["name"], years=p["years_experience"], job_title=job_title,
        company=company, avail=avail, github=p["github"])
