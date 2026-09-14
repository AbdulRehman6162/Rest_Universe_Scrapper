"""Offline regression tests load the actual notebook functions without Colab side effects."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import quote, unquote, urlparse, parse_qsl, urlencode, urlunparse

import pandas as pd

NOTEBOOK = Path(__file__).resolve().parents[1] / 'Profiling_GoogleSheets_Drive_V2_3_1_Fixed.ipynb'
CELLS = json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']


def load_functions():
    env = dict(globals(), TARGET_PRIORITY="P1")
    constants = {'MASTER_COLUMNS', 'V231_COLUMNS', 'KNOWN_BRANDS', 'TIME_TOKEN_RE', 'VALID_SPECIAL_HOURS', 'LOG_COLUMNS'}
    for index, cell in enumerate(CELLS):
        if cell['cell_type'] != 'code' or index == 2:
            continue
        tree = ast.parse(''.join(cell['source']))
        keep = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or
                (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets))]
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(NOTEBOOK), 'exec'), env)
    return env


class Cell:
    def __init__(self, row, col, value):
        self.row, self.col, self.value = row, col, value


class Worksheet:
    def __init__(self, values, row_count=None, col_count=None):
        self.values = copy.deepcopy(values)
        self.row_count = row_count or len(values)
        self.col_count = col_count or len(values[0])
        self.calls = []

    def get_all_values(self):
        return copy.deepcopy(self.values)

    def add_cols(self, count):
        self.col_count += count

    def add_rows(self, count):
        self.row_count += count

    def update_cells(self, cells, value_input_option=None):
        self.calls.append((copy.deepcopy(cells), value_input_option))
        for cell in cells:
            assert cell.row <= self.row_count and cell.col <= self.col_count
            while len(self.values) < cell.row:
                self.values.append([])
            row = self.values[cell.row - 1]
            row.extend([''] * (cell.col - len(row)))
            row[cell.col - 1] = cell.value

    def records(self):
        return [dict(zip(self.values[0], row)) for row in self.values[1:]]


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.env = load_functions()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env.update(OUTPUT_DIR=self.tmp.name, RUN_ID='test-run', RESET_CHECKPOINT=False,
                        CHECKPOINT_MAX_AGE_HOURS=24, CHECKPOINT_JSON=os.path.join(self.tmp.name, 'checkpoint.json'),
                        SCRAPE_LOG_CSV=os.path.join(self.tmp.name, 'log.csv'))
        self.env['gspread'] = types.SimpleNamespace(cell=types.SimpleNamespace(Cell=Cell))

    def call(self, name, *args):
        return self.env[name](*args)

    def master(self, *records, columns=True):
        headers = self.env['MASTER_COLUMNS'] + (self.env['V231_COLUMNS'] if columns else [])
        ws = Worksheet([headers] + [[r.get(h, '') for h in headers] for r in records])
        self.env['spreadsheet'] = types.SimpleNamespace(id='test-sheet', worksheet=lambda name: ws)
        return ws

    def record(self, **changes):
        return dict(name='Test Restaurant', place_id='ChIJbranchA', maps_url='https://www.google.com/maps/place/A',
                    phone='+92 300 1234567', address='Road A', observed_at=self.call('now_iso'), **changes)

    def test_notebook_cells_compile_and_outputs_are_empty(self):
        for i, cell in enumerate(CELLS):
            if cell['cell_type'] == 'code':
                self.assertEqual(cell['outputs'], [])
                if i != 2:
                    compile(''.join(cell['source']), str(i), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_search_and_refresh_use_shared_extractor(self):
        for index in (19, 20):
            calls = [node.func.id for node in ast.walk(ast.parse(''.join(CELLS[index]['source'])))
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
            self.assertIn('extract_place_details', calls)

    def test_strict_hours_reject_garbage(self):
        for value in ['s', 'Closes 11 PM', '11 PM', 'Copy open hours', 'See more hours',
                      '8 AM–1 AM See more hours', '13 AM–2 PM', '8:75 AM–1 PM', '8 AM blah 1 PM']:
            with self.subTest(value=value):
                self.assertEqual(self.call('extract_time_range', value), '')
                self.assertFalse(self.call('is_valid_hour_value', value))

    def test_hours_preserve_overnight_and_split_shifts(self):
        for value, expected in [('8 AM–1 AM', '8 AM–1 AM'), ('8–11 AM', '8 AM–11 AM'),
                                ('8 AM–1 PM, 5 PM–11 PM', '8 AM–1 PM, 5 PM–11 PM'),
                                ('Closed', 'Closed'), ('24 hours', 'Open 24 hours')]:
            self.assertEqual(self.call('extract_time_range', value), expected)
        self.assertEqual(self.call('compress_hours', 'Mon: 8 AM–1 AM | Sat: s | Sun: 8 AM–1 AM'),
                         'Mon: 8 AM–1 AM | Sun: 8 AM–1 AM')

    def test_service_negation_and_review_prose_are_unknown(self):
        for label in ['No delivery', "Doesn't offer delivery", 'Delivery unavailable', 'No dine-in',
                      'I loved the delivery service', 'Review mentions takeaway', 'Delivery options', 'Order delivery']:
            self.assertFalse(any(self.call('detect_service_token', label).values()), label)
        self.assertEqual(self.call('detect_service_token', 'Dine-in · Takeaway · No delivery'),
                         {'dine_in': 'Y', 'takeaway': 'Y', 'delivery': ''})
        self.assertEqual(self.call('detect_service_token', 'Service options: Contactless delivery')['delivery'], 'Y')

    def test_coordinates_use_pin_not_camera(self):
        url = 'https://www.google.com/maps/place/A/@33.1,73.1,14z/data=!3d33.7!4d73.05'
        self.assertEqual(self.call('extract_coordinates', url), (33.7, 73.05))
        self.assertEqual(self.call('extract_coordinates', 'https://maps.google.com/@33.1,73.1,14z'), (None, None))

    def test_rating_does_not_parse_review_count_or_distribution(self):
        self.assertEqual(self.call('parse_rating', '4.8 stars'), 4.8)
        for value in ['1,250 reviews', '5.9 stars', '14.3 stars', 'Rated by 3 people']:
            self.assertIsNone(self.call('parse_rating', value))

    def test_identity_bearing_url_query_is_preserved(self):
        a, b = 'https://maps.google.com/?cid=111', 'https://maps.google.com/?cid=222'
        self.assertNotEqual(self.call('normalize_maps_url', a), self.call('normalize_maps_url', b))
        self.assertNotEqual(self.call('checkpoint_key', 'SEARCH', a), self.call('checkpoint_key', 'SEARCH', b))
        self.assertEqual(self.call('normalize_maps_url', a + '&hl=en'), self.call('normalize_maps_url', a))
        self.assertEqual(self.call('extract_place_id_from_url', '?query_place_id=ChIJbranchA'), 'ChIJbranchA')
        self.assertEqual(self.call('extract_place_id_from_url', '!1s0x123:0x456'), '')

    def test_unicode_names_not_erased(self):
        self.assertNotEqual(self.call('normalize_name', 'کراچی'), self.call('normalize_name', 'اسلام آباد'))

    def test_distinct_place_ids_with_shared_phone_never_merge(self):
        ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'Branch A', 'Place ID': 'ChIJbranchA', 'Phone Number': '+92 300 1234567'})
        b = self.record(); b['place_id'] = 'ChIJbranchB'
        self.call('write_to_google_sheet', [b])
        self.assertEqual([r['Restaurant ID'] for r in ws.records()], ['1', '2'])
        self.assertEqual([r['Place ID'] for r in ws.records()], ['ChIJbranchA', 'ChIJbranchB'])

    def test_query_urls_do_not_merge_without_place_id(self):
        ws = self.master()
        a = self.record(); a.update(place_id='', phone='', maps_url='https://maps.google.com/?cid=111')
        b = dict(a, maps_url='https://maps.google.com/?cid=222', address='Road B')
        self.call('write_to_google_sheet', [a, b])
        self.assertEqual(len(ws.records()), 2)

    def test_ambiguous_phone_does_not_choose_last_branch(self):
        ws = self.master(*[{'Restaurant ID': str(i), 'Restaurant Name': f'Branch {i}', 'Phone Number': '03001234567'} for i in [1, 2]])
        rec = self.record(); rec.update(place_id='', maps_url='', phone='03001234567', address='Other road')
        self.call('write_to_google_sheet', [rec])
        self.assertEqual(len(ws.records()), 3)

    def test_repeated_new_record_keeps_reserved_id_and_is_idempotent(self):
        ws = self.master()
        rec = self.record()
        self.call('write_to_google_sheet', [rec, rec])
        self.call('write_to_google_sheet', [rec])
        self.assertEqual(len(ws.records()), 1)
        self.assertEqual(ws.records()[0]['Restaurant ID'], '1')
        self.assertEqual(ws.records()[0]['Status'], 'New Lead')

    def test_refresh_preserves_crm_notes_cuisine_and_existing_metadata(self):
        ws = self.master({'Restaurant ID': '7', 'Restaurant Name': 'Cafe', 'Place ID': 'ChIJbranchA',
                          'Status': 'Qualified', 'Notes': 'Call owner Tuesday', 'Cuisine Types': 'Pakistani; BBQ',
                          'Date Profiled': '2026-01-01', 'City': 'Islamabad', 'Foodpanda URL': 'https://example.com/menu'})
        rec = self.record(category='Restaurant', current_open_status='Closed', business_status='Operational', city='Rawalpindi')
        self.call('write_to_google_sheet', [rec])
        row = ws.records()[0]
        for field, value in [('Status', 'Qualified'), ('Notes', 'Call owner Tuesday'), ('Cuisine Types', 'Pakistani; BBQ'),
                             ('Date Profiled', '2026-01-01'), ('City', 'Islamabad'), ('Restaurant ID', '7')]:
            self.assertEqual(row[field], value)
        self.assertEqual(row['Current Open Status'], 'Closed')
        self.assertEqual(row['Business Status'], 'Operational')
        self.assertEqual(row['Last Observed At'], rec['observed_at'])

    def test_failed_rows_are_audit_only_and_do_not_create_ghosts(self):
        ws = self.master()
        rec = self.record(error='Navigation failed')
        self.call('write_to_google_sheet', [rec, {'name': ''}])
        self.assertEqual(ws.records(), [])
        self.assertEqual(self.call('deduplicate_records', [rec]), [])

    def test_missing_ids_and_blank_sheet_rows_keep_alignment(self):
        ws = self.master({'Restaurant ID': '10', 'Restaurant Name': 'A'}, {}, {'Restaurant Name': 'B'})
        self.call('write_to_google_sheet', [])
        rows = ws.records()
        self.assertEqual(rows[0]['Restaurant ID'], '10')
        self.assertEqual(rows[1]['Restaurant ID'], '')
        self.assertEqual(rows[2]['Restaurant ID'], '11')

    def test_duplicate_ids_stop_before_mutating_sheet(self):
        ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'A'}, {'Restaurant ID': '1', 'Restaurant Name': 'B'})
        with self.assertRaisesRegex(ValueError, 'Duplicate Restaurant ID'):
            self.call('write_to_google_sheet', [self.record()])
        self.assertEqual(ws.calls, [])

    def test_duplicate_place_ids_stop_before_mutating_sheet(self):
        ws = self.master(*[{'Restaurant ID': str(i), 'Restaurant Name': str(i), 'Place ID': 'ChIJduplicate'} for i in [1, 2]])
        with self.assertRaisesRegex(ValueError, 'Duplicate Place ID'):
            self.call('write_to_google_sheet', [])
        self.assertEqual(ws.calls, [])

    def test_grows_grid_and_uses_raw_to_preserve_text(self):
        ws = self.master(columns=False)
        rec = self.record(); rec['name'] = '=IMPORTXML("https://example.com","x")'
        self.call('write_to_google_sheet', [rec])
        self.assertEqual(ws.row_count, 2)
        self.assertEqual(ws.col_count, len(ws.values[0]))
        self.assertTrue(all(option == 'RAW' for _, option in ws.calls))
        self.assertEqual(ws.records()[0]['Phone Number'], '+92 300 1234567')
        self.assertTrue(list(Path(self.tmp.name).glob('master_before_write_*.json')))

    def test_newer_observation_wins_over_cached_refresh(self):
        old = self.record(); old['observed_at'] = '2026-01-01T00:00:00+00:00'; old['review_count'] = 100
        new = dict(old, observed_at='2026-01-02T00:00:00+00:00', review_count=120)
        self.assertEqual(self.call('deduplicate_records', [old, new])[0]['review_count'], 120)
        ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'A', 'Place ID': old['place_id'],
                          'Last Observed At': new['observed_at'], 'Google Review Count': 120})
        self.call('write_to_google_sheet', [old])
        self.assertEqual(ws.records()[0]['Google Review Count'], 120)

    def test_checkpoint_retries_errors_and_expired_observations(self):
        self.master()
        self.env['checkpoint'] = self.call('load_checkpoint')
        rec = self.record()
        url = rec['maps_url']
        self.call('save_record_checkpoint', 'SEARCH', url, rec)
        self.assertEqual(self.call('get_cached', 'SEARCH', url), rec)
        cached = self.call('get_cached', 'SEARCH', url); cached['name'] = 'Changed'
        self.assertNotEqual(self.call('get_cached', 'SEARCH', url)['name'], 'Changed')
        for variant in [dict(rec, error='Timeout'), dict(rec, name=''), dict(rec, observed_at='bad'),
                        dict(rec, observed_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())]:
            self.call('save_record_checkpoint', 'SEARCH', url, variant)
            self.assertIsNone(self.call('get_cached', 'SEARCH', url))

    def test_new_run_or_different_sheet_invalidates_checkpoint_and_retains_backup(self):
        self.master()
        self.env['checkpoint'] = self.call('load_checkpoint')
        rec = self.record(); self.call('save_record_checkpoint', 'REFRESH', rec['maps_url'], rec)
        self.env['RUN_ID'] = 'new-run'
        self.assertEqual(self.call('load_checkpoint')['records'], {})
        self.assertTrue(list(Path(self.tmp.name).glob('*.bak')))
        self.env['checkpoint'] = self.call('load_checkpoint')
        self.call('save_record_checkpoint', 'REFRESH', rec['maps_url'], rec)
        self.env['spreadsheet'].id = 'other-sheet'
        self.assertEqual(self.call('load_checkpoint')['records'], {})

    def test_logs_persist_across_modes_and_runs(self):
        self.master(); self.env['checkpoint'] = self.call('load_checkpoint')
        self.call('append_log', {'mode': 'REFRESH', 'restaurant': 'A'})
        self.env['checkpoint'] = self.call('load_checkpoint')
        self.call('append_log', {'mode': 'SEARCH', 'restaurant': 'B', 'query': 'cafes'})
        df = pd.read_csv(self.env['SCRAPE_LOG_CSV'])
        self.assertEqual(df['mode'].tolist(), ['REFRESH', 'SEARCH'])
        self.assertEqual(len(self.call('load_checkpoint')['logs']), 2)

    def test_review_count_focused_controls_and_zero_count(self):
        self.env['place_panel'] = lambda page: page
        for labels, texts, expected in [(['1,250 reviews'], [], 1250), ([], ['(18)'], 18),
                                        (['0 reviews'], [], 0), (['4.6 stars'], [], None)]:
            self.env['structured_values'] = AsyncMock(side_effect=[labels, texts])
            result = asyncio.run(self.call('extract_review_count', object()))
            self.assertEqual(result['value'], expected)

    def test_business_status_not_inferred_from_review_history(self):
        self.env['place_panel'] = lambda page: page
        for labels, expected in [(['1,250 reviews'], ('Unknown', 'Unknown')),
                                  (['Closed · Opens 8 AM'], ('Closed', 'Operational')),
                                  (['Open · Closes 11 PM'], ('Open', 'Operational')),
                                  (['Permanently closed'], ('Closed', 'Permanently Closed')),
                                  (['Temporarily closed', 'Open now'], ('Closed', 'Temporarily Closed')),
                                  (['A reviewer says permanently closed'], ('Unknown', 'Unknown'))]:
            self.env['structured_values'] = AsyncMock(return_value=labels)
            result = asyncio.run(self.call('extract_business_status', object()))
            self.assertEqual((result['current_open_status'], result['business_status']), expected)


    def test_search_discovery_resumes_and_failed_discovery_retries(self):
        self.master(); self.env['checkpoint'] = self.call('load_checkpoint')
        expected = [{'url': 'https://maps.google.com/?cid=123', 'label': 'Cafe'}]
        collector = AsyncMock(side_effect=[expected, [], expected])
        self.env['collect_search_results'] = collector
        first = asyncio.run(self.call('resumable_search_results', object(), 'cafes Islamabad', 10))
        self.env['checkpoint'] = self.call('load_checkpoint')
        resumed = asyncio.run(self.call('resumable_search_results', object(), 'cafes Islamabad', 10))
        self.assertEqual(first, resumed)
        self.assertEqual(collector.await_count, 1)
        self.assertEqual(asyncio.run(self.call('resumable_search_results', object(), 'cafes Rawalpindi', 10)), [])
        self.assertEqual(asyncio.run(self.call('resumable_search_results', object(), 'cafes Rawalpindi', 10)), expected)
        self.assertEqual(collector.await_count, 3)

    def test_blank_headers_stop_before_any_sheet_write(self):
        ws = self.master(); ws.values[0][2] = ''
        with self.assertRaisesRegex(ValueError, 'blank headers'):
            self.call('write_to_google_sheet', [self.record()])
        self.assertEqual(ws.calls, [])

    def test_legacy_dirty_hours_cleared_but_clean_schedule_retained(self):
        for existing, expected in [('Mon–Fri: 8 AM–1 AM | Sat: s | Sun: 8 AM–1 AM', ''),
                                    ('Mon–Fri: 8 AM–1 AM | Sat: Closed | Sun: Open 24 hours',
                                     'Mon–Fri: 8 AM–1 AM | Sat: Closed | Sun: Open 24 hours')]:
            ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'A', 'Place ID': 'ChIJbranchA',
                              'Operational Timing': existing})
            self.call('write_to_google_sheet', [self.record(hours='')])
            self.assertEqual(ws.records()[0]['Operational Timing'], expected)

    def test_changed_phone_is_removed_from_identity_index(self):
        ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'A', 'Place ID': 'ChIJbranchA', 'Phone Number': '111'})
        first = self.record(); first['phone'] = '222'
        second = self.record(); second.update(name='B', place_id='', maps_url='', phone='111', address='Road B')
        self.call('write_to_google_sheet', [first, second])
        self.assertEqual(len(ws.records()), 2)
        self.assertEqual(ws.records()[0]['Phone Number'], '222')

    def test_url_place_id_obeys_strict_identity_when_field_is_empty(self):
        ws = self.master({'Restaurant ID': '1', 'Restaurant Name': 'A', 'Place ID': 'ChIJbranchA', 'Phone Number': '111'})
        rec = self.record(); rec.update(place_id='', phone='111', maps_url='https://maps.google.com/?query_place_id=ChIJbranchB')
        self.call('write_to_google_sheet', [rec])
        self.assertEqual(len(ws.records()), 2)

    def test_shared_detail_extractor_returns_same_facts_in_both_modes(self):
        class Heading:
            first = property(lambda self: self)
            count = AsyncMock(return_value=1)
            inner_text = AsyncMock(return_value='Test Cafe')
        page = types.SimpleNamespace(url='https://www.google.com/maps/place/A/data=!3d33.7!4d73.05')
        self.env['place_panel'] = lambda p: types.SimpleNamespace(locator=lambda sel: Heading())
        self.env['safe_goto'] = AsyncMock()
        for name, value in [('extract_rating', 4.5), ('extract_review_count', {'value': 100, 'source': 'aria-label', 'raw': '100 reviews'}),
                            ('extract_address', 'Road A'), ('extract_phone', '111'), ('extract_website', ''),
                            ('extract_google_category', 'Cafe'), ('extract_hours', 'Mon: 8 AM–1 AM'),
                            ('extract_service_signals', {'dine_in': 'Y', 'takeaway': '', 'delivery': ''}),
                            ('extract_business_status', {'current_open_status': 'Closed', 'business_status': 'Operational'}),
                            ('extract_price_level', '')]:
            self.env[name] = AsyncMock(return_value=value)
        async def collect(mode):
            return await self.env['extract_place_details'](page, url='https://maps.google.com/?query_place_id=ChIJbranchA', mode=mode)
        refresh, search = asyncio.run(collect('REFRESH')), asyncio.run(collect('SEARCH'))
        for key in refresh:
            if key not in {'mode', 'observed_at'}:
                self.assertEqual(refresh[key], search[key], key)
        self.assertEqual(search['place_id'], 'ChIJbranchA')
        self.assertEqual(search['review_count'], 100)
        self.assertEqual(search['error'], '')


if __name__ == '__main__':
    unittest.main()
