"""Labeled local replay acceptance fixtures. Uses the real pinned artifact, never a model."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from tools.replay import parser as replay_parser, run_replay


async def load_failure(adapter):
    await adapter.page.route('**/accounts/12345/transactions/**',
                             lambda route:route.fulfill(status=500,body='Injected replay test failure'))


async def expired(adapter):
    await adapter.page.get_by_role('link',name='Log Out',exact=True).click()
    await adapter.page.locator('input[type="password"]').wait_for(state='visible')


async def blocked(adapter):
    await adapter.page.locator('#accountTable tbody a').first.evaluate('e => e.setAttribute("download","fixture")')


async def delayed(adapter):
    async def slow(route):
        await asyncio.sleep(0.35)
        await route.continue_()
    await adapter.page.route('**/accounts/12345/transactions/**',slow)


async def main(root, scenarios=None):
    # These values are public synthetic ParaBank fixtures, not persisted invocation data.
    cases=[('different_transaction','12345','12256',None,'succeeded',None),
           ('different_account_and_transaction','12678','12367',None,'succeeded',None),
           ('transaction_belongs_elsewhere','12345','12367',None,'business_outcome','transaction_not_found_in_account'),
           ('different_existing_account','12456','12256',None,'business_outcome','transaction_not_found_in_account'),
           ('account_absent','999999999','12256',None,'business_outcome','account_not_available'),
           ('transaction_absent','12345','999999999',None,'business_outcome','transaction_not_found_in_account'),
           ('slow_load','12345','12256',delayed,'succeeded',None),
           ('session_expired','12345','12256',expired,'failed','session_expired'),
           ('load_failed','12345','12256',load_failure,'failed','app_error'),
           ('policy_block','12345','12256',blocked,'failed','policy_denied')]
    for name,account,transaction,fixture,status,code in cases:
        if scenarios and name not in scenarios: continue
        args=replay_parser().parse_args(['--account',account,'--transaction',transaction,'--evidence-root',root])
        result,classification,directory=await run_replay(args,fixture=fixture,fixture_name=name if fixture else None)
        actual_code=result.get('code',result.get('error',{}).get('code'))
        checks={'status_matches':result['status']==status,'code_matches':actual_code==code}
        if status=='succeeded':
            checks.update({'account_matches':result.get('outputs',{}).get('account_id')==account,
                           'transaction_matches':result.get('outputs',{}).get('transaction_id')==transaction})
            if transaction=='12256':
                checks.update({'amount_matches':result.get('outputs',{}).get('amount')=='100.00',
                               'type_matches':result.get('outputs',{}).get('type')=='debit'})
        record={'format':'replay_acceptance_v1','fixture':name,'source':'test_harness',
                'checks':checks,'passed':all(checks.values()),'model_calls':0}
        if directory:
            fd=os.open(Path(directory)/'acceptance.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as file: json.dump(record,file,indent=2); file.write('\n')
        print(json.dumps({'scenario':name,'status':result['status'],'classification':classification,
                          'passed':record['passed'],'evidence_directory':directory}),flush=True)
        if not record['passed']:
            raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence-root',default='runs')
    p.add_argument('--scenario',action='append',choices=['different_transaction','different_account_and_transaction',
        'transaction_belongs_elsewhere','different_existing_account','account_absent','transaction_absent',
        'slow_load','session_expired','load_failed','policy_block'])
    args=p.parse_args()
    asyncio.run(main(args.evidence_root,args.scenario))
