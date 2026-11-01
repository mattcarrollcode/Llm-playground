#!/usr/bin/env python3
"""Monitor SCE incident 800588256 via public ArcGIS feeds; write updates to a GitHub issue.

The outage disappearing from a public feed is not proof that power was restored.
Requires Python 3.11+; no third-party packages or stored credentials.
"""
import argparse
import json
import os
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

OUTAGE_ID = 800588256
SCE_URL = "https://services5.arcgis.com/z6hI6KRjKHvhNO0r/ArcGIS/rest/services/Outages_P_VwLayer/FeatureServer/0/query"
CAL_OES_URL = "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/Power_Outages_%28View%29/FeatureServer/0/query"
PACIFIC = ZoneInfo("America/Los_Angeles")
ISSUE_TITLE = f"SCE outage {OUTAGE_ID} status"
STATE_MARKER = "<!-- SCE-OUTAGE-MONITOR-STATE -->"
FIELDS = (
    "ERT", "ERT_LOC", "JobStatus", "CrewStatusCdDesc", "MemoCauseCdDesc",
    "NoOfAffectedCust_Inci", "LastChngDateTime", "OutageStartDateTime",
    "EstimatedRestoreDate", "OutageStatus", "ImpactedCustomers", "Cause", "StartDate",
)

def get_json(url, params=None, token=None, method="GET", body=None):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "sce-outage-monitor/1.0"}
    if token:
        headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=25) as response:
        obj = json.load(response)
    if isinstance(obj, dict) and obj.get("error"):
        raise RuntimeError(str(obj["error"]))
    return obj

def query_outage():
    errors = []
    succeeded = 0
    queries = (
        ("SCE ArcGIS", SCE_URL, f"IncidentId={OUTAGE_ID}"),
        ("California OES ArcGIS", CAL_OES_URL, f"IncidentId='{OUTAGE_ID}'"),
    )
    for source, endpoint, where in queries:
        try:
            result = get_json(endpoint, {
                "f": "json", "where": where,
                "outFields": "*", "returnGeometry": "false",
            })
            succeeded += 1
            features = result.get("features", [])
            if features:
                attrs = features[0].get("attributes", {})
                return {
                    "availability": "found",
                    "source": source,
                    "fields": {k: attrs[k] for k in FIELDS if k in attrs},
                    "raw": attrs,
                    "errors": errors,
                }
        except (OSError, ValueError, RuntimeError, HTTPError, URLError) as exc:
            errors.append(f"{source}: {exc}")
    return {
        "availability": "not_listed" if succeeded else "unavailable",
        "source": None,
        "fields": {},
        "raw": {},
        "errors": errors,
    }

def snapshot(report):
    return {k: report[k] for k in ("availability", "source", "fields")}

def fmt_value(v):
    if isinstance(v, (int, float)) and v > 1_000_000_000_000:
        try:
            return datetime.fromtimestamp(v / 1000, PACIFIC).strftime("%Y-%m-%d %I:%M %p %Z")
        except (OverflowError, ValueError):
            pass
    return str(v)

def diff(previous, current):
    if previous is None:
        return ["Initial observation (not proof of restoration)."]
    changes = []
    if previous.get("availability") != current.get("availability"):
        new_state = current["availability"]
        if new_state == "not_listed":
            new_state += " (NOT confirmed restored)"
        changes.append(f"Availability: {previous.get('availability')} → {new_state}")
    if previous.get("source") != current.get("source"):
        changes.append(f"Source: {previous.get('source')} → {current.get('source')}")
    if current["availability"] == "found" and previous.get("availability") == "found":
        a, b = previous.get("fields", {}), current.get("fields", {})
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                changes.append(f"{key}: {fmt_value(a.get(key))} → {fmt_value(b.get(key))}")
    return changes

def issue_body(state):
    return (
        f"Hourly tracking for SCE incident #{OUTAGE_ID}. This issue stores the latest "
        "observed state, with a timestamped history in comments.\n\n"
        "Subscribe to this issue for GitHub notifications. **An outage disappearing "
        "from a data feed is not proof of service restoration.**\n\n"
        f"{STATE_MARKER}\n```json\n{json.dumps(state, indent=2, sort_keys=True)}\n```\n"
    )

def load_issue_state(body):
    pattern = re.escape(STATE_MARKER) + r"\s*```json\s*(.*?)\s*```"
    m = re.search(pattern, body or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None

def github(method, path, token, data=None):
    return get_json("https://api.github.com" + path, token=token, method=method, body=data)

def publish(report, checked_at):
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    base = f"/repos/{repo}"
    # The repository is dedicated to experimentation. This issue is the state store.
    issues = github("GET", base + "/issues?state=all&per_page=100", token)
    issue = next((i for i in issues if i.get("title") == ISSUE_TITLE and "pull_request" not in i), None)
    state = snapshot(report)
    old = load_issue_state(issue.get("body")) if issue else None
    changes = diff(old, state)

    if issue is None:
        issue = github("POST", base + "/issues", token, {"title": ISSUE_TITLE, "body": issue_body(state)})
    elif issue.get("state") == "closed":
        issue = github("PATCH", base + f"/issues/{issue['number']}", token, {"state": "open"})
    if changes:
        details = "\n".join("- " + line for line in changes)
        note = (
            f"SCE incident **#{OUTAGE_ID}**, checked **{checked_at} Pacific**.\n\n"
            f"{details}\n\n"
            f"Data source: {report['source'] or 'No usable feed'}\n"
        )
        if report["errors"]:
            note += "\nFeed errors: " + "; ".join(report["errors"]) + "\n"
        github("POST", base + f"/issues/{issue['number']}/comments", token, {"body": note})
        github("PATCH", base + f"/issues/{issue['number']}", token, {"body": issue_body(state)})
    print("Issue: " + issue.get("html_url", ""))
    print("Changes: " + json.dumps(changes, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github", action="store_true", help="Write changed status to GitHub issue")
    args = parser.parse_args()
    checked_at = datetime.now(PACIFIC).strftime("%Y-%m-%d %I:%M:%S %p %Z")
    report = query_outage()
    print(json.dumps({"checked_at_pacific": checked_at, "incident": OUTAGE_ID,
                      "snapshot": snapshot(report), "errors": report["errors"]},
                     ensure_ascii=False, indent=2))
    if args.github:
        publish(report, checked_at)
    if report["availability"] == "unavailable":
        raise SystemExit("Both outage feeds failed; current incident status could not be verified")

if __name__ == "__main__":
    main()
