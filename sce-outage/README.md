# SCE address-based outage monitor

Checks public SCE and California OES outage **point** data near a confidential service address. Does not search by a fixed outage number. The GitHub workflow is scheduled every 10 minutes at minutes 07, 17, 27, 37, 47 and 57 UTC (GitHub schedules can be delayed).

## Required one-time setup

1. Go to [repository Actions secrets](https://github.com/mattcarrollcode/Llm-playground/settings/secrets/actions).
2. Select **New repository secret**.
3. Name it exactly **SCE_SERVICE_ADDRESS**.
4. Set the value to the complete SCE service street address, including city, state and ZIP.
5. Save it. The next hourly job will use it; you can also start an immediate run under [Actions → SCE outage](https://github.com/mattcarrollcode/Llm-playground/actions/workflows/sce-outage.yml) → **Run workflow**.

The monitored street address is never included in this public repository's source code or GitHub issue comments. It is sent to a geocoding provider and transformed into location coordinates in the ephemeral GitHub Actions runner. Those coordinates are not published to the issue.

Do **not** enter the street address as a GitHub issue comment, workflow input, or public repository variable.

## What it can and cannot tell you

- SCE's open ArcGIS feature service provides **incident points**, not verified maps showing every affected address.
- The script geocodes the confidential street address (Census Geocoder, with Esri fallback) and queries nearby outage points within a **3-mile radius**, filtering SCE entries explicitly classified as scheduled maintenance.
- Nearby unscheduled repair events' incident IDs, status, cause, customer count and restoration estimates are recorded in [status issue #1](https://github.com/mattcarrollcode/Llm-playground/issues/1).
- The issue only gets a new comment when tracked incident data changes. Subscribe to the issue and enable GitHub notifications.
- **A nearby outage is not proof your address is impacted.** No nearby outage, or an incident disappearing, is **not proof power was restored at the house**. Exact service-address outage matching requires SCE's own [interactive address search](https://www.sce.com/outages-safety/outage-center/check-outage-status) or a verified address-level API.

If geocoding fails or neither feed is accessible, the job fails rather than publishing an incorrect "all clear."

## Usage

Run with GitHub Actions. No external Python packages or additional API tokens are required; posting changes to the GitHub issue uses the built-in GITHUB_TOKEN.

To run locally:

\`\`\`shell
SCE_SERVICE_ADDRESS="(your full address)" python3 sce-outage/monitor.py
\`\`\`

GitHub notification delivery may be delayed or filtered by your GitHub mobile/app settings. Disable the workflow when monitoring is no longer needed.
