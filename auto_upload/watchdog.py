"""Pipeline watchdog for the auto_upload repository.

Monitors recent Auto Upload workflow runs and the public Publishing Queue
state, escalates persistent or permanent failures to a GitHub issue labeled
`auto-fix`, and posts a short health report. Runs every 30 minutes from GitHub
Actions using only the public gviz endpoint and the Actions/Issues APIs.

No secrets are required: workflow-run data comes from the Actions API and
queue state comes from the publicly readable Google Sheet via gviz.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

WORKBOOK_ID = "1jjC4oaWsyqLzG6vT5EwJkVAgJCXGpz_7fWr6wb7OU3o"
GVIZ_URL = "https://docs.google.com/spreadsheets/d/%s/gviz/tq" % WORKBOOK_ID
QUEUE_SHEET = "Publishing Queue"
WORKFLOW_FILE = ".github/workflows/auto-upload.yml"
LEDGER_TITLE = "Watchdog Ledger"
AUTO_FIX_LABEL = "auto-fix"
CONSECUTIVE_FAILURE_THRESHOLD = 3
MIN_HEADER_MATCHES = 3
QUEUE_HEADERS = {
    "job_id", "sku", "account_id", "media_selection", "platform", "format",
    "language", "scheduled_at", "timezone", "stock_id_tag", "status",
    "attempts", "last_attempt_at", "platform_post_id", "published_url",
    "error_message", "notes", "tagging_status", "tag_stock_id_used",
    "caption_final",
}
STATUS_HEADER = "status"
WORKFLOW_NAME = "Auto Upload"

API_ROOT = "https://api.github.com/repos"
USER_AGENT = "auto-upload-watchdog"


def repo():
    return os.environ["GITHUB_REPOSITORY"]


def gh_api(token, method, path, body=None):
    url = API_ROOT + "/" + repo() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def get_workflow_runs(token):
    qs = urllib.parse.urlencode({"per_page": 20})
    encoded = urllib.parse.quote(WORKFLOW_FILE, safe="")
    path = "/actions/workflows/%s/runs?%s" % (encoded, qs)
    status, data = gh_api(token, "GET", path)
    if status != 200 or not isinstance(data, dict):
        return False, []
    return True, data.get("workflow_runs", [])


def get_run_log(token, run_id):
    url = API_ROOT + "/" + repo() + "/actions/runs/%s/logs" % run_id
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_error_signature(log_text):
    if not log_text:
        return "unknown (run log unavailable)"
    lines = [line for line in log_text.splitlines() if line.strip()]
    tail = lines[-200:]
    for line in reversed(tail):
        low = line.lower()
        if "error:" in low or "traceback" in low or "exception" in low:
            return re.sub(r"\s+", " ", line.strip())[:220]
    for line in reversed(tail):
        low = line.lower()
        if "error" in low or "failed" in low:
            return re.sub(r"\s+", " ", line.strip())[:220]
    return "unknown failure (no error text in log tail)"


def gviz_table(sheet_name):
    qs = urllib.parse.urlencode({"sheet": sheet_name, "tqx": "out:json"})
    req = urllib.request.Request(GVIZ_URL + "?" + qs)
    req.add_header("User-Agent", "Mozilla/5.0 auto-upload-watchdog")
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {"cols": [], "rows": []}
    return json.loads(text[start:end + 1]).get("table", {})


def queue_status_counts():
    try:
        table = gviz_table(QUEUE_SHEET)
    except Exception as exc:
        return {"error": "gviz fetch failed: %s" % exc}
    cols = [str(c.get("label", "") or "") for c in table.get("cols", [])]
    matrix = [cols]
    for row in table.get("rows", []):
        cells = row.get("c")
        if not cells:
            continue
        matrix.append([
            cell.get("v") if isinstance(cell, dict) else None
            for cell in cells
        ])
    header_idx = None
    for idx, row in enumerate(matrix[:6]):
        norm = {str(v).strip().lower() for v in row if str(v)}
        if len(norm & QUEUE_HEADERS) >= MIN_HEADER_MATCHES:
            header_idx = idx
            break
    if header_idx is None:
        return {"error": "could not locate Publishing Queue header row"}
    headers = [str(h or "").strip().lower() for h in matrix[header_idx]]
    if STATUS_HEADER not in headers:
        return {"error": "status column not found in Publishing Queue"}
    sidx = headers.index(STATUS_HEADER)
    counts = {}
    for row in matrix[header_idx + 1:]:
        if sidx < len(row) and row[sidx]:
            value = str(row[sidx])
            counts[value] = counts.get(value, 0) + 1
    return counts


def list_open_issues(token):
    issues = []
    page = 1
    while True:
        qs = urllib.parse.urlencode({
            "state": "open", "per_page": 100, "page": page,
        })
        status, data = gh_api(token, "GET", "/issues?" + qs)
        if status != 200 or not isinstance(data, list):
            break
        issues.extend(i for i in data if "pull_request" not in i)
        if len(data) < 100:
            break
        page += 1
    return issues


def find_ledger_issue(token):
    for issue in list_open_issues(token):
        if issue.get("title") == LEDGER_TITLE:
            return issue
    return None


def get_or_create_ledger(token):
    issue = find_ledger_issue(token)
    if issue:
        return issue
    status, data = gh_api(token, "POST", "/issues", {
        "title": LEDGER_TITLE,
        "body": "Run IDs already escalated by the Watchdog, one per line.",
    })
    if status not in (200, 201):
        return None
    return data


def update_ledger(token, issue, handled_ids):
    body = "\n".join(sorted(handled_ids))
    gh_api(token, "PATCH", "/issues/%s" % issue["number"], {
        "body": "Run IDs already escalated by the Watchdog, one per line.\n\n%s" % body,
    })


def find_auto_fix_issue(token, signature):
    title = "[auto-fix] %s" % signature[:80]
    for issue in list_open_issues(token):
        if issue.get("title") == title:
            return issue
    return None


def escalate_failure(token, signature, failed_runs):
    issue = find_auto_fix_issue(token, signature)
    if issue:
        ids = ", ".join(str(r["id"]) for r in failed_runs)
        gh_api(token, "POST", "/issues/%s/comments" % issue["number"], {
            "body": "Additional failures with the same signature: run(s) %s" % ids,
        })
        return "commented on existing issue #%s" % issue["number"]
    newest = failed_runs[0]
    title = "[auto-fix] %s" % signature[:80]
    body_lines = [
        "The Watchdog detected repeated Auto Upload failures.",
        "",
        "Latest failing run: [%s](%s)" % (newest["id"], newest.get("html_url", "")),
        "Affected runs: %s" % ", ".join(str(r["id"]) for r in failed_runs),
        "",
        "Error signature:",
        "```",
        signature,
        "```",
        "",
        "The Issue Fixer agent will attempt an automated fix. If it cannot, it",
        "will mark this issue for manual review.",
    ]
    status, data = gh_api(token, "POST", "/issues", {
        "title": title,
        "body": "\n".join(body_lines),
        "labels": [AUTO_FIX_LABEL],
    })
    if status in (200, 201):
        return "created issue #%s" % data["number"]
    return "could not create issue (HTTP %s)" % status


def write_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as handle:
            handle.write(text + "\n")


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("No GITHUB_TOKEN available; watchdog disabled.")
        return

    runs_ok, runs = get_workflow_runs(token)
    failures = [r for r in runs if r.get("conclusion") == "failure"]

    consec = []
    for run in runs:
        if run.get("conclusion") == "failure":
            consec.append(run)
        else:
            break

    lines = ["## Watchdog Report", ""]
    if not runs_ok:
        lines.append(
            "- Could not query workflow runs (Actions API error); "
            "verify the workflow has `actions: read` permission."
        )
        lines.append("- Recent runs: n/a")
    else:
        lines.append("- Recent runs: %d, failures: %d" % (len(runs), len(failures)))
        lines.append("- Consecutive failures at head: %d" % len(consec))

    if len(consec) >= CONSECUTIVE_FAILURE_THRESHOLD:
        newest = consec[0]
        signature = extract_error_signature(get_run_log(token, newest["id"]))
        ledger = get_or_create_ledger(token)
        ledger_ids = set()
        if ledger:
            for line in ledger.get("body", "").splitlines():
                line = line.strip()
                if line.isdigit():
                    ledger_ids.add(line)
        unseen = [r for r in consec if str(r["id"]) not in ledger_ids]
        if unseen:
            result = escalate_failure(token, signature, consec)
            lines.append("- Escalation: %s" % result)
            lines.append("- Signature: `%s`" % signature)
            if ledger:
                ledger_ids.update(str(r["id"]) for r in consec)
                update_ledger(token, ledger, ledger_ids)
        else:
            lines.append("- Escalation: already escalated (ledger up to date)")
    elif len(consec) > 0:
        lines.append(
            "- Below escalation threshold (%d); next cron run retries "
            "automatically." % CONSECUTIVE_FAILURE_THRESHOLD
        )
    else:
        lines.append("- No recent failures; pipeline healthy.")

    counts = queue_status_counts()
    lines.append("")
    lines.append("### Publishing Queue status counts")
    if "error" in counts:
        lines.append("- %s" % counts["error"])
    elif not counts:
        lines.append("- (empty)")
    else:
        for status_name in sorted(counts):
            lines.append("- %s: %d" % (status_name, counts[status_name]))

    report = "\n".join(lines)
    print(report)
    write_summary(report)


if __name__ == "__main__":
    main()
