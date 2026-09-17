"""Replay a pinned local capability with no model SDK, API key, or model decisions."""
import argparse
import asyncio
import json
import os
from uuid import uuid4

from automation.browser import BrowserAdapter
from automation.errors import UIError
from automation.registry import load_registered, check_registered_policy
from automation.replay import Replay, invocation_for
from automation.policy import SafetyPolicy
from tools.validate_contracts import ContractError, check_fields, pinned, validate


async def run_replay(args, *, fixture=None, fixture_name=None):
    cap, profile, entry = load_registered(args.capability, args.version)
    inputs = {'account_id':args.account,'transaction_id':args.transaction}
    try:
        check_fields(inputs,cap['inputs'],'inputs')
        if not (1 <= args.max_duration_ms <= 300000 and 1 <= args.max_actions <= 1000):
            raise UIError('invalid_input')
        check_registered_policy(entry, SafetyPolicy(args.target).config)
    except (ContractError,UIError) as error:
        code = error.code if isinstance(error,UIError) else 'invalid_input'
        result={'kind':'run_result','schema_version':'1.0.0','run_id':uuid4().hex,
                'capability':pinned(cap),'profile':pinned(profile),'evidence_refs':[], 'status':'failed',
                'error':{'code':code,'step_id':None,'expected':'Valid inputs and permitted local policy.',
                         'observed':'Preflight rejected; values redacted.','evidence_refs':[]}}
        validate(result,cap,profile)
        return result, 'hard_failure', None
    async with BrowserAdapter(cap,profile,inputs,base_url=args.target,headless=not args.headed,
                              evidence_root=args.evidence_root,evidence_source='deterministic_replay') as adapter:
        evidence = str(adapter.evidence.directory)
        await adapter.login(os.environ.get('PARABANK_USERNAME','john'),os.environ.get('PARABANK_PASSWORD','demo'))
        if fixture is not None:
            adapter.evidence._write('fixture.json',{'source':'test_harness','scenario':fixture_name})
            await fixture(adapter)
        runner=Replay(adapter,allow_pause=False)
        result=await runner.run(invocation_for(adapter,max_duration_ms=args.max_duration_ms,max_actions=args.max_actions))
    return result, runner.classification, evidence


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capability',default='lookup_transaction')
    p.add_argument('--version',default='0.2.0')
    p.add_argument('--account',default='12345')
    p.add_argument('--transaction',default='12256',help='Defaults to a different transaction from discovery')
    p.add_argument('--target',default='http://127.0.0.1:8080')
    p.add_argument('--headed',action='store_true')
    p.add_argument('--evidence-root',default=None)
    p.add_argument('--max-duration-ms',type=int,default=120000)
    p.add_argument('--max-actions',type=int,default=40)
    return p


async def main(args):
    result, classification, evidence=await run_replay(args)
    # Authorized Python callers receive typed outputs; the terminal/evidence get a safe summary.
    summary={key:value for key,value in result.items() if key!='outputs'}
    summary.update({'classification':classification,'output_fields':sorted(result.get('outputs',{})),
                    'evidence_directory':evidence,'model_calls':0})
    print(json.dumps(summary))
    return 0 if result['status'] in {'succeeded','business_outcome'} else 3 if classification=='recoverable_condition' else 1


if __name__=='__main__':
    p=parser()
    try:
        raise SystemExit(asyncio.run(main(p.parse_args())))
    except (UIError,ContractError,OSError,ValueError):
        p.exit(1,'Replay preflight/session setup failed (details redacted)\n')
