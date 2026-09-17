"""Real Chromium + local ParaBank integration tests. No model and no test IDs."""
import asyncio
import copy
from dataclasses import replace
import unittest
from time import monotonic

from automation.browser import BrowserAdapter, transform
from automation.errors import UIError
from tools.ui_smoke import act, walkthrough
from tools.validate_contracts import ROOT, load


class BrowserAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capability = load(ROOT / 'examples/lookup_transaction.capability.json')
        self.profile = load(ROOT / 'profiles/parabank_local_browser.json')
        self.adapter = BrowserAdapter(self.capability, self.profile,
                                      {'account_id': '12345', 'transaction_id': '12145'})
        await self.adapter.__aenter__()
        self.addAsyncCleanup(self.adapter.__aexit__, None, None, None)
        await self.adapter.login('john', 'demo')

    async def search_form(self):
        await act(self.adapter, 'click', 'find_transactions')
        await self.adapter.wait({'predicate': 'ready', 'target': 'search'})

    async def test_complete_walkthrough_and_second_input(self):
        result = await walkthrough(self.adapter)
        self.assertEqual(result['outputs']['transaction_id'], '12145')
        self.assertEqual(result['outputs']['amount'], '300.00')
        self.assertEqual(result['outputs']['type'], 'credit')
        await self.adapter.login('john', 'demo')
        self.adapter.inputs['transaction_id'] = '12256'
        result = await walkthrough(self.adapter)
        self.assertEqual(result['outputs']['transaction_id'], '12256')
        self.assertEqual(result['outputs']['amount'], '100.00')
        self.assertEqual(result['outputs']['type'], 'debit')

    async def test_missing_record_and_missing_account(self):
        self.adapter.inputs['transaction_id'] = '999999999'
        self.assertEqual((await walkthrough(self.adapter))['code'], 'transaction_not_found_in_account')
        await self.adapter.login('john', 'demo')
        self.adapter.inputs['account_id'] = '999999999'
        self.assertEqual((await walkthrough(self.adapter))['code'], 'account_not_available')

    async def test_real_readiness_waits_for_delayed_activity(self):
        async def delay(route):
            await asyncio.sleep(0.5)
            await route.continue_()
        await self.adapter.page.route('**/accounts/12345/transactions/**', delay)
        await act(self.adapter, 'click', 'account_link')
        self.assertFalse(await self.adapter.evaluate({'predicate': 'ready', 'target': 'activity'}))
        self.assertIsNone(await self.adapter.evaluate({'predicate': 'count_equals', 'target': 'account_transaction', 'expected': 0}))
        started = monotonic()
        await self.adapter.wait({'predicate': 'ready', 'target': 'activity'})
        self.assertGreater(monotonic() - started, 0.25)
        self.assertTrue(await self.adapter.evaluate({'predicate': 'count_equals', 'target': 'account_transaction', 'expected': 1}))

    async def test_failed_activity_is_not_empty_business_outcome(self):
        await self.adapter.page.route('**/accounts/12345/transactions/**',
                                      lambda route: route.fulfill(status=500, body='Injected failure'))
        with self.assertRaises(UIError) as caught:
            await walkthrough(self.adapter)
        self.assertEqual(caught.exception.code, 'app_error')

    async def test_bounded_timeout(self):
        async def delay(route):
            await asyncio.sleep(0.5)
            await route.continue_()
        await self.adapter.page.route('**/accounts/12345/transactions/**', delay)
        await act(self.adapter, 'click', 'account_link')
        with self.assertRaises(UIError) as caught:
            await self.adapter.wait({'predicate': 'ready', 'target': 'activity'}, timeout_ms=50)
        self.assertEqual(caught.exception.code, 'load_timeout')
        await asyncio.sleep(0.55)  # Drain test fixture before browser teardown.

    async def test_repeated_buttons_are_disambiguated_by_existing_id(self):
        await self.search_form()
        self.assertEqual(await self.adapter.page.get_by_role('button', name='Find Transactions', exact=True).count(), 4)
        resolution = await self.adapter.resolve('search_by_id', await self.adapter.observe())
        self.assertEqual(resolution.count, 1)
        await act(self.adapter, 'fill', 'transaction_input', {'input': 'transaction_id'})
        self.assertTrue(await self.adapter.evaluate({'predicate': 'value_equals', 'target': 'transaction_input',
                                                    'expected': {'input': 'transaction_id'}}))

    async def test_duplicate_target_stops_without_fallback(self):
        await self.search_form()
        # Controlled legacy-UI mutation inside this isolated local test session.
        await self.adapter.page.evaluate("document.querySelector('#findById').after(document.querySelector('#findById').cloneNode(true))")
        self.adapter.profile = copy.deepcopy(self.profile)
        self.adapter.profile['bindings']['search_by_id']['candidates'].append({'strategy': 'css', 'selector': '#findByDate'})
        resolution = await self.adapter.resolve('search_by_id', await self.adapter.observe())
        self.assertEqual(resolution.count, 2)
        with self.assertRaises(UIError) as caught:
            await self.adapter.perform({'type': 'click', 'target': 'search_by_id'}, resolution, epoch=self.adapter.epoch)
        self.assertEqual(caught.exception.code, 'target_ambiguous')
        self.assertTrue(await self.adapter.page.locator('#formContainer').is_visible())

    async def test_observation_from_another_session_is_rejected(self):
        observation = replace(await self.adapter.observe(), session_id='other_session')
        with self.assertRaises(UIError) as caught:
            await self.adapter.resolve('account_link', observation)
        self.assertEqual(caught.exception.code, 'stale_observation')

    async def test_stale_observation_is_rejected(self):
        await self.search_form()
        resolution = await self.adapter.resolve('transaction_input', await self.adapter.observe())
        await self.adapter.page.locator('#transactionId').fill('999')
        with self.assertRaises(UIError) as caught:
            await self.adapter.perform({'type': 'fill', 'target': 'transaction_input', 'value': {'input': 'transaction_id'}},
                                       resolution, epoch=self.adapter.epoch)
        self.assertEqual(caught.exception.code, 'stale_observation')

    async def test_control_epoch_and_human_ownership_block_actions(self):
        old_epoch = self.adapter.epoch
        resolution = await self.adapter.resolve('account_link', await self.adapter.observe())
        await self.adapter.set_owner('human')
        with self.assertRaises(UIError) as caught:
            await self.adapter.perform({'type': 'click', 'target': 'account_link'}, resolution, epoch=old_epoch)
        self.assertEqual(caught.exception.code, 'control_denied')
        await self.adapter.set_owner('automation')
        with self.assertRaises(UIError):
            await self.adapter.perform({'type': 'click', 'target': 'account_link'}, resolution, epoch=old_epoch)

    async def test_policy_blocks_rebound_write_link(self):
        self.adapter.profile = copy.deepcopy(self.profile)
        self.adapter.profile['bindings']['find_transactions']['candidates'] = [
            {'strategy': 'role', 'role': 'link', 'name': {'literal': 'Transfer Funds'}}]
        with self.assertRaises(UIError) as caught:
            await act(self.adapter, 'click', 'find_transactions')
        self.assertEqual(caught.exception.code, 'policy_denied')
        self.assertTrue(self.adapter.page.url.endswith('/overview.htm'))

    async def test_session_expiry_is_explicit(self):
        # Session-expiry fixture: direct UI logout, not a new browser context.
        await self.adapter.page.get_by_role('link', name='Log Out', exact=True).click()
        await self.adapter.page.locator('input[type="password"]').wait_for(state='visible')
        with self.assertRaises(UIError) as caught:
            await self.adapter.observe()
        self.assertEqual(caught.exception.code, 'session_expired')

    async def test_duplicate_query_keys_fail_closed(self):
        await self.adapter.page.locator('#accountTable tbody a').first.evaluate(
            '(e) => e.href = "activity.htm?id=12345&id=54321"')
        with self.assertRaises(UIError) as caught:
            await self.adapter.resolve('account_link', await self.adapter.observe())
        self.assertEqual(caught.exception.code, 'checkpoint_failed')

    async def test_not_found_search_waits_for_completed_404(self):
        await self.search_form()
        self.adapter.inputs['transaction_id'] = '999999999'
        await act(self.adapter, 'fill', 'transaction_input', {'input': 'transaction_id'})
        await act(self.adapter, 'click', 'search_by_id')
        await self.adapter.wait({'predicate': 'ready', 'target': 'results'})
        self.assertTrue(await self.adapter.evaluate({'predicate': 'count_equals', 'target': 'result_transaction', 'expected': 0}))


class TransformTests(unittest.TestCase):
    def test_strict_money_and_calendar_parsing(self):
        self.assertEqual(transform('-$1,234.50', 'usd_amount'), '-1234.50')
        self.assertEqual(transform('12-11-2025', 'date_mm_dd_yyyy'), '2025-12-11')
        for value, kind in [('02-30-2025', 'date_mm_dd_yyyy'), ('$1,23.00', 'usd_amount'),
                            ('300', 'usd_amount'), ('pending', 'credit_debit')]:
            with self.subTest(kind=kind), self.assertRaises(UIError): transform(value, kind)
