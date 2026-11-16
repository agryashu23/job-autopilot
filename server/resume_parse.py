"""Resume parsing: heuristics first (free, instant), local LLM (Ollama) to
refine when available. Never uses cloud tokens.

Output shape == the editable profile the frontend renders.
"""
import re

import pdfplumber

from . import ollama_client

SECTION_ALIASES = {
    "skills": ["skills", "technical skills", "tech stack", "technologies"],
    "experience": ["work experience", "experience", "employment",],
    "projects": ["projects", "personal projects", "side projects", "internships"],
    "education": ["education", "academics", "qualifications"],
    "certifications": ["certification", "certifications", "certificates", "courses"],
    "summary": ["summary", "about", "profile", "objective"],
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[-\s]?)?\d{10}|\+\d{1,3}[-\s]?\d{4,12}")
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w.-]+", re.I)
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w.-]+", re.I)

# tokens that look like tech skills (used to harvest from a skills section)
SKILL_SPLIT_RE = re.compile(r"[,•|/\n·;]+")

_BULLET_RE = re.compile(r"^[•\-–*·▪○]")
_ACTION_VERBS = {
    "designed", "built", "developed", "led", "implemented", "integrated",
    "architected", "owned", "created", "managed", "worked", "shipped", "acted",
    "maintained", "improved", "reduced", "migrated", "automated", "delivered",
    "launched", "collaborated", "handled", "gained", "wrote", "deployed",
    "engineered", "optimized", "co-developed", "independently", "successfully",
}
_DATE_RE = re.compile(
    r"((?:\w{3,9}\.?\s)?20\d{2}\s*(?:-|–|—|to)\s*(?:(?:\w{3,9}\.?\s)?20\d{2}|present)"
    r"|\d+\s*(?:months?|days?|years?))", re.I)
_TITLE_SPLIT_RE = re.compile(r"\s+(?:—|–|\||,|@|\bat\b)\s+| - ")


def _parse_blocks(section_text: str) -> list[dict]:
    """Generic block parser for experience/projects sections.

    A short, non-bullet line that doesn't end with '.' starts a new entry
    (it's a heading like 'Software Engineer — Acme Inc.   Sept 2025 - June 2026').
    Everything else accumulates into that entry. Afterwards, a leading
    non-bullet line followed by bullets is treated as a tagline and dropped —
    the rest becomes the description verbatim.
    """
    entries = []
    for raw in section_text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        is_bullet = bool(_BULLET_RE.match(line))
        header_like = (not is_bullet and len(line) <= 95
                       and not line.endswith((".", ";", ":")))

        # a lone date line belongs to the previous heading (sidebar layouts)
        if header_like and entries and not entries[-1]["period"]:
            solo = _DATE_RE.fullmatch(line)
            if solo:
                entries[-1]["period"] = solo.group(1)
                continue

        if header_like and (not entries or entries[-1]["_lines"]):
            period = ""
            if m := _DATE_RE.search(line):
                period = m.group(1).strip()
                line = _DATE_RE.sub("", line).strip(" -–—|,")
            parts = _TITLE_SPLIT_RE.split(line, maxsplit=1)
            title = parts[0].strip()
            company = re.sub(r"\s*\(.*?\)\s*", " ", parts[1]).strip() if len(parts) > 1 else ""
            entries.append({"title": title, "company": company,
                            "period": period, "_lines": []})
        elif entries:
            entries[-1]["_lines"].append((line.lstrip("•-–*· \t"), is_bullet))
        else:
            # section starts with body text and no heading — single catch-all entry
            entries.append({"title": "", "company": "", "period": "",
                            "_lines": [(line.lstrip("•-–*· \t"), is_bullet)]})

    for e in entries:
        lines = e.pop("_lines")
        # Drop a leading company tagline: PDFs usually lose bullet glyphs, so
        # detect it by content — a first line that is a noun phrase (doesn't
        # start with an action verb) while later lines do.
        if len(lines) > 1:
            first_is_bullet = lines[0][1]
            first_word = re.split(r"\W+", lines[0][0].lower(), maxsplit=1)[0]
            rest_verby = any(re.split(r"\W+", t.lower(), maxsplit=1)[0] in _ACTION_VERBS
                             for t, _ in lines[1:])
            if not first_is_bullet and first_word not in _ACTION_VERBS and rest_verby:
                lines = lines[1:]
        e["description"] = " ".join(t for t, _ in lines)[:2000]
    return [e for e in entries if e["title"] or e["description"]][:10]


def empty_profile() -> dict:
    return {
        "name": "", "title": "", "email": "", "phone": "",
        "location": "", "github": "", "linkedin": "", "summary": "",
        "years_experience": None,
        "skills": [], "experience": [], "projects": [],
        "education": [], "certifications": [],
        "preferences": {
            "job_types": ["contract", "freelance"],
            "remote_types": ["remote"],
            "locations": ["Anywhere (Remote)"],
            "salary_min": None, "salary_max": None,
            "salary_currency": "USD", "salary_period": "hour",
            "min_company_rating": None,
            "keywords": [],
        },
        "parse_engine": "none",
    }


def _extract_text(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _split_sections(text: str) -> dict:
    """Split resume text on section headings (lines that ARE a known heading)."""
    lines = text.split("\n")
    sections = {"header": []}
    current = "header"
    for line in lines:
        stripped = line.strip().lower().rstrip(":")
        matched = None
        if 0 < len(stripped) <= 40:
            for key, aliases in SECTION_ALIASES.items():
                # contains-match so "Earlier — Internships" still hits "internships"
                if any(stripped == a or stripped.startswith(a) or a in stripped
                       for a in aliases):
                    matched = key
                    break
        if matched:
            current = matched
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _heuristic_parse(text: str) -> dict:
    p = empty_profile()
    p["parse_engine"] = "heuristics"
    sec = _split_sections(text)
    header = sec.get("header", "")

    if m := EMAIL_RE.search(text):
        p["email"] = m.group(0)
    if m := PHONE_RE.search(text):
        p["phone"] = m.group(0).strip()
    if m := GITHUB_RE.search(text):
        p["github"] = "https://" + m.group(0).split("://")[-1]
    if m := LINKEDIN_RE.search(text):
        p["linkedin"] = "https://" + m.group(0).split("://")[-1]

    # name: first non-empty header line that isn't contact info
    for line in header.split("\n"):
        line = line.strip()
        if line and not EMAIL_RE.search(line) and not PHONE_RE.search(line) and len(line) < 50:
            p["name"] = line
            break
    # title: line after the name, if short and not contact
    if m := re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years", text, re.I):
        try:
            p["years_experience"] = float(m.group(1))
        except ValueError:
            pass

    if sec.get("summary"):
        p["summary"] = sec["summary"][:600]
    else:
        # many resumes put the intro paragraph right under the contact line
        # with no "Summary" heading — grab long header lines as the summary
        long_lines = [l.strip() for l in header.split("\n")
                      if len(l.strip()) > 90 and not EMAIL_RE.search(l)
                      and not l.strip().lower().startswith(("open to", "available for"))]
        if long_lines:
            p["summary"] = " ".join(long_lines)[:600]

    if sec.get("skills"):
        raw = SKILL_SPLIT_RE.split(sec["skills"])
        p["skills"] = [s.strip() for s in raw if 1 < len(s.strip()) < 40][:40]

    if sec.get("certifications"):
        items = [l.strip("•- \t") for l in sec["certifications"].split("\n") if l.strip()]
        p["certifications"] = [i for i in items if i][:10]

    if sec.get("projects"):
        blocks = _parse_blocks(sec["projects"])
        if len(blocks) == 1 and not blocks[0]["title"]:
            # no headings found — the section is a flat list; one entry per line,
            # "Name: did things..." style splits on the first colon/em-dash
            blocks = []
            for line in sec["projects"].split("\n"):
                line = line.strip().lstrip("•-–*· \t")
                if len(line) < 15:
                    continue
                m = re.match(r"(.{3,60}?)\s*[:—]\s+(.*)", line)
                if m and line[0].isupper():
                    blocks.append({"title": m.group(1), "company": "", "period": "",
                                   "description": m.group(2)})
                elif blocks:  # wrapped continuation of the previous line
                    blocks[-1]["description"] = (blocks[-1]["description"] + " " + line)[:2000]
                else:
                    blocks.append({"title": line[:60], "company": "", "period": "",
                                   "description": line})
        p["projects"] = [
            {"name": " — ".join(x for x in (b["title"], b["company"]) if x) or b["description"][:60],
             "period": b["period"], "description": b["description"]}
            for b in blocks[:10]
        ]

    if sec.get("education"):
        items = [l.strip() for l in sec["education"].split("\n") if l.strip()]
        p["education"] = [{"degree": i[:100], "institution": "", "year": ""} for i in items][:6]

    if sec.get("experience"):
        p["experience"] = _parse_blocks(sec["experience"])
    return p


LLM_PROMPT = """You are a resume parser. Extract structured data from the resume text below.

Rules:
- "summary": if the resume has its own summary/objective/about paragraph, copy
  THAT text (trimmed to 2-3 sentences). Only if there is none, write a short
  2-sentence summary yourself. Never longer than 3 sentences.
- "experience": one entry per JOB. "title" is the designation ONLY (e.g.
  "Software Engineer") — NEVER include the company name in it. "company" is the
  company name only. "period" exactly as written (e.g. "Sept 2025 - June 2026").
  "description" = ALL remaining text of that job's block (its bullet points /
  paragraphs) after removing the title, company, period and any one-line
  company tagline — join it as written, do NOT rewrite or summarize it.
- "projects": personal projects AND internships both go here (an internship
  entry's name = "Role, Company"). Same description rule as experience.

Respond with ONLY a JSON object, no markdown, matching exactly this schema:
{{
  "name": "", "title": "", "email": "", "phone": "", "location": "",
  "github": "", "linkedin": "", "summary": "",
  "years_experience": 0.0,
  "skills": ["skill1", ...],
  "experience": [{{"company": "", "title": "", "period": "", "description": ""}}],
  "projects": [{{"name": "", "period": "", "description": ""}}],
  "education": [{{"degree": "", "institution": "", "year": ""}}],
  "certifications": ["", ...]
}}

RESUME TEXT:
{text}
"""


def parse_resume_fast(pdf_path: str) -> tuple[dict, str]:
    """Instant heuristic parse. Returns (profile, raw_text) — text is kept
    so the slow LLM refinement can run afterwards without re-reading the PDF."""
    text = _extract_text(pdf_path)
    return _heuristic_parse(text), text


def llm_refine(profile: dict, text: str) -> dict:
    """Slow path (1-3 min on CPU): local LLM re-reads the resume and
    overrides/extends the heuristic fields. Returns the improved profile."""
    profile = dict(profile)
    if not (ollama_client.available() and ollama_client.model_ready()):
        return profile
    llm = ollama_client.generate_json(LLM_PROMPT.format(text=text[:7000]))
    if llm:
        for key in ("name", "title", "email", "phone", "location", "github",
                    "linkedin", "summary", "years_experience"):
            if llm.get(key):
                profile[key] = llm[key]
        for key in ("skills", "experience", "projects", "education", "certifications"):
            if isinstance(llm.get(key), list) and llm[key]:
                profile[key] = llm[key]
        profile["parse_engine"] = "heuristics+ollama"
    return profile


def parse_resume(pdf_path: str) -> dict:
    """Blocking convenience wrapper (fast parse + LLM refinement)."""
    profile, text = parse_resume_fast(pdf_path)
    return llm_refine(profile, text)
