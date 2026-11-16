"""Naukri apply executor.

Clicks the on-site Apply button; if Naukri's chatbot questionnaire pops up,
answers from the answer bank. Unknown question -> needs_attention, no submit.
Jobs with 'Apply on company site' are flagged for manual handling.
"""
import time

from . import db
from .util import load_answers, find_answer, human_pause, load_config


def _handle_chatbot(page, answers, unknown):
    """Naukri chatbot ('_chatBotContainer') asks questions one at a time."""
    for _ in range(12):
        time.sleep(2)
        bot = page.locator("div._chatBotContainer, div.chatbot_Drawer")
        if not bot.count():
            return True  # no (more) questions
        questions = bot.locator("div.botMsg, li.botItem span").all_inner_texts()
        if not questions:
            return True
        q = questions[-1].strip()
        entry = find_answer(answers, q)
        if not entry:
            unknown.append(q)
            return False

        # radio-style options?
        options = bot.locator("div.ssrc__radio-btn-container label, div.singleselect-radiobutton label")
        if options.count():
            target = "yes" if (entry["type"] == "bool" and entry["value"]) else \
                     "no" if entry["type"] == "bool" else str(entry["value"]).lower()
            clicked = False
            for lab in options.all():
                if target in lab.inner_text().lower():
                    lab.click()
                    clicked = True
                    break
            if not clicked:
                unknown.append(q)
                return False
        else:
            box = bot.locator("div.textArea[contenteditable], input.chatbot_InputBox, textarea")
            if not box.count():
                unknown.append(q + " (no input box found)")
                return False
            box.first.fill(str(entry["value"]))
        send = bot.locator("div.sendMsg, button:has-text('Save')")
        if send.count():
            send.first.click()
        time.sleep(1.5)
    return True


def apply_one(page, job, answers):
    page.goto(job["url"], timeout=60000)
    time.sleep(4)

    if page.locator("#already-applied, span:has-text('Applied')").count():
        return "applied", "was already applied"

    company_site = page.locator("button#company-site-button, button:has-text('Apply on company site')")
    if company_site.count():
        return "needs_attention", "external application on company site — apply manually"

    apply_btn = page.locator("button#apply-button, button:has-text('Apply')").first
    try:
        apply_btn.click(timeout=8000)
    except Exception:
        return "skipped", "no apply button (job closed or layout changed)"

    unknown = []
    ok = _handle_chatbot(page, answers, unknown)
    if not ok:
        return "needs_attention", "unknown chatbot question(s): " + " | ".join(unknown[:3])

    time.sleep(2)
    if page.locator("span:has-text('successfully applied'), #already-applied, span:has-text('Applied')").count():
        return "applied", ""
    return "applied", "clicked apply; no explicit confirmation banner seen — verify in Naukri"


def run(limit=None):
    from .browser import attach
    cfg = load_config()
    answers = load_answers()
    con = db.connect()

    cap = cfg["apply"]["daily_cap_naukri"] - db.applied_today(con, "naukri")
    if cap <= 0:
        print("Daily Naukri cap reached. Try tomorrow.")
        return
    rows = con.execute(
        "SELECT * FROM jobs WHERE source='naukri' AND status='approved' ORDER BY score DESC LIMIT ?",
        (min(cap, limit or cap),),
    ).fetchall()
    if not rows:
        print("No approved Naukri jobs in queue. Approve some in the web app first.")
        return

    pw, browser, context = attach(cfg)
    page = context.new_page()
    try:
        for row in rows:
            job = dict(row)
            print(f"→ {job['title']} @ {job['company']}")
            try:
                status, note = apply_one(page, job, answers)
            except Exception as e:
                if "has been closed" in str(e):
                    print("\n!! Browser window closed — aborting run (job stays approved).")
                    print("   Restart it with: python run.py chrome   then rerun apply.")
                    break
                status, note = "failed", f"error: {e}"
            db.set_status(con, job["id"], status, note)
            print(f"   {status} {('— ' + note) if note else ''}")
            human_pause(cfg, "between applications")
    finally:
        page.close()
        pw.stop()
