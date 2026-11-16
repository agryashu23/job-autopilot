"""Job Autopilot CLI.

  python run.py discover      fetch + filter + score + queue new jobs (logged in the web app)
  python run.py match         re-run the local AI match over un-scored jobs
  python run.py web           start the web app (http://localhost:5580)
  python run.py schedule      register a daily discovery run in Windows Task Scheduler
  python run.py unschedule    remove it
  python run.py stats         pipeline counts + last runs
  python run.py test          run the unit tests
  python run.py reset         drop un-reviewed jobs (new/queued/expired) so the next
                              discover re-fetches under current filters; history is kept

  Opt-in, not part of the daily flow:
  python run.py chrome        launch Chrome with a debug port (Naukri discovery, bots)
  python run.py apply --yes   Playwright auto-apply on LinkedIn/Naukri (ToS risk)
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TASK_NAME = "JobAutopilotDiscover"


def _ensure_venv():
    """Re-launch inside the project venv if invoked with a different Python."""
    venv_py = HERE / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        raise SystemExit(subprocess.call([str(venv_py)] + sys.argv))


def launch_chrome():
    from autopilot.util import load_config
    cfg = load_config()
    port = cfg["apply"]["chrome_debug_port"]
    profile_dir = HERE / "chrome-profile"
    profile_dir.mkdir(exist_ok=True)
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    exe = next((c for c in candidates if Path(c).exists()), None)
    if not exe:
        print("Chrome/Edge not found — edit run.py candidates list.")
        return
    subprocess.Popen([exe, f"--remote-debugging-port={port}",
                      f"--user-data-dir={profile_dir}"])
    print(f"Browser launched with debug port {port} (dedicated profile: {profile_dir}).")
    print("FIRST TIME: log in to naukri.com (and linkedin.com) in this window, then keep it open.")


def stats():
    import datetime
    from autopilot import db
    con = db.connect()
    print("Jobs by status:")
    for row in con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status ORDER BY c DESC"):
        print(f"  {row['status']:>16}: {row['c']}")
    runs = db.recent_runs(con, 5)
    if runs:
        print("Recent runs:")
        for r in runs:
            when = datetime.datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M")
            state = "running" if r["finished_at"] is None else ("ok" if r["ok"] else "FAILED")
            print(f"  {when}  {r['kind']:<9} {state:<8} {r['summary']}")


def schedule(remove=False):
    """Daily discovery via Windows Task Scheduler. Uses a .cmd wrapper so the
    task command needs no quoting; output goes to logs/scheduled.log and every
    run is also stored in the database for the web app."""
    if remove:
        subprocess.call(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        return
    from autopilot.util import load_config
    when = (load_config().get("schedule") or {}).get("time", "07:30")
    (HERE / "logs").mkdir(exist_ok=True)
    cmd_file = HERE / "discover.cmd"
    cmd_file.write_text(
        "@echo off\r\n"
        "set PYTHONUTF8=1\r\n"
        f"cd /d \"{HERE}\"\r\n"
        "echo ==== %date% %time% ==== >> logs\\scheduled.log\r\n"
        ".venv\\Scripts\\python.exe run.py discover >> logs\\scheduled.log 2>&1\r\n",
        encoding="ascii")
    rc = subprocess.call(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", when,
                          "/TN", TASK_NAME, "/TR", str(cmd_file)])
    if rc == 0:
        print(f"Scheduled: '{TASK_NAME}' runs {cmd_file.name} daily at {when} "
              f"(only while the PC is on). Remove with: python run.py unschedule")
    else:
        print("schtasks failed; run this from an elevated prompt or create the task manually.")


def main():
    _ensure_venv()
    # Windows consoles and redirected output default to cp1252; the pipeline
    # prints en dashes, arrows and job titles in any script.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "discover":
        from autopilot.discover import run_discovery
        run_discovery()
    elif cmd == "match":
        from autopilot.match import run_match
        run_match()
    elif cmd == "web":
        import uvicorn
        print("Web app: http://localhost:5580")
        uvicorn.run("server.main:app", port=5580, log_level="warning")
    elif cmd == "schedule":
        schedule()
    elif cmd == "unschedule":
        schedule(remove=True)
    elif cmd == "stats":
        stats()
    elif cmd == "test":
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"], cwd=HERE))
    elif cmd == "reset":
        from autopilot import db
        con = db.connect()
        n = con.execute("DELETE FROM jobs WHERE status IN ('new','queued','expired')").rowcount
        con.commit()
        print(f"Removed {n} un-reviewed/expired jobs. Run: python run.py discover")
    elif cmd == "chrome":
        launch_chrome()
    elif cmd == "apply":
        if "--yes" not in sys.argv:
            print("Auto-apply drives your real LinkedIn/Naukri session and violates their ToS.\n"
                  "It is opt-in: python run.py apply --yes   (needs: python run.py chrome)")
            return
        from autopilot import apply_linkedin, apply_naukri
        print("== LinkedIn Easy Apply ==")
        apply_linkedin.run()
        print("\n== Naukri ==")
        apply_naukri.run()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
