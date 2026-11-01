#!/usr/bin/env python3
"""SCE monitor: geocode private service address; find nearby public outage incidents.

The public SCE ArcGIS layer provides INCIDENT POINTS, not a coverage-by-address map.
A nearby incident does not prove the address is affected; no incident does not
prove electricity has been restored. Never print or publish the street address.
"""
import json
import math
import os
import re
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
SCE = "https://services5.arcgis.com/z6hI6KRjKHvhNO0r/arcgis/rest/services/Outages_P_VwLayer/FeatureServer/0/query"
CAL_OES = "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/Power_Outages_(View)/FeatureServer/0/query"
CENSUS = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
ESRI = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
ISSUE_TITLE = "SCE nearby outages (address-based)"
LEGACY_TITLE = "SCE outage 800588256 status"
STATE_START = "<!-- SCE_NEARBY_STATE_BEGIN -->"
STATE_END = "<!-- SCE_NEARBY_STATE_END -->"
FIELDS = (
    "IncidentId", "IncidentType", "JobStatus", "CrewStatusCdDesc",
    "MemoCauseCdDesc", "NoOfAffectedCust_Inci", "ERT", "ERT_LOC",
    "OutageStartDateTime", "LastChngDateTime", "ResultCdDesc",
    "OutageStatus", "Cause", "ImpactedCustomers", "StartDate",
    "EstimatedRestoreDate", "OutageType",
)

def api(url, params=None, token=None, method="GET", payload=None):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "SCE-nearby-monitor/1.0"}
    if token:
        headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, headers=headers, data=data, method=method), timeout=25) as response:
        result = json.load(response)
    if isinstance(result, dict) and result.get("error"):
        raise ValueError("API error: " + str(result["error"]))
    return result

def validate_match(address, matched):
    number = re.match(r"^\s*(\d+)", address)
    if not number or not re.search(r"\b" + re.escape(number.group(1)) + r"\b", matched):
        raise ValueError("Geocoder returned a non-address-level match; refusing to guess coordinates")
    postal = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
    if postal and postal.group(1) not in matched:
        raise ValueError("Geocoder returned a different ZIP; refusing to guess coordinates")

def geocode(address):
    errors = []
    try:
        response = api(CENSUS, {
            "address": address, "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "format": "json",
        })
        matches = response.get("result", {}).get("addressMatches", [])
        if matches:
            match = matches[0]
            validate_match(address, match["matchedAddress"])
            point = match["coordinates"]
            return float(point["y"]), float(point["x"])
        errors.append("Census: no address-level match")
    except Exception as exc:
        errors.append("Census geocoder: " + type(exc).__name__)
    try:
        response = api(ESRI, {
            "SingleLine": address, "f": "json", "maxLocations": 1,
            "outFields": "Addr_type,Match_addr,Postal",
        })
        matches = response.get("candidates", [])
        if matches:
            match = matches[0]
            if float(match.get("score", 0)) < 90:
                raise ValueError("low-confidence match")
            if match.get("attributes", {}).get("Addr_type") not in (
                "PointAddress", "StreetAddress", "Subaddress", "StreetInt"
            ):
                raise ValueError("not an address-level match")
            validate_match(address, match.get("address", ""))
            return float(match["location"]["y"]), float(match["location"]["x"])
        errors.append("Esri: no address-level match")
    except Exception as exc:
        errors.append("Esri geocoder: " + type(exc).__name__)
    raise RuntimeError("Could not geocode service address reliably: " + "; ".join(errors))

def distance_miles(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(min(1, math.sqrt(h)))

def query_layer(url, lat, lon, radius):
    dy = radius / 69.0
    dx = radius / (69.0 * max(0.01, math.cos(math.radians(lat))))
    envelope = ",".join(f"{value:.7f}" for value in (
        lon - dx, lat - dy, lon + dx, lat + dy
    ))
    response = api(url, {
        "f": "json", "where": "1=1",
        "geometry": envelope, "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects", "inSR": 4326,
        "outSR": 4326, "outFields": "*", "returnGeometry": "true",
        "resultRecordCount": 2000,
    })
    if response.get("exceededTransferLimit"):
        raise RuntimeError("Outage layer returned truncated data")
    features = response.get("features")
    if features is None:
        raise ValueError("No features array in ArcGIS response")
    matches = {}
    for f in features:
        geometry = f.get("geometry") or {}
        if "x" not in geometry or "y" not in geometry:
            continue
        d = distance_miles(lat, lon, float(geometry["y"]), float(geometry["x"]))
        if d > radius:
            continue
        a = f.get("attributes") or {}
        # Scheduled maintenance entries should not be treated as live repair outages.
        # SCE marks them as planned ('PO') and scheduled ('S'). 
        if str(a.get("IncidentType", "")).upper() == "PO" and str(a.get("OutageType", "")).upper() == "S":
            continue
        incident = a.get("IncidentId")
        if not incident:
            continue
        # SCE results are outage events; California's feed includes other utilities.
        if url == CAL_OES and not any(name in str(a.get("UtilityCompany", "")).lower() for name in ("southern california edison", "sce")):
            continue
        key = str(incident)
        details = {field: a[field] for field in FIELDS if a.get(field) is not None}
        details["point_distance_miles"] = round(d, 1)
        matches[key] = details
    return matches

def current_snapshot(address, radius):
    lat, lon = geocode(address)
    errors = []
    for name, url in (("SCE public ArcGIS", SCE), ("California OES public ArcGIS", CAL_OES)):
        try:
            matches = query_layer(url, lat, lon, radius)
            return {"method": "nearby_points_not_address_coverage",
                    "source": name, "radius_miles": radius,
                    "outages": matches, "errors": errors}
        except Exception as exc:
            errors.append(name + ": " + type(exc).__name__ + " (" + str(exc)[:150] + ")")
    raise RuntimeError("Both outage data sources failed: " + "; ".join(errors))

def public_snapshot(snapshot):
    return {k: snapshot[k] for k in ("method", "source", "radius_miles", "outages")}

def fmt(value):
    if isinstance(value, (int, float)) and value > 1e12:
        try:
            return datetime.fromtimestamp(value / 1000, PACIFIC).strftime("%Y-%m-%d %I:%M %p %Z")
        except (ValueError, OverflowError):
            pass
    return str(value)

def changes(previous, current):
    if not previous or previous.get("method") != current["method"]:
        return ["Address-based monitoring initialized. Outage points indicate proximity, not confirmed service impact."]
    # Ignore planned maintenance from the previous snapshot during migration.
    old = {k: v for k, v in previous.get("outages", {}).items()
           if not (str(v.get("IncidentType", "")).upper() == "PO"
                   and str(v.get("OutageType", "")).upper() == "S")}
    new = current["outages"]
    result = []
    for key in sorted(set(new) - set(old)):
        result.append(f"Nearby incident {key} appeared ({new[key].get('point_distance_miles')} miles from address).")
    for key in sorted(set(old) - set(new)):
        result.append(f"Nearby incident {key} is no longer listed; restoration at the property is NOT confirmed.")
    for key in sorted(set(old) & set(new)):
        a, b = old[key], new[key]
        for field in sorted(set(a) | set(b)):
            if a.get(field) != b.get(field):
                result.append(f"Incident {key}, {field}: {fmt(a.get(field))} → {fmt(b.get(field))}")
    if previous.get("source") != current["source"]:
        result.append(f"Source changed: {previous.get('source')} → {current['source']}")
    return result

def body(state):
    return (
        "**This monitor looks up SCE outages near a privately configured service address.** "
        "Public incident points do not identify exactly which homes are affected. "
        "**No nearby outage** and **outage no longer listed** do not prove electrical service is restored.\n\n"
        "Subscribe to this issue to receive notifications about detected changes. "
        "The precise address and geocoded coordinates are not logged here.\n\n"
        f"{STATE_START}\n"
        + json.dumps(state, sort_keys=True, indent=2) + "\n"
        + f"{STATE_END}\n"
    )

def old_state(content):
    if STATE_START in (content or "") and STATE_END in (content or ""):
        try:
            return json.loads(content.split(STATE_START, 1)[1].split(STATE_END, 1)[0].strip())
        except ValueError:
            pass
    return None

def gh(path, method="GET", payload=None):
    return api("https://api.github.com" + path,
               token=os.environ["GITHUB_TOKEN"], method=method, payload=payload)

def update_issue(snapshot, checked_at):
    repo = os.environ["GITHUB_REPOSITORY"]
    root = f"/repos/{repo}"
    issue_list = gh(root + "/issues?state=all&per_page=100")
    issue = next((i for i in issue_list if i.get("title") in (ISSUE_TITLE, LEGACY_TITLE)
                  and "pull_request" not in i), None)
    state = public_snapshot(snapshot)
    previous = old_state(issue.get("body")) if issue else None
    updates = changes(previous, state)
    if not issue:
        issue = gh(root + "/issues", "POST", {"title": ISSUE_TITLE, "body": body(state)})
    else:
        # Preserve the issue number and subscriptions from the previous monitor.
        new_issue = {"title": ISSUE_TITLE, "body": body(state)}
        if issue.get("state") == "closed":
            new_issue["state"] = "open"
        if updates or issue.get("title") != ISSUE_TITLE:
            issue = gh(root + f"/issues/{issue['number']}", "PATCH", new_issue)
    if updates:
        details = "\n".join("- " + line for line in updates)
        note = (f"**Checked:** {checked_at} (Pacific)\n\n{details}\n\n"
                f"Source: {snapshot['source']}; search radius: {snapshot['radius_miles']} miles.\n")
        gh(root + f"/issues/{issue['number']}/comments", "POST", {"body": note})
    print("Issue:", issue.get("html_url", ""))
    print("Changes:", json.dumps(updates, ensure_ascii=False))

def main():
    address = os.environ.get("SCE_SERVICE_ADDRESS", "").strip()
    if not address:
        raise SystemExit(
            "SETUP REQUIRED: Add SCE_SERVICE_ADDRESS in GitHub repo Settings → "
            "Secrets and variables → Actions → New repository secret. "
            "Do NOT commit the street address to the public repository."
        )
    try:
        radius = float(os.environ.get("SCE_SEARCH_RADIUS_MILES", "3"))
        if not (0.5 <= radius <= 15):
            raise ValueError("radius out of range")
        result = current_snapshot(address, radius)
    except Exception as exc:
        raise SystemExit("Outage check failed: " + str(exc)) from exc
    checked = datetime.now(PACIFIC).strftime("%Y-%m-%d %I:%M:%S %p %Z")
    print(json.dumps({"checked_at": checked, "source": result["source"],
                      "possible_nearby_incidents": len(result["outages"]),
                      "incident_ids": sorted(result["outages"].keys())}, indent=2))
    if os.environ.get("GITHUB_TOKEN"):
        update_issue(result, checked)
    else:
        print(json.dumps(public_snapshot(result), indent=2))

if __name__ == "__main__":
    main()
