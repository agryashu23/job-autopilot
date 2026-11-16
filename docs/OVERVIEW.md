# Job Autopilot — Complete Overview

*A beginner-friendly map of everything in this system: what you put in, what
was built, and how the AI parts actually work.*

---

## 1. Your inputs (everything the system knows comes from these)

| Input | Where it lives | What it feeds |
|---|---|---|
| Resume PDF | `profile.resume_path` in `config.yaml` | Parsed into your profile; attached to applications |
| Job profile (skills, projects, education, certifications) | `profile.json` (edited in the web app) | AI matching, extension autofill |
| Preferences: contract/freelance only, remote only, locations, pay floor, min company rating, keywords | `profile.json → preferences` | **Hard filters** at discovery + scoring boosts |
| Questionnaire answer bank (years of experience, expected rate, notice period, ...) | `answers.yaml` | LinkedIn/Naukri auto-apply, extension autofill, apply kit |
| Search terms, skill weights, daily apply caps, ATS company list | `config.yaml` | Discovery + scoring |
| Approve / Deny clicks | jobs board | Decides what gets applied to (and nothing gets submitted without you) |
| Logged-in browser session | `chrome-profile\` (debug Chrome, port 9222) | Naukri discovery + Playwright-assisted applying. No passwords stored anywhere |

## 2. Every feature built

### Resume & profile
- Contract-tuned one-page resume, regenerable from an HTML source (kept out of git)
- Resume parser: **heuristics** (rules/regex over the PDF text) + **local AI** (qwen2.5:3b) cleanup
- Web profile editor: basics, skills chips, projects, education, certifications, preference dropdowns

### Job discovery — 7 sources, all keyless
| Source | How | Notes |
|---|---|---|
| LinkedIn | jobspy public search | Easy-Apply filter on; no login needed to *find* jobs |
| Naukri | your real Chrome session (CDP) | Direct API is bot-blocked (406); we ride the browser instead |
| RemoteOK | public API | remote-only board |
| WeWorkRemotely | RSS feeds | remote-only board |
| Remotive | official public API | remote-only, has a job-type field |
| Greenhouse boards | official public JSON | per-company (gitlab, postman, vercel, …) — extend in config |
| Lever boards | official public JSON | per-company (plaid, kraken, …) |

### Filtering & scoring (deterministic — plain code, no AI)
- **Hard filters** (violating job never reaches you): must be remote (onsite/hybrid wording rejected), must be contract/freelance/hourly/project-based, must match ≥1 of your tech skills, title blocklist (intern/.net/php/…)
- **Scoring**: weighted skill keywords (CouchDB weighted highest — rare skill), title bonus, contract bonus, hourly/fixed-price pay bonus (+6), pay-rate extraction (`$40-70/hr` shown as 💰 badge)
- Dedupe by (source, company, title) — you never see the same job twice

### AI matching (all local, zero cloud tokens)

**Field-level vectors, not one blob.** Your profile is embedded as three separate
vectors — *role* (title + what you're seeking), *skills*, *experience* — and each
job as two — *title*, *body*. Cosine similarity is computed per meaningful pair
and blended:

| Profile field ↔ Job field | Weight |
|---|---|
| role ↔ title | 0.30 |
| skills ↔ body | 0.30 |
| experience ↔ body | 0.15 |
| role ↔ body | 0.10 |
| exact skill-token overlap (not embeddings) | 0.15 |

**Numeric facts are compared arithmetically, never as vectors** — embedding
"8 years" doesn't preserve magnitude, so vector length would be meaningless.
Instead: required years vs yours (−22 if they want 4+ more), advertised rate vs
your pay floor (+8 above it, −12 below).

Result is one **0-100 match_score** with a plain-English reason
("matches aws, mongodb, python · pays $90/hr"). ≥55 promotes into your queue,
<40 demotes out. Sanity check from a real run: dev contracts scored 50-69 while
HR Business Partner / Legal VP / Product Manager scored 4-11.

### Review & applying (human always in control)
- Web app (localhost:5580): tabs, AI-fit/EASY-APPLY/💰 badges, Find-new-jobs button, approve/deny
- **Approve** → job opens in new tab + apply-kit popup (cover note, resume path, all answers click-to-copy)
- Playwright auto-apply (`run.py apply`): LinkedIn Easy Apply modals + Naukri chatbot, filled from the answer bank; daily caps (15/30), randomized 20-75s pauses; **unknown question ⇒ stops without submitting** and files under Needs-attention; external-apply jobs get their real company URL captured for you
- Chrome extension (`extension/`, load unpacked): on ANY application page, fills text/selects/radios from profile + answers, highlights the resume upload, lists unanswered questions — **has no ability to submit**
- Marketplaces tab: 12 talent platforms (Mercor, Braintrust, Turing, Arc…) where one signup = ongoing contract matching

### Infrastructure
- SQLite (`jobs.db`) for jobs/status; `profile.json` for you; no server, no accounts, no keys, no cloud
- CLI: `run.py discover | match | web | schedule | stats | test | reset | chrome | apply --yes`

## 3. How the AI parts work (plain-language)

**The pipeline:**
```
 boards/APIs → hard filters → keyword score → embeddings (match %) 
   → stack extraction + fit computation → your Review queue 
   → YOU approve → assisted apply → YOU submit
```

**What's an LLM?** A neural network trained on huge amounts of text that
predicts the next word. Given "extract the skills from this resume," it can
read messy text and produce structured data. Yours (qwen2.5:3b) runs on your
own PC via **Ollama** — private and free — instead of a paid cloud API.

**What are embeddings?** A model that turns text into a list of ~768 numbers
(a *vector*) where similar meanings land close together. "Node.js backend
developer" and "server-side JavaScript engineer" end up near each other even
though they share few words. We embed your profile once and every job once;
**cosine similarity** (angle between vectors) gives the match %. This is the
retrieval half of what's called RAG (retrieval-augmented generation).

**Why doesn't the AI decide the fit score?** We tried (three prompt designs).
A 3-billion-parameter model is great at *extraction* ("list what this job
requires") but unreliable at *judgment* ("how good is this match?") — it
drifted generous, then parroted rejection rules, then anchored on the example
in the prompt. The lesson (a common one in AI engineering): **let the model
extract facts, let code make decisions.** The fit score is now computed by
plain Python — same inputs, same score, every time, with an explainable
reason.

**Heuristics** = hand-written rules (regex). They handle the predictable 80%
(emails, phone numbers, section headers, "$40/hr" patterns) instantly; the LLM
handles the messy remainder.

**Where AI is deliberately NOT used:** clicking submit. Every application is
approved by you and submitted by you — that's the compliance line that keeps
your accounts safe.

## 4. Cost & requirements

- **Cloud/API cost: ₹0.** Everything is local (Ollama models are a one-time
  download: qwen2.5:3b ≈ 2GB, nomic-embed-text ≈ 270MB).
- **Keys/hosting: none.** All job sources are public APIs; the site runs on
  localhost.
- Requirements: keep `python run.py web` running while using the site or
  extension; debug Chrome open (and logged in) for Naukri discovery and
  Playwright applying.
