"""Prevent Windows idle sleep while a long backtest is running.

Uses SetThreadExecutionState (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) — the flag
is tied to this process and clears automatically when it exits, so no
permanent power-settings change is made.
"""
import ctypes
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

prev = ctypes.windll.kernel32.SetThreadExecutionState(
    ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
print(f"keep-awake active (prev state {prev:#x}); machine will not idle-sleep "
      "while this process runs", flush=True)
while True:
    # re-assert periodically in case something else clears it
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    time.sleep(60)
