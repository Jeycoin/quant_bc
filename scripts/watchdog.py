"""Watchdog for the live agent loop.

Independent of the loop itself: checks analytics.db for decision-event
freshness. If the loop is alive-but-hung (no PAPER decision for longer
than MAX_SILENCE_MIN), it kills the loop process and restarts it, and
appends an incident line to data/watchdog.log.

The 2026-10-12 incident — a proxy switch left a network call hanging
for 17h with the process alive — showed retry logic alone cannot be
trusted; freshness must be verified from OUTSIDE the process.
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAX_SILENCE_MIN = float(os.getenv("WATCHDOG_SILENCE_MIN", "45"))
CHECK_EVERY_SEC = 300
LOOP_CMD = [sys.executable, str(REPO / "scripts" / "agent_loop.py"),
            "--interval-min", "30", "--review-every", "6"]
LOG = REPO / "data" / "watchdog.log"
PIDFILE = REPO / "data" / "agent_loop.pid"


def log(msg: str) -> None:
    line = f"{time.strftime('%F %T')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def last_decision_age_min() -> float | None:
    try:
        con = sqlite3.connect(str(REPO / "data" / "analytics.db"))
        row = con.execute(
            "SELECT MAX(ts) FROM decision_events WHERE mode='PAPER'").fetchone()
        con.close()
        if not row or not row[0]:
            return None
        return (time.time() - row[0]) / 60
    except Exception:
        return None


def child_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_loop() -> subprocess.Popen:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = open(REPO / "data" / "agent_loop.log", "ab", buffering=0)
    proc = subprocess.Popen(LOOP_CMD, cwd=str(REPO), env=env,
                            stdout=out, stderr=subprocess.STDOUT)
    PIDFILE.write_text(str(proc.pid))
    return proc


def main() -> None:
    # grace period on startup: the first cycle needs a few minutes
    proc = start_loop()
    log(f"watchdog started; agent_loop pid={proc.pid}, "
        f"silence limit {MAX_SILENCE_MIN}min")
    time.sleep(600)
    while True:
        alive = proc.poll() is None
        age = last_decision_age_min()
        if not alive:
            log(f"INCIDENT: agent_loop exited (code={proc.returncode}); restarting")
            proc = start_loop()
            log(f"restarted agent_loop pid={proc.pid}")
            time.sleep(600)
            continue
        if age is not None and age > MAX_SILENCE_MIN:
            log(f"INCIDENT: no PAPER decision for {age:.0f}min "
                f"(limit {MAX_SILENCE_MIN}); killing hung loop pid={proc.pid}")
            try:
                proc.terminate()
                proc.wait(timeout=30)
            except Exception:
                proc.kill()
            proc = start_loop()
            log(f"restarted agent_loop pid={proc.pid}")
            time.sleep(600)
            continue
        time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    main()
