"""Same-session operator interaction simulated through real Chromium, never claimed as human evidence."""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from automation.browser import BrowserAdapter
from automation.errors import UIError
from automation.registry import load_registered
from automation.replay import Replay, invocation_for
from automation.takeover import Takeover
from tools.validate_contracts import load,validate


class TakeoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        c,p,_=load_registered(); self.cap,self.profile=c,p
        self.adapter=BrowserAdapter(c,p,{'account_id':'12345','transaction_id':'12256'},evidence_root=self.temp.name)
        await self.adapter.__aenter__(); self.addAsyncCleanup(self.adapter.__aexit__,None,None,None)
        await self.adapter.login('john','demo')
        self.runner=Replay(self.adapter,allow_pause=True)
        self.handoff=Takeover(self.runner,source='test_harness')
        await self.handoff.install(); self.addAsyncCleanup(self.handoff.close)
        self.page=self.adapter.page; self.session=self.adapter.session_id

    async def pause(self):
        await self.adapter.page.get_by_role('link',name='Log Out',exact=True).click()
        await self.adapter.page.locator('input[type="password"]').wait_for(state='visible')
        result=await self.runner.run(invocation_for(self.adapter))
        self.assertEqual(result['status'],'paused')
        await self.handoff.begin(result)
        return result

    def ticket(self):
        return {k:self.handoff.ticket[k] for k in ('request_id','session_id','control_epoch')}

    async def human_login(self):
        # Deliberately use browser controls, NOT adapter.login (which requires automation ownership).
        await self.page.locator('input[name="username"]').fill('john')
        await self.page.locator('input[name="password"]').fill('demo')
        await self.page.get_by_role('button',name='Log In',exact=True).click()
        await self.adapter.wait({'predicate':'ready','target':'overview'})

    async def test_same_session_login_capture_return_and_success(self):
        await self.pause()
        old_epoch=self.adapter.epoch
        await self.human_login()
        result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['outputs']['transaction_id'],'12256')
        self.assertIs(self.adapter.page,self.page)
        self.assertEqual(self.adapter.session_id,self.session)
        self.assertGreater(self.adapter.epoch,old_epoch)
        self.assertEqual(self.adapter.owner,'automation')
        validate(result,self.cap,self.profile)
        records=[load(p) for p in self.adapter.evidence.directory.glob('human-event-*.json')]
        controls={r.get('control') for r in records if r['event']=='manual_action'}
        self.assertIn('login_password',controls)
        self.assertIn('login_username',controls)
        serialized=json.dumps(records)
        for value in ('john','demo','12345','12256'): self.assertNotIn(value,serialized)
        self.assertTrue(all(r['source']=='test_harness' for r in records))
        self.assertTrue((self.adapter.evidence.directory/'replay-result-001.json').exists())

    async def test_resume_before_recovery_keeps_human_owner(self):
        await self.pause()
        with self.assertRaises(UIError): await self.handoff.resume(**self.ticket())
        self.assertTrue(self.handoff.active)
        self.assertEqual(self.adapter.owner,'human')
        await self.human_login()
        self.assertEqual((await self.handoff.resume(**self.ticket()))['status'],'succeeded')

    async def test_stale_foreign_and_duplicate_returns_denied(self):
        await self.pause(); valid=self.ticket()
        for key,value in [('session_id','foreign'),('request_id','foreign'),('control_epoch',0)]:
            with self.assertRaises(UIError): await self.handoff.resume(**(valid|{key:value}))
        await self.human_login()
        self.assertEqual((await self.handoff.resume(**valid))['status'],'succeeded')
        with self.assertRaises(UIError): await self.handoff.resume(**valid)

    async def test_automation_cannot_act_while_human_owns_session(self):
        await self.pause()
        with self.assertRaises(UIError) as caught:
            await self.adapter.perform({'type':'wait','target':'overview'},epoch=0)
        self.assertEqual(caught.exception.code,'control_denied')

    async def test_abort_revokes_resume(self):
        await self.pause(); valid=self.ticket()
        result=await self.handoff.abort()
        self.assertEqual(result['status'],'failed')
        self.assertIsNone(self.runner.pending)
        self.assertFalse(self.adapter._human_login_active)
        with self.assertRaises(UIError): await self.handoff.resume(**valid)

    async def test_human_timeout_revokes_control(self):
        self.handoff.limit=0.1
        await self.pause()
        await asyncio.sleep(0.2)
        # Watchdog may still be writing failure evidence; wait for its bounded task.
        await self.handoff._watchdog
        self.assertTrue(self.handoff.expired)
        self.assertFalse(self.handoff.active)
        self.assertIsNone(self.runner.pending)

    async def test_changed_inputs_cannot_resume(self):
        await self.pause(); await self.human_login()
        self.adapter.inputs['transaction_id']='12145'
        result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'failed')
        self.assertFalse(self.handoff.active)
        self.assertEqual(result['error']['code'],'policy_denied')

    async def test_closed_session_is_not_replaced(self):
        await self.pause()
        await self.page.close()
        result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'failed')
        self.assertIs(self.adapter.page,self.page)
        self.assertIsNone(self.runner.pending)

    async def test_policy_fault_cannot_be_cleared_by_return(self):
        await self.pause()
        try:
            await self.page.goto(self.adapter.policy.origin+'/parabank/transfer.htm')
        except Exception:
            pass
        result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'policy_denied')

    async def test_concurrent_returns_do_not_duplicate_replay(self):
        await self.pause(); await self.human_login()
        ticket=self.ticket()
        results=await asyncio.gather(self.handoff.resume(**ticket),self.handoff.resume(**ticket),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) and r['status']=='succeeded' for r in results),1)
        self.assertEqual(sum(isinstance(r,UIError) for r in results),1)

    async def test_mid_workflow_expiry_restarts_all_membership_checks(self):
        execute=self.runner.execute_step
        expired=False
        async def expire_once(step):
            nonlocal expired
            if step['id']=='step_003' and not expired:
                expired=True
                await self.page.get_by_role('link',name='Log Out',exact=True).click()
                await self.page.locator('input[type="password"]').wait_for(state='visible')
            return await execute(step)
        with patch.object(self.runner,'execute_step',side_effect=expire_once):
            result=await self.runner.run(invocation_for(self.adapter))
            self.assertEqual(result['status'],'paused')
            prior_actions=self.runner.actions
            await self.handoff.begin(result); await self.human_login()
            result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.runner.actions,prior_actions+9)
        self.assertIs(self.adapter.page,self.page)

    async def test_manual_text_is_redacted_before_binding(self):
        await self.pause()
        await self.page.locator('input[name="username"]').fill('PRIVATE-CANARY@example.test')
        await self.handoff.mode('sealed')
        content=''.join(p.read_text() for p in self.adapter.evidence.directory.glob('human-event-*.json'))
        self.assertNotIn('PRIVATE-CANARY',content)

    async def test_total_action_budget_survives_handoff(self):
        await self.page.get_by_role('link',name='Log Out',exact=True).click()
        await self.page.locator('input[type="password"]').wait_for(state='visible')
        result=await self.runner.run(invocation_for(self.adapter,max_actions=1))
        await self.handoff.begin(result); await self.human_login()
        result=await self.handoff.resume(**self.ticket())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.runner.actions,1)
