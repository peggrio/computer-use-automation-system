"""Genuine model discovery through the isolated local ParaBank browser adapter."""
import argparse
import asyncio
import json

from automation.browser import BrowserAdapter
from automation.discovery import Discovery, Limits
from automation.errors import UIError
from automation.model import OpenAIModel, DiscoveryError, local_config
from tools.validate_contracts import ROOT, load, ContractError


async def main(args):
    config = local_config(args.config)
    model = OpenAIModel(config.get('OPENAI_API_KEY'), args.model)
    try:
        goal = load(ROOT / 'goals/lookup_transaction.json')
        # Only the reviewed contract/profile are reused. No example steps enter the model prompt.
        cap = load(ROOT / 'examples/lookup_transaction.capability.json')
        profile = load(ROOT / 'profiles/parabank_local_browser.json')
        async with BrowserAdapter(cap, profile, {'account_id': args.account, 'transaction_id': args.transaction},
                                  base_url=args.target, headless=not args.headed,
                                  evidence_root=args.evidence_root, evidence_source='discovery_attempt') as adapter:
            print(json.dumps({'evidence_directory': str(adapter.evidence.directory)}), flush=True)
            await adapter.login(config.get('PARABANK_USERNAME', 'john'), config.get('PARABANK_PASSWORD', 'demo'))
            discovery = Discovery(adapter, model, goal,
                                  limits=Limits(args.max_steps, args.max_seconds, args.max_tokens, args.call_timeout),
                                  version=args.version)
            result = await discovery.run()
            print(json.dumps({k: v for k, v in result.items() if k != 'outputs'}), flush=True)
            return 0 if result['status'] == 'succeeded' else 1
    finally:
        await model.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--goal', choices=['lookup_transaction'], default='lookup_transaction', help='Reviewed named goal')
    p.add_argument('--target', default='http://127.0.0.1:8080', help='Local ParaBank origin permitted by policy')
    p.add_argument('--model', choices=['gpt-5.4-mini'], default='gpt-5.4-mini')
    p.add_argument('--config', default=str(ROOT / '.env.local'), help='Local dotenv file, never executed as shell code')
    p.add_argument('--account', default='12345')
    p.add_argument('--transaction', default='12145')
    p.add_argument('--version', default='0.2.0', help='New candidate version; does not promote into a registry')
    p.add_argument('--headed', action='store_true')
    p.add_argument('--evidence-root', default=None)
    p.add_argument('--max-steps', type=int, default=20)
    p.add_argument('--max-seconds', type=float, default=180)
    p.add_argument('--max-tokens', type=int, default=60000)
    p.add_argument('--call-timeout', type=float, default=30)
    return p


if __name__ == '__main__':
    p = parser()
    try:
        raise SystemExit(asyncio.run(main(p.parse_args())))
    except (DiscoveryError, UIError) as error:
        p.exit(1, f'Discovery failed: {error.code}\n')
    except (ContractError, OSError, ValueError):
        p.exit(1, 'Discovery failed: invalid_configuration (details redacted)\n')
