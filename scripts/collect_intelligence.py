"""Intelligence collection loop.

Usage:
    python scripts/collect_intelligence.py              # one round
    python scripts/collect_intelligence.py --loop 15    # every 15 minutes

Runs on the analytics path only — it never touches trading components.
History accumulates in data/intelligence.db so future replays can
reconstruct what external information was available at time T.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from intelligence.collector import collect_once
from intelligence.store import IntelligenceStore

SYMBOLS = ["BTC", "ETH"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Intelligence collector")
    parser.add_argument("--loop", type=int, default=0,
                        help="loop interval in minutes (0 = single round)")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    store = IntelligenceStore(str(REPO_ROOT / "data" / "intelligence.db"))
    try:
        while True:
            meta = await collect_once(store, SYMBOLS)
            stamp = time.strftime("%m-%d %H:%M")
            summary = " ".join(
                f"{k}:{v['status']}" for k, v in meta["providers"].items())
            print(f"[{stamp}] {summary}", flush=True)
            if args.loop <= 0:
                break
            await asyncio.sleep(args.loop * 60)
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
