"""Normalize and verify profile links (GitHub, LinkedIn, portfolio).

GitHub can be truly verified — its public API confirms a username exists.
LinkedIn blocks automated requests (HTTP 999/403), so we can only normalize
the URL and check the handle's shape; the UI marks it "unverified, open it".
"""
import re

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# strip tracking junk LinkedIn/GitHub links pick up when copy-pasted
_TRACKING = re.compile(r"[?&](utm_[^&]+|trk|originalSubdomain|miniProfileUrn)=[^&]*", re.I)


def _clean(url: str) -> str:
    url = (url or "").strip().strip("<>,;")
    url = _TRACKING.sub("", url)
    return url.rstrip("/?&")


def normalize_github(value: str) -> str:
    """Accepts a full URL, 'github.com/user', '@user' or a bare username."""
    v = _clean(value)
    if not v:
        return ""
    m = re.search(r"github\.com/([A-Za-z0-9-]{1,39})", v, re.I)
    handle = m.group(1) if m else re.sub(r"^@", "", v)
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", handle or ""):
        return ""
    return f"https://github.com/{handle}"


def normalize_linkedin(value: str) -> str:
    """Accepts a full URL (any country subdomain), 'linkedin.com/in/x' or a handle."""
    v = _clean(value)
    if not v:
        return ""
    m = re.search(r"linkedin\.com/in/([A-Za-z0-9\-%_.]+)", v, re.I)
    handle = m.group(1) if m else re.sub(r"^@", "", v)
    if not re.fullmatch(r"[A-Za-z0-9\-%_.]{3,100}", handle or ""):
        return ""
    return f"https://www.linkedin.com/in/{handle}"


def check_github(url: str) -> dict:
    """Verify via the public GitHub API (no key needed)."""
    norm = normalize_github(url)
    if not norm:
        return {"status": "invalid", "url": url,
                "message": "Not a valid GitHub username or URL"}
    handle = norm.rsplit("/", 1)[-1]
    try:
        r = requests.get(f"https://api.github.com/users/{handle}",
                         headers={"User-Agent": UA, "Accept": "application/vnd.github+json"},
                         timeout=12)
        if r.status_code == 200:
            d = r.json()
            who = d.get("name") or d.get("login")
            return {"status": "valid", "url": norm,
                    "message": f"✓ {who} · {d.get('public_repos', 0)} public repos"}
        if r.status_code == 404:
            return {"status": "invalid", "url": norm,
                    "message": f"No GitHub user '{handle}' — check the spelling"}
        return {"status": "unverified", "url": norm,
                "message": f"GitHub API returned {r.status_code}; link looks well-formed"}
    except Exception:
        return {"status": "unverified", "url": norm,
                "message": "Offline — link looks well-formed but was not checked"}


def check_linkedin(url: str) -> dict:
    """LinkedIn blocks bots, so this validates shape and flags for eyeballing."""
    norm = normalize_linkedin(url)
    if not norm:
        return {"status": "invalid", "url": url,
                "message": "Not a valid LinkedIn profile URL (expected linkedin.com/in/your-handle)"}
    handle = norm.rsplit("/", 1)[-1]
    try:
        r = requests.get(norm, headers={"User-Agent": UA}, timeout=12,
                         allow_redirects=True)
        if r.status_code == 404:
            return {"status": "invalid", "url": norm,
                    "message": f"LinkedIn says '{handle}' does not exist"}
        if r.status_code == 200 and "authwall" not in r.url:
            return {"status": "valid", "url": norm, "message": "✓ Profile is reachable"}
    except Exception:
        pass
    return {"status": "unverified", "url": norm,
            "message": "LinkedIn blocks automated checks — click Open to confirm it's you"}


def check_url(url: str) -> dict:
    """Generic reachability check for a portfolio/site link."""
    u = _clean(url)
    if not u:
        return {"status": "invalid", "url": url, "message": "Empty URL"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        r = requests.head(u, headers={"User-Agent": UA}, timeout=12, allow_redirects=True)
        if r.status_code < 400:
            return {"status": "valid", "url": u, "message": f"✓ Reachable ({r.status_code})"}
        return {"status": "invalid", "url": u, "message": f"Returned HTTP {r.status_code}"}
    except Exception as e:
        return {"status": "unverified", "url": u,
                "message": f"Could not reach it ({type(e).__name__})"}
