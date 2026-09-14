"""Regression coverage for zero-results discovery and observable failure states."""
import asyncio
import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

from test_notebook import load_functions, CELLS


class SearchDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = load_functions()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env.update(OUTPUT_DIR=self.tmp.name, SEARCH_READY_TIMEOUT_MS=45000,
                        MAX_SEARCH_SCROLLS=20, SEARCH_SCROLL_WAIT_MS=2000,
                        MAX_NAV_RETRIES=3, RETRY_BASE_DELAY=0, PAGE_WAIT_MIN=0, PAGE_WAIT_MAX=0)
        self.env['append_log'] = Mock()
        self.page = types.SimpleNamespace(url='https://www.google.com/maps/search/?api=1&query=cafes',
                                          wait_for_timeout=AsyncMock(), mouse=types.SimpleNamespace(wheel=AsyncMock()))
        self.env['safe_goto'] = AsyncMock()
        self.env['detect_captcha'] = AsyncMock(return_value=False)
        self.env['save_search_debug'] = AsyncMock(return_value='/drive/search_debug/cafes.json')

    def run_async(self, name, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.env[name](*args))

    def test_documented_query_syntax_preserves_f6(self):
        parsed = urlparse(self.env['maps_search_url']('restaurants in F-6 Islamabad'))
        self.assertEqual(parsed.path, '/maps/search/')
        self.assertEqual(parse_qs(parsed.query), {'api': ['1'], 'query': ['restaurants in F-6 Islamabad'], 'hl': ['en']})

    def test_listing_url_supports_relative_cid_and_place_id(self):
        for url in ['/maps/place/Cafe/data=!1sChIJabc', 'https://www.google.com/maps?cid=123',
                    'https://www.google.com/maps/search/?api=1&query=Cafe&query_place_id=ChIJabc',
                    'https://maps.google.com/?q=place_id:ChIJabc']:
            self.assertTrue(self.env['listing_url'](url), url)
        for url in ['https://evil.test/maps/place/Cafe', 'javascript:alert(1)', '/maps/search/cafes',
                    'https://www.google.com/maps/dir/?api=1&destination=Cafe']:
            self.assertEqual(self.env['listing_url'](url), '', url)

    def test_page_states_distinguish_blocked_consent_loading_and_no_results(self):
        for snapshot, expected in [
            ({'body': 'Our systems have detected unusual traffic'}, 'blocked'),
            ({'body': '', 'url': 'https://www.google.com/sorry/index'}, 'blocked'),
            ({'http_status': 429, 'listing_count': 4}, 'blocked'),
            ({'http_status': 503}, 'http_error'),
            ({'body': 'Before you continue to Google'}, 'consent'),
            ({'body': 'Please enable JavaScript'}, 'javascript_required'),
            ({'body': "This site can’t be reached"}, 'network_error'),
            ({'body': 'Google Maps\nNo results found\nTry another search'}, 'no_results'),
            ({'body': 'Loading...', 'feed_count': 1}, 'unrecognized'),
            ({'body': 'Cafe results', 'listing_count': 2}, 'results'),
            ({'url': 'https://www.google.com/maps/place/Cafe', 'detail_name': 'Cafe'}, 'single_place'),
        ]:
            self.assertEqual(self.env['classify_maps_page'](snapshot), expected, snapshot)

    def test_unrecognized_zero_links_raises_after_one_reload_and_logs_report(self):
        self.env['wait_for_search_state'] = AsyncMock(return_value={'state': 'unrecognized', 'body': 'Loading...'})
        with self.assertRaisesRegex(self.env['SearchCollectionError'], 'SEARCH FAILED'):
            self.run_async('collect_search_results', self.page, 'cafes F-6 Islamabad', 5)
        self.assertEqual(self.env['safe_goto'].await_count, 2)
        self.assertEqual(self.env['save_search_debug'].await_count, 1)
        self.assertEqual(self.env['append_log'].call_args.args[0]['status'], 'ERROR')

    def test_blocked_page_stops_without_repeated_searches(self):
        self.env['wait_for_search_state'] = AsyncMock(return_value={'state': 'blocked'})
        with self.assertRaises(self.env['SearchCollectionError']):
            self.run_async('collect_search_results', self.page, 'cafes Islamabad')
        self.assertEqual(self.env['safe_goto'].await_count, 1)

    def test_navigation_failure_is_not_an_empty_success(self):
        self.env['safe_goto'] = AsyncMock(side_effect=TimeoutError('navigation timeout'))
        with self.assertRaises(self.env['SearchCollectionError']):
            self.run_async('collect_search_results', self.page, 'cafes Islamabad')
        self.assertEqual(self.env['save_search_debug'].await_count, 1)

    def test_explicit_no_results_is_logged_and_returns_empty(self):
        self.env['wait_for_search_state'] = AsyncMock(return_value={'state': 'no_results'})
        self.assertEqual(self.run_async('collect_search_results', self.page, 'impossible query'), [])
        self.env['save_search_debug'].assert_not_awaited()
        self.assertEqual(self.env['append_log'].call_args.args[0]['status'], 'NO_RESULTS')

    def test_slow_loading_recovers_on_bounded_retry(self):
        self.env['wait_for_search_state'] = AsyncMock(side_effect=[{'state': 'unrecognized'}, {'state': 'results'}])
        expected = [{'url': 'https://www.google.com/maps/place/Cafe', 'label': 'Cafe'}]
        self.env['discover_listing_links'] = AsyncMock(return_value=expected)
        self.assertEqual(self.run_async('collect_search_results', self.page, 'cafes', 1), expected)
        self.assertEqual(self.env['safe_goto'].await_count, 2)

    def test_direct_place_redirect_is_a_result(self):
        self.page.url = 'https://www.google.com/maps/place/Cafe'
        self.env['wait_for_search_state'] = AsyncMock(return_value={'state': 'single_place', 'detail_name': 'Cafe'})
        self.assertEqual(self.run_async('collect_search_results', self.page, 'Cafe Islamabad'),
                         [{'url': self.page.url, 'label': 'Cafe'}])

    def test_non_div_feed_scrolls_and_accumulates_unique_links(self):
        self.env['wait_for_search_state'] = AsyncMock(return_value={'state': 'results'})
        a = {'url': 'https://www.google.com/maps/place/A', 'label': 'A'}
        b = {'url': 'https://www.google.com/maps/place/B', 'label': 'B'}
        self.env['discover_listing_links'] = AsyncMock(side_effect=[[a], [a, b]])
        feed = types.SimpleNamespace(count=AsyncMock(return_value=1), evaluate=AsyncMock())
        feed.first = feed
        self.page.locator = Mock(return_value=feed)
        self.assertEqual(self.run_async('collect_search_results', self.page, 'cafes', 2), [a, b])
        self.page.locator.assert_called_with('[role="feed"]')
        feed.evaluate.assert_awaited_once()

    def test_link_discovery_reads_live_attributes_and_deduplicates_tracking(self):
        values = [{'href': '/maps/place/A?hl=en', 'label': 'A'}, {'href': '/maps/place/A?hl=ur', 'label': 'A'},
                  {'place_id': 'ChIJbbb', 'label': 'B'}, {'cid': '123', 'label': 'C'},
                  {'href': 'https://example.com/maps/place/NotGoogle', 'label': 'noise'}]
        self.page.locator = Mock(return_value=types.SimpleNamespace(evaluate_all=AsyncMock(return_value=values)))
        results = self.run_async('discover_listing_links', self.page)
        self.assertEqual(len(results), 3)
        self.assertIn('query_place_id=ChIJbbb', results[1]['url'])
        self.assertEqual(results[2]['url'], 'https://www.google.com/maps?cid=123')

    def test_debug_bundle_survives_screenshot_failure(self):
        env = load_functions(); env['OUTPUT_DIR'] = self.tmp.name
        self.page.screenshot = AsyncMock(side_effect=RuntimeError('screenshot failed'))
        self.page.content = AsyncMock(return_value='<html>Loading...</html>')
        snapshot = {'url': self.page.url, 'state': 'unrecognized', 'body': 'Loading...', 'title': 'Maps'}
        with contextlib.redirect_stdout(io.StringIO()):
            path = asyncio.run(env['save_search_debug'](self.page, 'cafes F-6', 'no usable links', snapshot))
        report = json.loads(Path(path).read_text())
        self.assertEqual(report['state'], 'unrecognized')
        self.assertIn('screenshot_error', report['files'])
        self.assertTrue(Path(report['files']['html']).exists())

    def test_failed_search_is_not_checkpointed_as_empty_success(self):
        self.env['checkpoint'] = {'records': {}, 'logs': []}
        self.env['save_checkpoint'] = Mock()
        self.env['collect_search_results'] = AsyncMock(side_effect=self.env['SearchCollectionError']('failed'))
        with self.assertRaises(self.env['SearchCollectionError']):
            self.run_async('resumable_search_results', self.page, 'cafes', 10)
        self.assertEqual(self.env['checkpoint']['search_queries'], {})
        self.env['save_checkpoint'].assert_not_called()

    def test_empty_refresh_does_not_open_browser_tab(self):
        self.env.update(RUN_REFRESH=True, MAX_REFRESH_RECORDS=10, existing_records=[])
        context = types.SimpleNamespace(new_page=AsyncMock())
        self.assertEqual(self.run_async('run_refresh', context), [])
        context.new_page.assert_not_awaited()

    def test_failure_clears_old_results_and_blocks_writer(self):
        self.env.update(refresh_results=[{'name': 'Old'}], search_results=[{'name': 'Old'}], unique_records=[{'name': 'Old'}])
        cell = ast.parse(''.join(CELLS[21]['source']))
        cell.body = [node for node in cell.body if not isinstance(node, ast.AsyncFunctionDef)]
        self.env['run_collection'] = AsyncMock(side_effect=self.env['SearchCollectionError']('failed'))
        async def execute():
            await eval(compile(cell, 'collection-cell', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), self.env)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(self.env['SearchCollectionError']):
            asyncio.run(execute())
        self.assertEqual(self.env['COLLECTION_STATUS'], 'FAILED')
        self.assertEqual(self.env['unique_records'], [])
        with self.assertRaisesRegex(RuntimeError, 'incomplete/failed'):
            self.env['write_to_google_sheet']([{'name': 'Old'}])

    def test_wait_function_uses_configured_timeout_and_inspects_after_timeout(self):
        self.page.wait_for_function = AsyncMock(side_effect=TimeoutError('not ready'))
        self.env['inspect_maps_page'] = AsyncMock(return_value={'state': 'unrecognized'})
        self.assertEqual(self.run_async('wait_for_search_state', self.page), {'state': 'unrecognized'})
        self.assertEqual(self.page.wait_for_function.call_args.kwargs['timeout'], 45000)


if __name__ == '__main__':
    unittest.main()
