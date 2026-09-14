# Run locally in Visual Studio (Windows)

See [LOCAL_SETUP.md](LOCAL_SETUP.md) for installation, Visual Studio Python support, Google authentication and PowerShell run commands. `local_runner.py` opens visible Chromium, previews locally, and syncs to Sheets with `--write`. Cursor/VS Code are also supported. The included notebook has the V2.3.3 search diagnostics from PR #3.

# Restaurant Universe Collector

Google Maps collection for the Islamabad–Rawalpindi restaurant intelligence project.
The existing Colab notebook has been hardened to **V2.3.3**; its filename is retained so existing links continue to work.

[Open notebook in Google Colab](https://colab.research.google.com/github/AbdulRehman6162/Rest_Universe_Scrapper/blob/master/Profiling_GoogleSheets_Drive_V2_3_1_Fixed.ipynb)

## Run in Colab

1. Open the notebook and run the dependency, import/Drive mount, and Sheets authentication cells.
2. Set `GOOGLE_SHEET_NAME` in Cell 3 to your spreadsheet. It must contain `Restaurant Master` and `ISB-RWP Target Areas`, with the required headers shown in Cell 5.
3. Set `OUTPUT_DIR` and the run limits in Cell 4. The default smoke test refreshes 10 records and runs 2 searches with up to 10 results each.
4. Run through the diagnostics table. Check names, Place IDs, reviews, hours, and errors before running Cell 24, the Google Sheets writer.
5. Run the final verification cell. Confirm unique Restaurant IDs, Place IDs, and Maps URLs and inspect newly created rows.

Run **one collector at a time per master**. Sheets offers no transactional uniqueness lock here. A before-write JSON snapshot is retained on Drive for recovery, but is not an automatic rollback of formatting/formulas.

Use Google Sheets as the system of record. No Excel upload is needed. Drive stores checkpoint JSON, append-only scrape logs, timestamped raw results (including failures), and before-write snapshots. Failed extractions do not create or update restaurant rows.

## Corrections against the project context

| Requirement | Current behavior |
|---|---|
| One extractor for REFRESH and SEARCH | Both call `extract_place_details()` |
| Reliable review counts | Structured listing summary controls; no generic body-text fallback |
| Branch-safe identity | A known Place ID matches only that ID; unmatched IDs create new rows |
| Fallback identity | URL, phone, then name/address only without Place ID; ambiguous keys cannot pick an arbitrary branch |
| URL identity | Retains `cid` and Place ID query parameters; removes only known tracking parameters |
| Internal IDs | Preserves IDs; allocates missing/new IDs above the maximum, including repeated records within one write |
| Existing duplicate IDs | Stops before writing; manual identity resolution is required instead of renumbering CRM records |
| Coordinates | Uses place-pin `!3d/!4d`, never the map viewport center |
| Clean hours | Full time ranges, split shifts, Closed, or Open 24 hours; rejects partial times/UI fragments |
| Legacy dirty hours | Clears malformed schedules on refreshed rows; preserves old validated schedules if new hours are unavailable |
| Services | Explicit positive labels only; `Y` means detected, blank means unknown; negations/prose do not become Yes |
| Three separate statuses | Preserves CRM Status; writes Current Open Status and Business Status separately |
| Manual information | Preserves Notes, cuisines, contacts, and existing analyst classifications/location |
| Freshness | Writes actual `Last Observed At`; preserves initial `Date Profiled` |
| Enrichment separation | Does not scan page links for social/Foodpanda data; existing enrichment remains intact |
| Google Sheets grid | Expands rows/columns when required and writes scraped values with RAW semantics |
| Dashboard | Optional (`REPAIR_DASHBOARD=False`); formula repair uses USER_ENTERED semantics |

A stricter extractor can return more blanks when Google does not expose usable evidence. Missing observations generally preserve previously recorded facts, so `Last Observed At` is the listing observation time, **not proof every retained field was reverified**. Current Open Status becomes Unknown when not detected; a known Business Status is preserved if no new status is established. Price Level is an optional Maps signal, not menu-item pricing.

Google Category is not a normalized cuisine taxonomy. Owner/manager data, POS/BI signals, social links, Foodpanda listings, and menu pricing require separate research/enrichment.

## Resume versus a fresh run

- `RUN_ID` defaults to the current UTC date. An interrupted run on the same day resumes successful observations less than 24 hours old.
- Set a new `RUN_ID` (for example, `"2026-09-14-evening"`) to collect fresh data immediately. For a run continuing past midnight, retain its original ID; expired observations will still be revisited.
- Failed, nameless, malformed, or expired checkpoint records are retried. Search discovery URLs are also checkpointed, so ordering changes do not discard earlier discoveries on resume.
- Checkpoints are scoped to the spreadsheet ID and code schema. Old/mismatched checkpoint files are retained as timestamped backups when replaced. V2.3.1 files are left untouched because V2.3.2 uses new filenames.
- `RESET_CHECKPOINT=True` starts fresh without using the old cache. Leave it False for normal recovery. Archive files and the append-only CSV retain observation evidence.

## Validation

Run the offline regression suite from the repository root:

```powershell
python -m pip install pandas
python -m unittest discover -s tests -v
```

Tests load functions directly from the notebook, without mounting Drive, authenticating, opening a browser, or writing to a live Sheet. They exercise parsing, identities, failed/stale cache handling, observation freshness, actual writer logic using an in-memory worksheet, and notebook compilation. Browser extractor tests use mocked structured control values; they do not establish live Google Maps selector coverage.

Before a full run, perform the limited Colab smoke test against a copy of the master. Include two branches sharing a phone, a temporarily/permanently closed listing, overnight hours, and a listing with an explicitly unavailable service. Verify existing Qualified/Contacted statuses and analyst notes remain unchanged. This repository does not include a live-authenticated end-to-end test result.

## Separate future work

The attached project context positions this collector as **Layer 1: restaurant identity**. Google Places API adoption remains an explicit product/cost decision. Menu source discovery, channel-specific prices, append-only price observations, canonical item matching, competitor sets, benchmark baskets, and the owner portal belong to separate pipelines. They are not implemented by this correction.
