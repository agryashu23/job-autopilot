"""Feature extraction + weighted scoring for job matching.

Every job is reduced to a handful of named features, each scored 0..1, then
combined with the weights in config.yaml (scoring.weights). Nothing here is
a black box: `explain` returns each feature's contribution so the UI can say
exactly why a job ranked where it did.
"""
import re

# ---------------------------------------------------------------- vocabulary
ALIASES = {
    "nodejs": "node.js", "node": "node.js", "node js": "node.js",
    "reactjs": "react", "react.js": "react", "react js": "react",
    "ts": "typescript", "js": "javascript", "es6": "javascript",
    "expressjs": "express", "express.js": "express",
    "postgres": "postgresql", "psql": "postgresql", "mongo": "mongodb",
    "golang": "go", "vuejs": "vue", "vue.js": "vue",
    "next.js": "nextjs", "nuxt.js": "nuxtjs",
    "amazon web services": "aws", "google cloud": "gcp",
    "restful apis": "rest", "rest apis": "rest", "rest api": "rest",
    "websockets": "websocket", "ci/cd": "cicd", "ci cd": "cicd",
    "k8s": "kubernetes", "eks": "kubernetes", "gke": "kubernetes",
    "reactnative": "react native", "dotnet": ".net", "c sharp": "c#",
}

# Canonical technologies we can recognise in a posting. Order matters only for
# readability; matching is whole-word.
TECH_VOCAB = [
    "node.js", "express", "nestjs", "javascript", "typescript", "python", "django",
    "flask", "fastapi", "java", "spring", "kotlin", "go", "rust", "ruby", "rails",
    "php", "laravel", ".net", "c#", "c++", "scala", "elixir",
    "react", "nextjs", "vue", "nuxtjs", "angular", "svelte", "redux", "jquery",
    "html", "css", "tailwind", "bootstrap", "react native", "flutter", "dart",
    "swift", "android", "ios",
    "mongodb", "postgresql", "mysql", "sqlite", "redis", "couchdb", "pouchdb",
    "dynamodb", "cassandra", "elasticsearch", "sql", "nosql", "prisma",
    "graphql", "rest", "grpc", "websocket", "rabbitmq", "kafka", "celery",
    "aws", "gcp", "azure", "lambda", "cloudfront", "s3", "ec2", "docker",
    "kubernetes", "terraform", "jenkins", "cicd", "nginx", "linux", "serverless",
    "microservices", "git", "github actions", "jira", "agile", "scrum",
    "firebase", "supabase", "stripe", "razorpay", "twilio", "openai", "langchain",
    "pandas", "numpy", "pytorch", "tensorflow", "machine learning",
]

_REQ_HEADERS = re.compile(
    r"(requirements?|qualifications?|must[- ]?haves?|what you.{0,15}(need|bring)|"
    r"skills? (required|needed)|we.{0,10}(are )?looking for|you (should )?have)", re.I)
_NICE_HEADERS = re.compile(
    r"(nice[- ]to[- ]have|good[- ]to[- ]have|bonus|preferred|plus(es)?\b|"
    r"desirable|advantage)", re.I)
_REQ_INLINE = re.compile(
    r"(must have|required|strong (experience|knowledge)|proficient|expertise in|"
    r"expert in|at least \d|minimum \d|\d\+? years)", re.I)

_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|head of|manager)\b", re.I)
_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|intern|trainee|entry[- ]level|graduate)\b", re.I)


# Having the key implies having the values (a React dev knows JavaScript).
# NOTE: relationships must be explicit — never inferred by substring, or
# "java" matches "javascript" and a Java role looks like a perfect fit.
IMPLIES = {
    "node.js": {"javascript"}, "typescript": {"javascript"},
    "react": {"javascript", "html", "css"}, "nextjs": {"react", "javascript"},
    "vue": {"javascript"}, "angular": {"javascript", "typescript"},
    "express": {"node.js", "rest"}, "nestjs": {"node.js", "typescript"},
    "mongodb": {"nosql"}, "couchdb": {"nosql"}, "postgresql": {"sql"},
    "mysql": {"sql"}, "sqlite": {"sql"}, "dynamodb": {"nosql"},
    "flutter": {"dart"}, "react native": {"react", "javascript"},
    "kubernetes": {"docker"}, "lambda": {"aws", "serverless"},
    "cloudfront": {"aws"}, "s3": {"aws"}, "ec2": {"aws"},
    "django": {"python"}, "flask": {"python"}, "fastapi": {"python"},
    "github actions": {"cicd", "git"}, "jenkins": {"cicd"},
}


def profile_techs(profile: dict) -> set:
    """Canonical technologies the candidate actually has — derived with the
    SAME vocabulary used on job postings, so comparisons are apples-to-apples."""
    parts = [" ".join(s for s in profile.get("skills", []) if isinstance(s, str)),
             profile.get("title") or "", profile.get("summary") or ""]
    for e in profile.get("experience", []) or []:
        if isinstance(e, dict):
            parts.append(f"{e.get('title','')} {e.get('description','')}")
    for pr in profile.get("projects", []) or []:
        if isinstance(pr, dict):
            parts.append(f"{pr.get('name','')} {pr.get('description','')}")
    techs = techs_in(" ".join(parts))
    for t in list(techs):
        techs |= IMPLIES.get(t, set())
    return techs


def canon(term: str) -> str:
    t = re.sub(r"[^a-z0-9+#. ]", "", (term or "").lower()).strip()
    return ALIASES.get(t, t)


def _has_word(text: str, term: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


def techs_in(text: str) -> set:
    """Canonical technologies mentioned in a blob of text."""
    t = (text or "").lower()
    found = set()
    for term in TECH_VOCAB:
        if _has_word(t, term):
            found.add(term)
    for alias, canonical in ALIASES.items():
        if _has_word(t, alias):
            found.add(canonical)
    return found


def extract_requirements(title: str, description: str) -> dict:
    """Split a posting into must-have vs nice-to-have technologies.

    Works off section headers ('Requirements:', 'Nice to have:') and inline
    phrasing ('must have', 'strong experience with', '5+ years of').
    """
    desc = description or ""
    lines = re.split(r"[\n•·]|(?<=[.;])\s+", desc)
    must, nice, mode = set(), set(), None

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if _NICE_HEADERS.search(s) and len(s) < 120:
            mode = "nice"
        elif _REQ_HEADERS.search(s) and len(s) < 120:
            mode = "must"
        found = techs_in(s)
        if not found:
            continue
        if _NICE_HEADERS.search(s):
            nice |= found
        elif mode == "must" or _REQ_INLINE.search(s):
            must |= found
        elif mode == "nice":
            nice |= found
        else:
            nice |= found

    title_techs = techs_in(title)
    must |= title_techs           # anything in the title is non-negotiable
    nice -= must

    # No structure detected -> treat the most-mentioned techs as required
    if not must:
        must = set(list(techs_in(desc))[:5])
        nice -= must

    primary = ""
    for term in sorted(title_techs) or sorted(must):
        primary = term
        break

    min_years = None
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", desc, re.I)
    if m:
        try:
            min_years = float(m.group(1))
        except ValueError:
            pass
    return {"must": sorted(must), "nice": sorted(nice),
            "primary": primary, "min_years": min_years}


def experience_fit(required_years, my_years) -> float:
    """1.0 up to my level, still good at +1 year, then falls off a cliff.
    (Your rule: total 3 -> search up to 4, beyond that only if other
    features carry it.)"""
    if not required_years or not my_years:
        return 0.75            # unstated: neutral-positive, don't punish
    gap = float(required_years) - float(my_years)
    if gap <= 0:
        return 1.0
    if gap <= 1:
        return 0.85
    if gap <= 2:
        return 0.45
    if gap <= 3:
        return 0.20
    return 0.0


def seniority_fit(title: str, my_years) -> float:
    t = title or ""
    if _JUNIOR_RE.search(t):
        return 0.5             # under-levelled: pay will disappoint
    if _SENIOR_RE.search(t):
        return 0.9 if (my_years or 0) >= 3 else 0.55
    return 1.0


def pay_fit(rate_text: str, description: str, floor, currency="USD") -> float:
    blob = f"{rate_text or ''} {description or ''}"
    has_hourly = bool(re.search(r"\b(hourly|per hour|/hr|/hour)\b", blob, re.I))
    has_fixed = bool(re.search(r"\b(fixed[- ](price|bid|budget)|per project|milestone)\b",
                               blob, re.I))
    if not rate_text and not has_hourly and not has_fixed:
        return 0.4             # no pay signal at all
    if rate_text and floor:
        nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", rate_text)]
        if nums:
            top = max(nums)
            if top >= float(floor) * 1.5:
                return 1.0
            if top >= float(floor):
                return 0.85
            return 0.15        # explicitly below your floor
    return 0.8 if (has_hourly or has_fixed or rate_text) else 0.5


def score(profile_skills: set, my_years, job: dict, sims: dict, weights: dict) -> tuple:
    """Combine all features. `sims` holds the embedding similarities already
    computed (0..1). Returns (score 0-100, explanation dict)."""
    reqs = extract_requirements(job.get("title", ""), job.get("description", ""))
    must, nice = set(reqs["must"]), set(reqs["nice"])

    # EXACT canonical membership only (see IMPLIES note above).
    have_must = must & profile_skills
    missing = sorted(must - have_must)
    coverage = len(have_must) / len(must) if must else 0.5

    have_nice = nice & profile_skills
    nice_cov = len(have_nice) / len(nice) if nice else 0.5

    primary = reqs["primary"]
    primary_ok = (not primary) or (primary in profile_skills)

    # A posting that mentions only one technology is weak evidence — 100%
    # coverage of a single word shouldn't equal 100% coverage of eight.
    # Below 3 detected requirements we lean on the title-similarity signal.
    conf = min(1.0, len(must) / 3.0)
    coverage_feat = coverage * conf + sims.get("role_title", 0.0) * (1 - conf)

    feats = {
        "must_have_coverage": coverage_feat,
        "role_title_semantic": sims.get("role_title", 0.0),
        "skills_semantic": sims.get("skills_body", 0.0),
        "experience_years": experience_fit(reqs["min_years"], my_years),
        "skill_overlap": nice_cov,
        "experience_domain": sims.get("experience_body", 0.0),
        "pay": pay_fit(job.get("rate", ""), job.get("description", ""),
                       job.get("salary_min")),
        "seniority": seniority_fit(job.get("title", ""), my_years),
    }
    total_w = sum(weights.get(k, 0) for k in feats) or 1
    raw = sum(feats[k] * weights.get(k, 0) for k in feats) / total_w
    final = 100 * raw
    if not primary_ok:
        final *= 0.70          # its core technology isn't yours
    final = max(0.0, min(100.0, final))

    return final, {
        "features": {k: round(v, 3) for k, v in feats.items()},
        "contributions": {k: round(feats[k] * weights.get(k, 0), 2) for k in feats},
        "must": sorted(must), "have": sorted(have_must), "missing": missing,
        "primary": primary, "primary_ok": primary_ok,
        "min_years": reqs["min_years"],
    }


def reason_text(exp: dict, rate: str = "") -> str:
    bits = []
    if exp["have"]:
        bits.append("has " + ", ".join(exp["have"][:5]))
    if exp["missing"]:
        bits.append("missing " + ", ".join(exp["missing"][:4]))
    if not exp["primary_ok"] and exp["primary"]:
        bits.append(f"core tech {exp['primary']} not in your profile")
    if exp["min_years"]:
        bits.append(f"wants {exp['min_years']:.0f}y")
    if rate:
        bits.append(f"pays {rate}")
    return " · ".join(bits)[:300]
