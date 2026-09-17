"""Real-browser replay tests. No model imports or credentials are required."""
import asyncio
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from automation.browser import BrowserAdapter
from automation.errors import UIError
from automation.registry import load_registered
from automation.replay import Replay, invocation_for
from tools.validate_contracts import load, validate, ROOT


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cap,self.profile,_=load_registered()
        self.adapter=BrowserAdapter(self.cap,self.profile,{'account_id':'12345','transaction_id':'12256'},
                                    evidence_root=self.temp.name,evidence_source='deterministic_replay')
        await self.adapter.__aenter__()
        self.addAsyncCleanup(self.adapter.__aexit__,None,None,None)
        await self.adapter.login('john','demo')

    async def run_replay(self, **kwargs):
        self.runner=Replay(self.adapter,**kwargs)
        result=await self.runner.run(invocation_for(self.adapter))
        validate(result,self.cap,self.profile)
        return result

    async def test_different_input_typed_outputs_and_no_data_in_evidence(self):
        result=await self.run_replay()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['outputs']['transaction_id'],'12256')
        self.assertEqual(result['outputs']['amount'],'100.00')
        self.assertEqual(result['outputs']['type'],'debit')
        self.assertEqual(self.runner.actions,9)
        content=''.join(p.read_text() for p in self.adapter.evidence.directory.iterdir())
        for value in ('12345','12256','100.00','Check #','John Smith'):
            self.assertNotIn(value,content)
        self.assertEqual(load(self.adapter.evidence.directory/'replay-result.json')['model_calls'],0)

    async def test_different_account_absent_is_business_outcome(self):
        self.adapter.inputs['account_id']='999999999'
        result=await self.run_replay()
        self.assertEqual(result['status'],'business_outcome')
        self.assertEqual(result['code'],'account_not_available')
        self.assertEqual(self.runner.actions,1)

    async def test_different_valid_account_and_transaction(self):
        self.adapter.inputs.update(account_id='12678',transaction_id='12367')
        result=await self.run_replay()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['outputs']['account_id'],'12678')
        self.assertEqual(result['outputs']['transaction_id'],'12367')

    async def test_transaction_in_another_account_is_not_success(self):
        self.adapter.inputs['transaction_id']='12367'
        result=await self.run_replay()
        self.assertEqual(result['status'],'business_outcome')
        self.assertEqual(result['code'],'transaction_not_found_in_account')

    async def test_missing_transaction_is_business_outcome(self):
        self.adapter.inputs['transaction_id']='999999999'
        result=await self.run_replay()
        self.assertEqual(result['status'],'business_outcome')
        self.assertEqual(result['code'],'transaction_not_found_in_account')
        self.assertEqual(self.runner.actions,3)

    async def test_failed_load_never_becomes_business_outcome(self):
        await self.adapter.page.route('**/accounts/12345/transactions/**',lambda route:route.fulfill(status=500,body='PRIVATE-CANARY'))
        result=await self.run_replay()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'app_error')
        self.assertEqual(self.runner.classification,'hard_failure')

    async def test_delayed_load_succeeds_without_repeating_click(self):
        async def slow(route):
            await asyncio.sleep(0.3)
            await route.continue_()
        await self.adapter.page.route('**/accounts/12345/transactions/**',slow)
        result=await self.run_replay()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.runner.actions,9)

    async def test_session_expiry_cli_is_recoverable_but_not_falsely_paused(self):
        await self.adapter.page.get_by_role('link',name='Log Out',exact=True).click()
        await self.adapter.page.locator('input[type="password"]').wait_for(state='visible')
        result=await self.run_replay()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'session_expired')
        self.assertEqual(self.runner.classification,'recoverable_condition')
        self.assertNotIn('intervention',result)

    async def test_live_caller_can_pause_and_cede_ownership(self):
        await self.adapter.page.get_by_role('link',name='Log Out',exact=True).click()
        await self.adapter.page.locator('input[type="password"]').wait_for(state='visible')
        result=await self.run_replay(allow_pause=True)
        self.assertEqual(result['status'],'paused')
        self.assertEqual(result['intervention']['session_id'],self.adapter.session_id)
        self.assertEqual(self.adapter.owner,'human')
        self.assertFalse(self.adapter.page.is_closed())

    async def test_policy_block_is_hard_even_when_intervention_requested(self):
        await self.adapter.page.locator('#accountTable tbody a').first.evaluate(
            'e => { e.setAttribute("download", "private"); }')
        result=await self.run_replay(allow_pause=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'policy_denied')
        self.assertEqual(self.runner.classification,'hard_failure')
        self.assertEqual(self.adapter.owner,'automation')

    async def test_bad_pin_and_invalid_input_rejected_before_dispatch(self):
        runner=Replay(self.adapter)
        invocation=invocation_for(self.adapter)
        invocation['capability']['sha256']='0'*64
        result=await runner.run(invocation)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'invalid_input')
        self.assertEqual(runner.actions,0)

    async def test_runtime_inputs_cannot_change_after_invocation(self):
        invocation=invocation_for(self.adapter)
        self.adapter.inputs['transaction_id']='12145'
        runner=Replay(self.adapter)
        result=await runner.run(invocation)
        self.assertEqual(result['error']['code'],'policy_denied')
        self.assertEqual(runner.actions,0)

    async def test_action_budget_and_no_accidental_second_run(self):
        runner=Replay(self.adapter)
        invocation=invocation_for(self.adapter,max_actions=1)
        result=await runner.run(invocation)
        self.assertEqual(result['error']['code'],'limit_exceeded')
        self.assertEqual(runner.actions,1)
        with self.assertRaises(UIError): await runner.run(invocation)

    async def test_wait_retry_is_bounded_and_click_is_never_retried(self):
        self.adapter.capability['steps'][0]['retry']['max_attempts']=2
        original=self.adapter.perform
        attempts=0
        async def transient_wait(action,*args,**kwargs):
            nonlocal attempts
            if action=={'type':'wait','target':'overview'}:
                attempts+=1
                if attempts==1: raise UIError('load_timeout')
            return await original(action,*args,**kwargs)
        with patch.object(self.adapter,'perform',side_effect=transient_wait):
            runner=Replay(self.adapter)
            result=await runner.run(invocation_for(self.adapter))
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(attempts,2)
        self.assertEqual(runner.actions,10)

    async def test_exhausted_wait_retries_share_one_deadline(self):
        import time
        step = self.adapter.capability['steps'][0]
        step['retry'].update(max_attempts=3, delay_ms=0)
        step['timeout_ms'] = 300
        operations = []
        async def never_ready(action, *args, **kwargs):
            operations.append(action['type'])
            await asyncio.sleep(10)
        start = time.monotonic()
        with patch.object(self.adapter, 'perform', side_effect=never_ready):
            self.runner = Replay(self.adapter)
            result = await self.runner.run(invocation_for(self.adapter))
            validate(result, self.adapter.capability, self.profile)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'load_timeout')
        self.assertEqual(self.runner.classification, 'recoverable_condition')
        self.assertEqual(operations, ['wait'] * 3)
        self.assertLess(time.monotonic() - start, 2)
        self.assertNotIn('outputs', result)

    async def test_uncertain_click_is_never_retried_or_paused(self):
        original=self.adapter.perform
        calls=0
        async def uncertain(action,*args,**kwargs):
            nonlocal calls
            if action['type']=='click':
                calls+=1
                raise UIError('indeterminate_action')
            return await original(action,*args,**kwargs)
        with patch.object(self.adapter,'perform',side_effect=uncertain):
            result=await self.run_replay(allow_pause=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'indeterminate_action')
        self.assertEqual(calls,1)

    async def test_unknown_outcome_is_not_a_negative_answer(self):
        original=self.adapter.evaluate
        async def unknown(check):
            if check=={'predicate':'count_equals','target':'account_link','expected':0}:
                return None
            return await original(check)
        with patch.object(self.adapter,'evaluate',side_effect=unknown):
            result=await self.run_replay()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'checkpoint_failed')

    async def test_final_checkpoint_is_required_after_extraction(self):
        original=self.adapter.perform
        async def changed_after_extract(action,*args,**kwargs):
            receipt=await original(action,*args,**kwargs)
            if action['type']=='extract':
                await self.adapter.page.locator('tr:has(td:first-child > b:text-is("Transaction ID:")) > td:nth-child(2)').evaluate(
                    'e => { e.textContent="999999999"; }')
            return receipt
        # A short test-only checkpoint timeout keeps this negative case bounded.
        self.adapter.capability['steps'][-1]['timeout_ms']=200
        with patch.object(self.adapter,'perform',side_effect=changed_after_extract):
            runner=Replay(self.adapter)
            result=await runner.run(invocation_for(self.adapter))
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('outputs',result)

    async def test_wall_clock_limit(self):
        runner=Replay(self.adapter)
        result=await runner.run(invocation_for(self.adapter,max_duration_ms=1))
        self.assertEqual(result['status'],'failed')
        self.assertIn(result['error']['code'],{'limit_exceeded','indeterminate_action'})

    async def test_pre_cancelled_run_never_dispatches(self):
        cancel=asyncio.Event(); cancel.set()
        result=await self.run_replay(cancel_event=cancel)
        self.assertEqual(result['error']['code'],'limit_exceeded')
        self.assertEqual(self.runner.actions,0)


class IsolationTests(unittest.TestCase):
    def test_replay_has_no_model_import_dependency(self):
        code="""
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname in {'openai','automation.model','automation.discovery'}:
   raise AssertionError('Model dependency imported')
sys.meta_path.insert(0,Block())
from tools.replay import run_replay
from automation.registry import load_registered
load_registered()
"""
        subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True,capture_output=True)

    def test_registry_rejects_changed_content_under_same_version(self):
        cap,profile,_=load_registered()
        cap['steps'][0]['timeout_ms']=9999
        registry=load(ROOT/'capabilities/registry.json')
        with patch('automation.registry.load',side_effect=[registry,cap,profile]):
            with self.assertRaises(UIError) as caught: load_registered()
        self.assertEqual(caught.exception.code,'policy_denied')
