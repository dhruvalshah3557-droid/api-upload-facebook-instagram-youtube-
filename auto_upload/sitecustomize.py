"""Quota-safe scheduler throttle for GitHub Actions.

The workflow wakes every 2 minutes, but scheduled runs only do real Python work
on every fifth run. That gives an effective cadence of about 10 minutes while
manual/workflow_dispatch runs remain immediate.
"""

import os


def _should_skip_scheduled_run():
    if os.getenv("GITHUB_EVENT_NAME", "").strip().lower() != "schedule":
        return False
    try:
        run_number = int(os.getenv("GITHUB_RUN_NUMBER", "0") or 0)
    except ValueError:
        return False
    return bool(run_number and run_number % 5 != 0)


if _should_skip_scheduled_run():
    run_number = os.getenv("GITHUB_RUN_NUMBER", "?")
    print(
        f"Quota throttle: scheduled run #{run_number} skipped; "
        "real upload work runs about every 10 minutes."
    )
    raise SystemExit(0)
