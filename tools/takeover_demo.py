"""Labeled simulated operator acceptance check through real browser controls; no model calls."""
import argparse
import asyncio
import json

from automation.browser import BrowserAdapter
from automation.registry import load_registered
from automation.replay import Replay,invocation_for
from automation.takeover import Takeover


async def main(args):
    cap,profile,_=load_registered()
    async with BrowserAdapter(cap,profile,{'account_id':'12345','transaction_id':'12256'},
                              evidence_root=args.evidence_root,evidence_source='deterministic_replay',
                              headless=not args.headed) as adapter:
        await adapter.login('john','demo')
        runner=Replay(adapter,allow_pause=True)
        handoff=Takeover(runner,source='test_harness')
        await handoff.install()
        original_page=adapter.page; original_session=adapter.session_id
        adapter.evidence._write('takeover-fixture.json',{
            'source':'test_harness','scenario':'ui_logout_then_simulated_operator_login_and_explicit_return',
            'actual_human_participated':False})
        try:
            await adapter.page.get_by_role('link',name='Log Out',exact=True).click()
            await adapter.page.locator('input[type="password"]').wait_for(state='visible')
            paused=await runner.run(invocation_for(adapter))
            await handoff.begin(paused)
            # Operator simulation uses UI controls in the existing tab, never adapter.login.
            await adapter.page.locator('input[name="username"]').fill('john')
            await adapter.page.locator('input[name="password"]').fill('demo')
            await adapter.page.get_by_role('button',name='Log In',exact=True).click()
            await adapter.wait({'predicate':'ready','target':'overview'})
            ticket={key:handoff.ticket[key] for key in ('request_id','session_id','control_epoch')}
            result=await handoff.resume(**ticket)
            checks={'paused_before_manual_work':paused['status']=='paused',
                    'same_page':adapter.page is original_page,'same_session':adapter.session_id==original_session,
                    'explicit_epoch_advanced':adapter.epoch>ticket['control_epoch'],
                    'completed':result['status']=='succeeded',
                    'correct_transaction':result.get('outputs',{}).get('transaction_id')=='12256',
                    'correct_amount':result.get('outputs',{}).get('amount')=='100.00',
                    'ownership_returned':adapter.owner=='automation'}
            adapter.evidence._write('takeover-acceptance.json',{
                'source':'test_harness','actual_human_participated':False,'checks':checks,
                'passed':all(checks.values()),'model_calls':0})
            print(json.dumps({'passed':all(checks.values()),'evidence_directory':str(adapter.evidence.directory),
                              'actual_human_participated':False,'model_calls':0}))
            if not all(checks.values()): raise SystemExit(1)
        finally:
            await handoff.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root',default='runs')
    parser.add_argument('--headed',action='store_true')
    asyncio.run(main(parser.parse_args()))
