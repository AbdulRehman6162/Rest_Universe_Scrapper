"""Local Windows runner: python local_runner.py --help."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def local_path(value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def nonnegative(value):
    value = int(value)
    if value < 0:
        raise argparse.ArgumentTypeError("Use 0 for all, or a positive number.")
    return value


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("Must be at least 1.")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["both", "search", "refresh"], default="both")
    parser.add_argument("--query", help="One query instead of the target-area search plan.")
    parser.add_argument("--priority", default="P1")
    parser.add_argument("--max-refresh", type=nonnegative, default=10, help="0 = all")
    parser.add_argument("--max-queries", type=nonnegative, default=2, help="0 = all")
    parser.add_argument("--max-results", type=positive, default=10)
    parser.add_argument("--run-id", help="Reuse to resume, change for fresh collection.")
    parser.add_argument("--output-dir")
    parser.add_argument("--headless", action="store_true", help="Hide Chromium; visible by default.")
    parser.add_argument("--check", action="store_true", help="Check Sheets access and inputs only.")
    parser.add_argument("--write", action="store_true", help="Sync successful observations to Sheets.")
    args = parser.parse_args(argv)
    if args.query and args.mode == "refresh":
        parser.error("--query requires search or both mode")
    if args.check and args.write:
        parser.error("--check cannot be combined with --write")
    return args


def connect_sheet():
    import gspread
    url = os.getenv("GOOGLE_SHEET_URL", "").strip()
    if not url.startswith("https://docs.google.com/spreadsheets/d/"):
        raise ValueError("Set GOOGLE_SHEET_URL in .env to your spreadsheet URL.")
    method = os.getenv("GOOGLE_AUTH_METHOD", "oauth").strip().lower()
    if method == "oauth":
        credentials = local_path(os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "secrets/oauth_client.json"))
        token = local_path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "secrets/authorized_user.json"))
        if not credentials.is_file():
            raise FileNotFoundError(f"Desktop OAuth JSON missing: {credentials}. See LOCAL_SETUP.md.")
        token.parent.mkdir(parents=True, exist_ok=True)
        client = gspread.oauth(credentials_filename=str(credentials), authorized_user_filename=str(token))
    elif method == "service_account":
        credentials = local_path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/service_account.json"))
        if not credentials.is_file():
            raise FileNotFoundError(f"Service-account JSON missing: {credentials}. See LOCAL_SETUP.md.")
        client = gspread.service_account(filename=str(credentials),
                                        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    else:
        raise ValueError("GOOGLE_AUTH_METHOD must be oauth or service_account.")
    return client.open_by_url(url)


def configure_core(core, args, sheet, output):
    core.spreadsheet = sheet
    core.OUTPUT_DIR = str(output)
    core.CHECKPOINT_JSON = str(output / "checkpoint.json")
    core.SCRAPE_LOG_CSV = str(output / "scrape_log.csv")
    core.RUN_ID = args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    core.RESET_CHECKPOINT = False
    core.RUN_REFRESH = args.mode in {"both", "refresh"}
    core.RUN_SEARCH = args.mode in {"both", "search"}
    core.MAX_REFRESH_RECORDS = args.max_refresh or None
    core.MAX_SEARCH_QUERIES = args.max_queries or None
    core.MAX_RESULTS_PER_QUERY = args.max_results
    core.TARGET_PRIORITY = args.priority
    core.HEADLESS = args.headless
    core.COLLECTION_STATUS = "RUNNING"


def prepare_inputs(core, args):
    core.validate_google_sheet()
    core.existing_records = core.read_existing_master()
    core.report_refresh_inputs(core.existing_records)
    core.queries = []
    if core.RUN_SEARCH:
        core.queries = (
            [{"query": args.query, "city": "", "area": "", "category": "", "priority": args.priority}]
            if args.query else core.generate_queries(priority=args.priority)
        )
        if not core.queries:
            raise ValueError(f"No queries match priority {args.priority}. Check the target-area tab.")
    print("Search queries available:", len(core.queries))
    if core.RUN_REFRESH and not core.RUN_SEARCH and not any(r.get("maps_url") for r in core.existing_records):
        raise ValueError("No Maps links available for REFRESH. Use --mode search to discover restaurants.")


async def collect_local(core):
    # Windows default Proactor loop supports Playwright subprocesses.
    # No nest_asyncio, Drive mounts or Linux browser flags.
    async with core.async_playwright() as p:
        browser = await p.chromium.launch(headless=core.HEADLESS)
        try:
            context = await browser.new_context(viewport=core.VIEWPORT, locale="en-US")
            refresh = await core.run_refresh(context)
            search = await core.run_search(context)
            return refresh + search
        finally:
            await browser.close()


def save_observations(records, output):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = output / f"observations_{stamp}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("Raw observations:", path)
    return path


def execute(args):
    from dotenv import load_dotenv
    from filelock import FileLock, Timeout
    from collector_core import load_core
    load_dotenv(ROOT / ".env", override=False)
    sheet = connect_sheet()
    output = local_path(args.output_dir or os.getenv("OUTPUT_DIR", "output")) / sheet.id
    output.mkdir(parents=True, exist_ok=True)
    log = output / ("run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".log")
    logging.basicConfig(filename=log, encoding="utf-8", level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")
    print("Spreadsheet:", sheet.title)
    print("Spreadsheet URL:", sheet.url)
    print("Output folder:", output)
    print("Run-summary/error log:", log)
    core = load_core()
    configure_core(core, args, sheet, output)
    try:
        with FileLock(str(output / "collector.lock"), timeout=0):
            prepare_inputs(core, args)
            if args.check:
                print("CHECK PASSED. No browser opened; no restaurant rows written.")
                return 0
            core.checkpoint = core.load_checkpoint()
            logging.info("Started mode=%s run_id=%s write=%s", args.mode, core.RUN_ID, args.write)
            try:
                records = asyncio.run(collect_local(core))
            except (Exception, KeyboardInterrupt):
                core.COLLECTION_STATUS = "FAILED"
                save_observations(list(core.checkpoint.get("records", {}).values()), output)
                raise
            core.COLLECTION_STATUS = "COMPLETE"
            save_observations(records, output)
            unique = core.deduplicate_records(records)
            core.quality_report(unique)
            if unique:
                core.diagnostics_table(unique).to_csv(output / "latest_diagnostics.csv", index=False)
            failed = sum(bool(r.get("error")) or not r.get("name") for r in records)
            if failed:
                print(f"{failed} failed detail records retained in audit files, excluded from writes.")
            if args.write and unique:
                core.write_to_google_sheet(unique)
                core.verify_google_sheet()
            elif not args.write:
                print("PREVIEW COMPLETE: Sheets unchanged. Repeat with --write to sync.")
            else:
                print("No successful observations to write.")
            logging.info("Complete records=%d unique=%d failed=%d", len(records), len(unique), failed)
            return 2 if records and not unique else 0
    except Timeout:
        raise RuntimeError("Another collector holds this output folder's lock. Stop it before rerunning.")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    try:
        return execute(args)
    except KeyboardInterrupt:
        print("\nStopped. Successful observations are retained for resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.exception("Local collector failed")
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("See LOCAL_SETUP.md and output/<sheet-id>/search_debug.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
