"""LinkedIn Easy Apply executor.

Only handles Easy Apply jobs. Fills modal steps from the answer bank;
anything it can't answer confidently -> job marked needs_attention and
the modal is closed WITHOUT submitting. Never guesses.
"""
import re
import time

from . import db
from .util import load_answers, find_answer, human_pause


def _fill_step(page, answers, profile, unknown):
    """Fill all visible inputs in the current Easy Apply modal step."""
    modal = page.locator("div.jobs-easy-apply-modal, div[data-test-modal]").first

    # --- resume upload ---
    for finput in modal.locator("input[type=file]").all():
        try:
            finput.set_input_files(profile["resume_path"])
            time.sleep(1.5)
        except Exception:
            pass

    # --- text / numeric inputs ---
    for inp in modal.locator("input[type=text], input[type=tel], input[type=number], textarea").all():
        try:
            if inp.input_value().strip():
                continue  # already pre-filled by LinkedIn
            label = _label_for(modal, inp)
            if not label:
                continue
            entry = find_answer(answers, label)
            if entry:
                inp.fill(str(entry["value"]))
                time.sleep(0.4)
            else:
                unknown.append(label)
        except Exception:
            continue

    # --- radio groups (Yes/No etc.) ---
    for fieldset in modal.locator("fieldset").all():
        try:
            legend = fieldset.locator("legend").inner_text(timeout=800).strip()
        except Exception:
            continue
        entry = find_answer(answers, legend)
        if not entry:
            if fieldset.locator("input[type=radio]:checked").count() == 0:
                unknown.append(legend)
            continue
        target = "yes" if (entry["type"] == "bool" and entry["value"]) else \
                 "no" if entry["type"] == "bool" else str(entry["value"]).lower()
        for lab in fieldset.locator("label").all():
            try:
                if target in lab.inner_text(timeout=500).lower():
                    lab.click()
                    time.sleep(0.3)
                    break
            except Exception:
                continue

    # --- selects ---
    for sel in modal.locator("select").all():
        try:
            label = _label_for(modal, sel)
            if not label:
                continue
            entry = find_answer(answers, label)
            if not entry:
                if sel.input_value() in ("", "Select an option"):
                    unknown.append(label)
                continue
            target = "Yes" if (entry["type"] == "bool" and entry["value"]) else \
                     "No" if entry["type"] == "bool" else str(entry["value"])
            options = sel.locator("option").all_inner_texts()
            match = next((o for o in options if target.lower() in o.lower()), None)
            if match:
                sel.select_option(label=match)
                time.sleep(0.3)
            else:
                unknown.append(label)
        except Exception:
            continue


def _label_for(modal, element):
    try:
        el_id = element.get_attribute("id")
        if el_id:
            lab = modal.locator(f'label[for="{el_id}"]')
            if lab.count():
                return lab.first.inner_text(timeout=800).strip()
    except Exception:
        pass
    return None


def apply_one(page, job, cfg, answers):
    """Returns (status, note)."""
    profile = cfg["profile"]
    page.goto(job["url"], timeout=60000)
    time.sleep(4)

    apply_re = re.compile(r"\bapply\b", re.I)
    candidates = page.get_by_role("button", name=apply_re)
    if not candidates.count():
        candidates = page.get_by_role("link", name=apply_re)
    if not candidates.count():
        if page.locator("span:has-text('No longer accepting applications')").count():
            return "skipped", "job closed (no longer accepting applications)"
        return "skipped", "no apply button found (job may be closed)"

    btn = candidates.first
    name = ((btn.inner_text() or "") + " " +
            (btn.get_attribute("aria-label") or "")).lower()

    if "easy apply" not in name:
        # External application ("Responses managed off LinkedIn").
        # Click once to capture the company's real application URL for manual use.
        ext_url = ""
        try:
            with page.expect_popup(timeout=8000) as pop:
                btn.click()
            ext_url = pop.value.url
            pop.value.close()
        except Exception:
            if "linkedin.com/jobs" not in page.url:
                ext_url = page.url
                page.go_back()
        note = "external application — apply manually"
        if ext_url:
            note += f" → {ext_url[:200]}"
        return "needs_attention", note

    btn.click()
    time.sleep(2.5)

    unknown = []
    for _step in range(10):  # Easy Apply is at most ~6 steps
        _fill_step(page, answers, profile, unknown)

        if unknown:
            # close without submitting
            page.keyboard.press("Escape")
            time.sleep(1)
            discard = page.locator("button:has-text('Discard')")
            if discard.count():
                discard.first.click()
            return "needs_attention", "unknown question(s): " + " | ".join(unknown[:3])

        submit = page.locator("button[aria-label='Submit application']")
        if submit.count():
            submit.first.click()
            time.sleep(3)
            page.keyboard.press("Escape")
            return "applied", ""

        nxt = page.locator(
            "button[aria-label='Continue to next step'], button[aria-label='Review your application']"
        )
        if not nxt.count():
            break
        nxt.first.click()
        time.sleep(2)

    page.keyboard.press("Escape")
    return "failed", "could not complete modal flow"


def run(limit=None):
    from .browser import attach
    from .util import load_config
    cfg = load_config()
    answers = load_answers()
    con = db.connect()

    cap = cfg["apply"]["daily_cap_linkedin"] - db.applied_today(con, "linkedin")
    if cap <= 0:
        print("Daily LinkedIn cap reached. Try tomorrow.")
        return
    rows = con.execute(
        "SELECT * FROM jobs WHERE source='linkedin' AND status='approved' ORDER BY score DESC LIMIT ?",
        (min(cap, limit or cap),),
    ).fetchall()
    if not rows:
        print("No approved LinkedIn jobs in queue. Approve some in the web app first.")
        return

    pw, browser, context = attach(cfg)
    page = context.new_page()
    try:
        for row in rows:
            job = dict(row)
            print(f"→ {job['title']} @ {job['company']}")
            try:
                status, note = apply_one(page, job, cfg, answers)
            except Exception as e:
                if "has been closed" in str(e):
                    print("\n!! Browser window closed — aborting run (job stays approved).")
                    print("   Restart it with: python run.py chrome   then rerun apply.")
                    break
                status, note = "failed", f"error: {e}"
            db.set_status(con, job["id"], status, note)
            if "external application" in (note or ""):
                # discovered at apply time: not actually Easy Apply — fix the badge
                con.execute("UPDATE jobs SET is_easy_apply=0 WHERE id=?", (job["id"],))
                con.commit()
            print(f"   {status} {('— ' + note) if note else ''}")
            human_pause(cfg, "between applications")
    finally:
        page.close()
        pw.stop()
