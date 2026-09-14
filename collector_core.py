"""Shared extraction code, loaded without Colab setup or notebook execution."""
import ast
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import traceback
import types
from urllib.parse import quote, unquote, urlparse, parse_qsl, urlencode, urlunparse, urljoin

NOTEBOOK = Path(__file__).with_name("Profiling_GoogleSheets_Drive_V2_3_1_Fixed.ipynb")
CONSTANTS = set("""
RUN_REFRESH RUN_SEARCH MAX_REFRESH_RECORDS MAX_SEARCH_QUERIES MAX_RESULTS_PER_QUERY
TARGET_PRIORITY SEARCH_CATEGORIES HEADLESS VIEWPORT MIN_DELAY MAX_DELAY PAGE_WAIT_MIN
PAGE_WAIT_MAX MAX_NAV_RETRIES RETRY_BASE_DELAY CAPTCHA_PAUSE RESET_CHECKPOINT
CHECKPOINT_MAX_AGE_HOURS REPAIR_DASHBOARD SEARCH_READY_TIMEOUT_MS MAX_SEARCH_SCROLLS
SEARCH_SCROLL_WAIT_MS REQUIRED_SHEETS MASTER_COLUMNS V231_COLUMNS KNOWN_BRANDS
TIME_TOKEN_RE VALID_SPECIAL_HOURS LOG_COLUMNS
""".split())


def load_core():
    import pandas as pd
    import gspread
    from playwright.async_api import async_playwright
    core = types.ModuleType("restaurant_collector_core")
    core.__dict__.update({k: globals()[k] for k in (
        "asyncio", "datetime", "timezone", "json", "os", "Path", "random", "re",
        "traceback", "quote", "unquote", "urlparse", "parse_qsl", "urlencode",
        "urlunparse", "urljoin",
    )})
    core.pd, core.gspread, core.async_playwright = pd, gspread, async_playwright
    core.display = lambda df: print(df.to_string(index=False))
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for i, cell in enumerate(notebook["cells"]):
        if i < 5 or cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nodes.append(node)
            elif isinstance(node, ast.Assign) and all(
                isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets
            ):
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]),
                     f"{NOTEBOOK}:cell{i}", "exec"), core.__dict__)
    # No authentication, pip, mount, collection or Sheets-writing cells execute above.
    return core
