"""Explicit adapter smoke walkthrough, not a replay engine or LLM discovery run."""
import argparse
import asyncio
import json
import os

from automation.browser import BrowserAdapter
from automation.errors import UIError
from tools.validate_contracts import ROOT, load


async def act(adapter, kind, target, value=None):
    action = {'type': kind, 'target': target}
    if value is not None:
        action['value'] = value
    observation = await adapter.observe()
    resolution = await adapter.resolve(target, observation)
    return await adapter.perform(action, resolution, epoch=adapter.epoch)


async def walkthrough(adapter):
    """Fixed test scenario using only adapter methods; no artifact step interpreter."""
    await adapter.wait({'predicate': 'ready', 'target': 'overview'})
    if await adapter.evaluate({'predicate': 'count_equals', 'target': 'account_link', 'expected': 0}):
        return {'status': 'business_outcome', 'code': 'account_not_available'}
    await act(adapter, 'click', 'account_link')
    await adapter.wait({'predicate': 'ready', 'target': 'activity'})
    if await adapter.evaluate({'predicate': 'count_equals', 'target': 'account_transaction', 'expected': 0}):
        return {'status': 'business_outcome', 'code': 'transaction_not_found_in_account'}
    if not await adapter.evaluate({'predicate': 'count_equals', 'target': 'account_transaction', 'expected': 1}):
        raise UIError('target_ambiguous', 'account_transaction')
    await act(adapter, 'click', 'find_transactions')
    await adapter.wait({'predicate': 'ready', 'target': 'search'})
    await act(adapter, 'select', 'account_select', {'input': 'account_id'})
    await act(adapter, 'fill', 'transaction_input', {'input': 'transaction_id'})
    await act(adapter, 'click', 'search_by_id')
    await adapter.wait({'predicate': 'ready', 'target': 'results'})
    await act(adapter, 'click', 'result_transaction')
    await adapter.wait({'predicate': 'ready', 'target': 'details'})
    # Exercise the adapter's declared extraction operation without interpreting workflow steps.
    extraction = adapter.capability['steps'][-1]['action']
    receipt = await adapter.perform(extraction, epoch=adapter.epoch)
    return {'status': 'succeeded', 'outputs': receipt['outputs']}


async def main(args):
    capability = load(ROOT / 'examples/lookup_transaction.capability.json')
    profile = load(ROOT / 'profiles/parabank_local_browser.json')
    async with BrowserAdapter(capability, profile,
                              {'account_id': args.account, 'transaction_id': args.transaction},
                              headless=not args.headed, timezone_id=args.timezone, evidence_root=args.evidence_root) as adapter:
        await adapter.login(os.environ.get('PARABANK_USERNAME', 'john'), os.environ.get('PARABANK_PASSWORD', 'demo'))
        if args.inject_failure:
            await adapter.page.route('**/accounts/12345/transactions/**',
                                     lambda route: route.fulfill(status=500, body='Injected test failure'))
        if args.export_draft:
            adapter.evidence.export_capability(capability, capability)
        print(json.dumps({'evidence_directory': str(adapter.evidence.directory)}))
        result = await walkthrough(adapter)
        # Print a safe summary; returned bank data stays in memory, not in logs.
        print(json.dumps({'check': 'scripted_adapter_smoke', 'status': result['status'],
                          'code': result.get('code'), 'output_fields': sorted(result.get('outputs', {}))}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', default='12345')
    parser.add_argument('--transaction', default='12145')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--timezone', default='UTC')
    parser.add_argument('--evidence-root', default=None, help='Default: ignored runs/ directory')
    parser.add_argument('--inject-failure', action='store_true', help='Inject a local test-only activity load failure')
    parser.add_argument('--export-draft', action='store_true', help='Export a sanitized, non-executable capability candidate for review')
    try:
        asyncio.run(main(parser.parse_args()))
    except UIError as error:
        parser.exit(1, f'UI check failed: {error.result_code}\n')
