# SCE power outage monitor — incident 800588256

Uses two public ArcGIS outage feeds to check incident `800588256`, without scraping SCE's interactive website.

- **Primary:** public SCE incident layer, keyed by `IncidentId`.
- **Fallback:** California OES Power Outage Incidents view.
- **Schedule:** hourly at minute 17 UTC via `.github/workflows/sce-outage.yml`; GitHub may delay starts.
- **History:** GitHub issue named `SCE outage 800588256 status`. Each detected change is posted in a timestamped comment using Pacific time. The issue body stores the last snapshot.
- **Tracking:** estimated restoration, repair and crew status, cause, customer count, and incident timestamps.
- **Safety:** disappearance of an incident from a feed does **not** establish that power is restored. Only physical confirmation or utility confirmation can do that.
- **Limits:** upstream data may lag or not include the incident. The first execution may report "not listed". Errors and no-record results are clearly identified.

## Test it

1. Open [the workflow in GitHub Actions](../../actions/workflows/sce-outage.yml).
2. Select **Run workflow** to execute it once now. (ChatGPT GitHub tools do not currently provide a workflow-dispatch action.)
3. Open the run log to verify API connectivity and that the job completes. A successful job can still report "not listed", which is not restoration proof.
4. Find the created [status issue](../../issues?q=is%3Aissue+SCE+outage+800588256) and click **Subscribe** so GitHub can notify you of future comments.
5. Enable GitHub mobile push notifications for subscribed issue activity if desired.

The workflow requests `issues: write` for its built-in `GITHUB_TOKEN`. If the run gets a 403 on issues, check repository **Settings → Actions → General → Workflow permissions** and ensure writing to issues is permitted.

## Run locally

```shell
python3 sce-outage/monitor.py
```

No additional Python packages are needed, and no secret tokens are required in repository settings. The GitHub workflow uses the built-in `GITHUB_TOKEN`.

## Stopping monitoring

Delete or disable the workflow from the **Actions** tab after the outage is over. The schedule does not stop automatically.

This monitor does not independently verify household electrical service or guarantee GitHub notification delivery.
