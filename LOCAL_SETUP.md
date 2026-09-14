# Local setup: Visual Studio desktop on Windows

The scraper now has a normal Python entry point: local_runner.py.
It opens Chromium on your laptop and keeps Google Sheets as the master.
Colab, a GPU, and a paid IDE subscription are not required by this runner.

## 1. Get the code

Install Python 3.12 for Windows with its Python launcher, and Git.
In PowerShell:

~~~powershell
git clone --branch local-windows-runner https://github.com/AbdulRehman6162/Rest_Universe_Scrapper.git
cd Rest_Universe_Scrapper
~~~

For an existing clone, save/commit your own edits before switching branches:

~~~powershell
git fetch origin
git switch --track origin/local-windows-runner
~~~

If the branch already exists locally, use git switch local-windows-runner.
After the PR is merged, master also contains these files.

## 2. Install dependencies

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
~~~

The process-only flag does not change your permanent PowerShell policy.
Setup creates .venv, installs Python packages and Chromium, and copies .env.example to
.env only if .env does not already exist.

If scripts are blocked by your organization, run these equivalent commands manually:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force secrets
~~~

No virtual-environment activation is needed; the commands use its Python directly.

## 3. Configure Visual Studio

In Visual Studio Installer, choose Modify and install the Python development workload.
Open this repository with File > Open > Folder.

After setup, select .venv/Scripts/python.exe as your Python environment.
Use View > Other Windows > Python Environments if you need to add the existing .venv
environment. In Solution Explorer, right-click local_runner.py and select Set as Startup
Item. Complete authentication below and run --check from PowerShell first.

Ctrl+F5 then runs the default small preview. Use the terminal commands below when supplying
custom limits or --write. A solution/project file is not needed for the Open Folder workflow.

Microsoft instructions:
[Python workload](https://learn.microsoft.com/en-us/visualstudio/python/installing-python-support-in-visual-studio)
and [Python Open Folder](https://learn.microsoft.com/en-us/visualstudio/python/quickstart-05-python-visual-studio-open-folder).

VS Code/Cursor also work: install their Python and Python Debugger extensions, select
.venv/Scripts/python.exe, and use the included .vscode/launch.json.
That launch file is for VS Code/Cursor, not Visual Studio desktop.

## 4. Set up Google Sheets authentication

Open .env and replace GOOGLE_SHEET_URL with your complete spreadsheet URL.
The runner selects by URL rather than title.

The existing required tabs remain Restaurant Master and ISB-RWP Target Areas.
Restaurant Master must have its template headers. The target-area tab needs City,
Area/Sector and Priority. An empty master with headers can be populated through SEARCH.

Choose one authentication method:

### Sign in as yourself (default)

1. In a Google Cloud project, enable Google Sheets API and Google Drive API.
2. Configure the OAuth consent screen. If using an External app in Testing, add your
   Google email as a test user.
3. Create an OAuth client of type Desktop app and download its JSON.
4. Save the JSON as secrets/oauth_client.json.
5. Leave GOOGLE_AUTH_METHOD=oauth in .env.

The first --check command opens a browser for Google authorization. Sign in with the
account that can access the master. The token is saved in secrets/authorized_user.json.
If that token is revoked/expired and cannot refresh, delete only the token file and
run --check again to authorize.

### Service account (alternative)

1. Enable Google Sheets API.
2. Create a service account and download its JSON key as secrets/service_account.json.
3. Share the spreadsheet with that file's client_email as an Editor.
4. Set GOOGLE_AUTH_METHOD=service_account.

A project-wide Editor/Owner role is not needed just to edit the shared spreadsheet.
If your organization disables service-account key creation, use OAuth or ask its administrator.

Keep credentials in secrets. That folder, .env, tokens and local output are ignored by Git.
Do not put credentials in Python source.

Reference: [gspread authentication](https://docs.gspread.org/en/latest/oauth2.html).

## 5. Check access

~~~powershell
.\.venv\Scripts\python.exe local_runner.py --check
~~~

This prints the selected sheet, checks its tabs/headers and shows refresh/search inputs.
It does not open Chromium or write restaurant rows.

## 6. Preview five restaurants

~~~powershell
.\.venv\Scripts\python.exe local_runner.py --mode search --query "restaurants in F-6 Islamabad" --max-results 5 --run-id first-local-test
~~~

A visible Chromium window opens. Keep the laptop awake and connected.
This writes only local audit files, not restaurant rows in Sheets.
Inspect output/<sheet-id>/latest_diagnostics.csv and the terminal quality report.

Custom --query searches leave City/Area blank because arbitrary search text is not verified
location data. The target-area plan supplies those discovery hints.

## 7. Sync to Sheets

Repeat the same command with --write:

~~~powershell
.\.venv\Scripts\python.exe local_runner.py --mode search --query "restaurants in F-6 Islamabad" --max-results 5 --run-id first-local-test --write
~~~

Within the cache freshness window it reuses the same observations.
Existing Place IDs update matching rows; new identities receive new Restaurant IDs.
CRM status and analyst notes are preserved.

Use your target-area sheet for two queries:

~~~powershell
.\.venv\Scripts\python.exe local_runner.py --mode search --priority P1 --max-queries 2 --max-results 10 --write
~~~

After a successful small test, remove the refresh/query limits:

~~~powershell
.\.venv\Scripts\python.exe local_runner.py --mode both --max-refresh 0 --max-queries 0 --max-results 18 --write
~~~

0 means all refresh rows/all selected queries. max-results remains a per-query limit;
it is not a guarantee of exhaustive coverage.

## Files, resume and debugging

- Reuse --run-id to resume; change it for fresh collection. Its default is the UTC date.
  Cached observations expire after 24 hours. Failed records are retried.
- Add --headless to hide Chromium after the visible run works.
- Output is separated by spreadsheet ID. It contains checkpoint.json, scrape_log.csv,
  observations_*.json, latest_diagnostics.csv, pre-write snapshots and search_debug files.
- Detailed progress prints to the terminal. run_*.log stores run summaries and exceptions.
- A failed search stops collection before Sheets sync. Earlier successes remain in
  checkpoints; failed detail records are retained for audit and excluded from writes.
- Only run one writer per spreadsheet. A file lock protects the same local output folder;
  it does not lock another device or another output directory.
- Drive is not mounted or uploaded to automatically. To use Google Drive for Desktop,
  set OUTPUT_DIR to an already-synced folder, such as G:/My Drive/RestaurantCollector.
  Use a folder outside the repository for custom synced data.
- The runner reuses definitions from the included notebook through collector_core.py.
  It does not run notebook cells or Colab imports. Keep that notebook in the cloned repo.

## Troubleshooting

| Symptom | Action |
|---|---|
| py not found | Install Python 3.12 with its launcher; reopen the terminal |
| ModuleNotFoundError | Run with .venv/Scripts/python.exe and reinstall requirements |
| Chromium missing | Run .venv/Scripts/python.exe -m playwright install chromium |
| SpreadsheetNotFound/403 | Check URL, enabled API and signed-in account; share with the service-account email if applicable |
| No refresh records | Check the selected master and Google Maps Link column; SEARCH works independently |
| No queries for P1 | Check the target-area Priority and City/Area/Sector cells |
| Zero/blocked search | Open the saved search_debug screenshot/JSON report to see what Google returned |
| OAuth access_denied | Check OAuth test users and organization restrictions |
| Lock error | Stop the other process using the same output folder |

Google blocks and unsupported Maps layouts can still occur locally. This runner does not
bypass access challenges. Share the diagnostic report rather than repeatedly retrying.

## Tests and validation

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

The 47 existing offline regression tests passed in earlier work. Eight local-runner tests
are added for CLI options, isolated loading, configuration, identity and browser cleanup.
The new tests were not executed in this turn because no execution environment was
available. Windows execution and authenticated Sheets integration remain to be verified
with the setup, --check and small preview above.
