"""Run the project delivery job from an AutoDL container without systemd."""

import argparse
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from time import sleep

from .schedule import BEIJING, MORNING_TIME, next_slot

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def next_run(now: datetime, first_date: date | None = None) -> datetime:
    target = next_slot(now)
    if first_date is not None:
        target = max(target, datetime.combine(first_date, MORNING_TIME, tzinfo=BEIJING))
    return target


def _run_cli(*arguments: str) -> int:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -a; source ./.env || exit 1; set +a; exec "$@"',
            "commute-digest-scheduler",
            sys.executable,
            "-m",
            "commute_digest",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-date", type=date.fromisoformat)
    first_date = parser.parse_args().first_date
    target = next_run(datetime.now(BEIJING), first_date)
    feed_exit_code = _run_cli("widget-feed")
    print(f"widget_feed_exit_code={feed_exit_code}", flush=True)

    while True:
        print(f"next_run={target.isoformat()}", flush=True)
        while (remaining := (target - datetime.now(BEIJING)).total_seconds()) > 0:
            sleep(min(remaining, 60))
        started = datetime.now(BEIJING)
        if started >= next_slot(target):
            print(f"missed_slot={target.isoformat()}", flush=True)
            target = next_run(started)
            continue

        print(f"delivery_started={started.isoformat(timespec='seconds')}", flush=True)
        exit_code = _run_cli("deliver", "--slot", target.isoformat())
        print(f"delivery_exit_code={exit_code}", flush=True)
        target = next_run(max(target, datetime.now(BEIJING)))


if __name__ == "__main__":
    main()
