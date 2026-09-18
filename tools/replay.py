"""Replay a pinned local capability with no model SDK, API key, or model decisions."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from automation.browser import BrowserAdapter
from automation.errors import UIError
from automation.registry import load_registered, check_registered_policy
from automation.replay import Replay, invocation_for
from automation.policy import SafetyPolicy
from tools.validate_contracts import ContractError, check_fields, pinned, validate


async def _load_failure(adapter):
    await adapter.page.route(
        '**/accounts/12345/transactions/**',
        lambda route: route.fulfill(status=500, body='Injected replay test failure'),
    )


async def _expired(adapter):
    await adapter.page.get_by_role('link', name='Log Out', exact=True).click()
    await adapter.page.locator('input[type="password"]').wait_for(state='visible')


async def _blocked(adapter):
    await adapter.page.locator('#accountTable tbody a').first.evaluate(
        'element => element.setAttribute("download", "fixture")'
    )


async def _delayed(adapter):
    async def slow(route):
        await asyncio.sleep(0.35)
        await route.continue_()

    await adapter.page.route('**/accounts/12345/transactions/**', slow)


async def run_replay(args, *, fixture=None, fixture_name=None):
    cap, profile, entry = load_registered(args.capability, args.version)
    inputs = {'account_id':args.account,'transaction_id':args.transaction}
    try:
        check_fields(inputs,cap['inputs'],'inputs')
        if not (1 <= args.max_duration_ms <= 300000 and 1 <= args.max_actions <= 1000
                and 0 <= args.slow_mo_ms <= 5000):
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
                              evidence_root=args.evidence_root,evidence_source='deterministic_replay',
                              slow_mo_ms=args.slow_mo_ms) as adapter:
        evidence = str(adapter.evidence.directory)
        await adapter.login(os.environ.get('PARABANK_USERNAME','john'),os.environ.get('PARABANK_PASSWORD','demo'))
        if fixture is not None:
            adapter.evidence._write('fixture.json',{'source':'test_harness','scenario':fixture_name})
            await fixture(adapter)
        runner=Replay(adapter,allow_pause=False)
        result=await runner.run(invocation_for(adapter,max_duration_ms=args.max_duration_ms,max_actions=args.max_actions))
    return result, runner.classification, evidence


def parser(*, include_demo=True):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capability',default='lookup_transaction')
    p.add_argument('--version',default='0.2.0')
    p.add_argument('--account',default='12345')
    p.add_argument('--transaction',default='12256',help='Defaults to a different transaction from discovery')
    p.add_argument('--target',default='http://127.0.0.1:8080')
    p.add_argument('--headed',action='store_true')
    p.add_argument('--slow-mo-ms', type=int, default=0,
                   help='Delay Playwright operations by 0-5000 ms for visible demos')
    p.add_argument('--evidence-root',default=None)
    p.add_argument('--max-duration-ms',type=int,default=120000)
    p.add_argument('--max-actions',type=int,default=40)
    if include_demo:
        p.add_argument('--demo', action='store_true', help='Run the deterministic replay acceptance scenarios')
        p.add_argument('--scenario', action='append', choices=[
            'different_transaction', 'different_account_and_transaction',
            'transaction_belongs_elsewhere', 'different_existing_account', 'account_absent',
            'transaction_absent', 'slow_load', 'session_expired', 'load_failed', 'policy_block',
        ])
    return p


async def run_demo(root, scenarios=None):
    """Run labeled local fixtures against the real pinned artifact without a model."""
    cases = [
        ('different_transaction', '12345', '12256', None, 'succeeded', None),
        ('different_account_and_transaction', '12678', '12367', None, 'succeeded', None),
        ('transaction_belongs_elsewhere', '12345', '12367', None, 'business_outcome', 'transaction_not_found_in_account'),
        ('different_existing_account', '12456', '12256', None, 'business_outcome', 'transaction_not_found_in_account'),
        ('account_absent', '999999999', '12256', None, 'business_outcome', 'account_not_available'),
        ('transaction_absent', '12345', '999999999', None, 'business_outcome', 'transaction_not_found_in_account'),
        ('slow_load', '12345', '12256', _delayed, 'succeeded', None),
        ('session_expired', '12345', '12256', _expired, 'failed', 'session_expired'),
        ('load_failed', '12345', '12256', _load_failure, 'failed', 'app_error'),
        ('policy_block', '12345', '12256', _blocked, 'failed', 'policy_denied'),
    ]
    for name, account, transaction, fixture, status, code in cases:
        if scenarios and name not in scenarios:
            continue
        args = parser().parse_args([
            '--account', account, '--transaction', transaction, '--evidence-root', str(root),
        ])
        result, classification, directory = await run_replay(
            args, fixture=fixture, fixture_name=name if fixture else None
        )
        actual_code = result.get('code', result.get('error', {}).get('code'))
        checks = {'status_matches': result['status'] == status, 'code_matches': actual_code == code}
        if status == 'succeeded':
            checks.update({
                'account_matches': result.get('outputs', {}).get('account_id') == account,
                'transaction_matches': result.get('outputs', {}).get('transaction_id') == transaction,
            })
            if transaction == '12256':
                checks.update({
                    'amount_matches': result.get('outputs', {}).get('amount') == '100.00',
                    'type_matches': result.get('outputs', {}).get('type') == 'debit',
                })
        record = {
            'format': 'replay_acceptance_v1', 'fixture': name, 'source': 'test_harness',
            'checks': checks, 'passed': all(checks.values()), 'model_calls': 0,
        }
        if directory:
            fd = os.open(Path(directory) / 'acceptance.json',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(record, file, indent=2)
                file.write('\n')
        print(json.dumps({
            'scenario': name, 'status': result['status'], 'classification': classification,
            'passed': record['passed'], 'evidence_directory': directory,
        }), flush=True)
        if not record['passed']:
            return 1
    return 0


async def main(args):
    if args.demo:
        return await run_demo(args.evidence_root or 'runs', args.scenario)
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
