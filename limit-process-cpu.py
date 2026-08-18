#!/usr/bin/env python3
"""Apply a simple duty-cycle CPU limit to one process and all its threads."""

import os
import signal
import sys
import time


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} PID PERCENT", file=sys.stderr)
        return 2

    pid = int(sys.argv[1])
    percent = max(1, min(95, int(sys.argv[2])))
    period = 0.1
    running = period * percent / 100.0
    stopped = period - running

    try:
        while True:
            os.kill(pid, signal.SIGCONT)
            time.sleep(running)
            os.kill(pid, signal.SIGSTOP)
            time.sleep(stopped)
    except ProcessLookupError:
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
