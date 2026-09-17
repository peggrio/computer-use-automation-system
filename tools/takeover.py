"""Run replay with a visible same-session browser and explicit terminal takeover commands."""
import asyncio
import json
import os
import sys
from time import monotonic

from automation.browser import BrowserAdapter
from automation.errors import UIError
from automation.policy import SafetyPolicy
from automation.registry import load_registered, check_registered_policy
from automation.replay import Replay, invocation_for
from automation.takeover import Takeover
from tools.replay import parser as replay_parser
from tools.validate_contracts import ContractError


async def read_command(timeout):
    """Cancelable terminal input: no blocked worker thread survives operator timeout."""
    loop=asyncio.get_running_loop()
    future=loop.create_future()
    def ready():
        line=sys.stdin.readline()
        if not future.done(): future.set_result(line.strip().lower() if line else 'abort')
    loop.add_reader(sys.stdin.fileno(),ready)
    try:
        return await asyncio.wait_for(future,timeout)
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def main(args):
    cap,profile,entry=load_registered(args.capability,args.version)
    check_registered_policy(entry,SafetyPolicy(args.target).config)
    async with BrowserAdapter(cap,profile,{'account_id':args.account,'transaction_id':args.transaction},
                              base_url=args.target,headless=False,evidence_root=args.evidence_root,
                              evidence_source='deterministic_replay') as adapter:
        await adapter.login(os.environ.get('PARABANK_USERNAME','john'),os.environ.get('PARABANK_PASSWORD','demo'))
        runner=Replay(adapter,allow_pause=True)
        handoff=Takeover(runner,max_human_seconds=args.operator_timeout)
        await handoff.install()
        print(json.dumps({'evidence_directory':str(adapter.evidence.directory),'session_id':adapter.session_id}),flush=True)
        try:
            if args.demo_expiry:
                adapter.evidence._write('takeover-fixture.json',{'source':'test_harness','scenario':'ui_logout_before_replay'})
                await adapter.page.get_by_role('link',name='Log Out',exact=True).click()
                await adapter.page.locator('input[type="password"]').wait_for(state='visible')
            result=await runner.run(invocation_for(adapter,max_duration_ms=args.max_duration_ms,max_actions=args.max_actions))
            while result['status']=='paused':
                print(json.dumps(await handoff.begin(result)),flush=True)
                while handoff.active:
                    print('Human owns the browser. Restore Accounts Overview, then type resume; or type abort. Do not type credentials here.',flush=True)
                    try:
                        command=await read_command(max(0.01,handoff.deadline-monotonic()))
                    except TimeoutError:
                        if handoff._watchdog:
                            await handoff._watchdog
                        result=handoff.terminal_result
                        break
                    if not handoff.active:
                        if handoff._watchdog:
                            await handoff._watchdog
                        result=handoff.terminal_result
                        break
                    if command=='abort':
                        result=await handoff.abort(); break
                    if command!='resume':
                        print('Allowed commands: resume, abort.',flush=True); continue
                    ticket=handoff.ticket
                    try:
                        result=await handoff.resume(**{k:ticket[k] for k in ('request_id','session_id','control_epoch')})
                        break
                    except UIError:
                        print('Resume rejected: restore the required entry view in this browser, then try again.',flush=True)
                if handoff.expired: result=handoff.terminal_result
            print(json.dumps({'status':result['status'],'code':result.get('code',result.get('error',{}).get('code')),
                              'output_fields':sorted(result.get('outputs',{})),'model_calls':0}),flush=True)
            return 0 if result['status'] in {'succeeded','business_outcome'} else 1
        finally:
            await handoff.close()


if __name__=='__main__':
    p=replay_parser()
    p.description=__doc__
    p.add_argument('--demo-expiry',action='store_true',help='Deliberately log out through the UI to demonstrate recovery')
    p.add_argument('--operator-timeout',type=int,default=300)
    try:
        raise SystemExit(asyncio.run(main(p.parse_args())))
    except (UIError,ContractError,OSError,ValueError):
        p.exit(1,'Takeover stopped safely (details redacted).\n')
