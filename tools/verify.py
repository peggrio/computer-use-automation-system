"""Verify discovery evidence or run the complete acceptance suite without model calls."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from automation.discovery import compile_capability
from tools._evidence import scan
from tools.validate_contracts import ROOT, ContractError, digest, load, validate

DISCOVERY = ROOT / 'evidence/discovery/dbce8a9d535c40a4ba91a003792854ea'


def verify_discovery(directory):
    directory = Path(directory)
    summary = load(directory / 'discovery-result.json')
    manifest = load(directory / 'discovery-manifest.json')
    base_manifest = load(directory / 'manifest.json')
    goal = load(ROOT / 'goals/lookup_transaction.json')
    base_profile = load(ROOT / 'profiles/parabank_local_browser.json')
    base_cap = load(ROOT / 'examples/lookup_transaction.capability.json')

    def require(ok):
        if not ok:
            raise ContractError('Discovery evidence consistency check failed (details redacted)')

    require(summary['status'] == 'succeeded' and summary['contains_model_discovery'] is True)
    require(manifest['provider'] == 'openai_responses' and manifest['scripted_plan_supplied'] is False)
    require(summary['run_id'] == manifest['run_id'] == base_manifest['run_id'] == directory.name)
    require(manifest['goal_sha256'] == digest(goal))
    require(base_manifest['profile_sha256'] == digest(base_profile))
    require(base_manifest['capability_sha256'] == digest(base_cap))
    capability = load(directory / 'capability.json')
    profile = load(directory / 'surface-profile.json')
    validate(capability)
    validate(profile, capability)
    require(digest(capability) == summary['capability_sha256'])
    require(digest(profile) == summary['profile_sha256'])

    from jsonschema import Draft202012Validator

    trace = []
    initial = final = None
    tokens = 0
    for index in range(summary['model_calls_completed']):
        request = load(directory / f'model-request-{index:03d}.json')
        response = load(directory / f'model-response-{index:03d}.json')
        require(digest(request['context']) == response['request_sha256'])
        require(digest(request['schema']) == manifest['decision_schema_sha256'])
        require(Draft202012Validator(request['schema']).is_valid(response['decision']))
        receipt = response['receipt']
        require(bool(re.fullmatch(r'resp_[a-zA-Z0-9_-]{1,200}', receipt['response_id'])))
        require(bool(re.fullmatch(r'gpt-5\.4-mini(?:-[0-9-]+)?', receipt['model'])))
        tokens += receipt['input_tokens'] + receipt['output_tokens']
        state = request['context']['observation']
        if initial is None:
            initial = state
        decision = response['decision']
        if decision['operation'] == 'finish':
            require(index == summary['model_calls_completed'] - 1)
            final = state
        else:
            entry = load(directory / f'execution-{index:03d}.json')
            expected = {'type': decision['operation'], 'target': decision['target']}
            if decision['input'] is not None:
                expected['value'] = {'input': decision['input']}
            require(entry['action'] == expected and entry['before'] == state)
            require(entry['model_response'] == f'model-response-{index:03d}.json')
            if trace:
                require(trace[-1]['after'] == entry['before'])
            trace.append(entry)
    require(final is not None and trace and trace[-1]['after'] == final)
    require(tokens == summary['total_tokens'])
    compiled, compiled_profile = compile_capability(
        goal, base_profile, initial, trace, final, summary['run_id'], capability['version']
    )
    require(compiled == capability and compiled_profile == profile)
    return {
        'status': 'verified',
        'model_calls': summary['model_calls_completed'],
        'total_tokens': tokens,
        'recorded_ui_actions': len(trace),
        'compiled_steps': len(capability['steps']),
    }


def run_acceptance(evidence_root):
    directory = evidence_root.resolve() / ('acceptance-' + uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    report = {
        'format': 'complete_flow_acceptance_v1', 'passed': False,
        'new_model_calls': 0, 'actual_human_participated': False, 'checks': {},
    }
    try:
        from automation.registry import load_registered

        report['recorded_discovery'] = verify_discovery(DISCOVERY)
        report['discovery_mode'] = 'verify_previous_real_run; regression tests use model doubles'
        capability, profile, _ = load_registered()
        report['checks']['replay_uses_discovered_artifact'] = (
            digest(capability) == digest(load(DISCOVERY / 'capability.json'))
            and digest(profile) == digest(load(DISCOVERY / 'surface-profile.json'))
        )
        print('Verified real discovery and exact registered artifact pins.', flush=True)
        tests = subprocess.run(
            [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
        )
        report['checks']['regression_suite'] = tests.returncode == 0
        match = re.search(r'Ran (\d+) tests? in ([0-9.]+)s', tests.stderr)
        report['regression_tests'] = int(match[1]) if match else None
        report['regression_failures'] = re.findall(
            r'^(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)', tests.stderr, re.MULTILINE
        )
        print('Regression suite completed: ' + str(report['checks']['regression_suite']), flush=True)
        browser_root = directory / 'browser'
        worker = subprocess.run(
            [sys.executable, '-m', 'tools._acceptance_worker', '--evidence-root', str(browser_root)],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        records = [json.loads(line) for line in worker.stdout.splitlines() if line.startswith('{')]
        report['checks']['isolated_browser_runs'] = (
            worker.returncode == 0 and len(records) == 12
            and all(record.get('passed', record.get('guard_passed', False)) for record in records)
        )
        report['browser_results'] = records
        secrets = []
        local = ROOT / '.env.local'
        if local.exists():
            for line in local.read_text().splitlines():
                name, separator, value = line.partition('=')
                if separator and name.strip() == 'OPENAI_API_KEY':
                    secrets.append(value.strip().strip('"\''))
        if os.environ.get('OPENAI_API_KEY'):
            secrets.append(os.environ['OPENAI_API_KEY'])
        report['evidence_scan'] = {
            'discovery': scan(DISCOVERY, secrets),
            'browser': scan(browser_root, secrets),
        }
        report['checks']['evidence_redacted'] = all(
            result['passed'] for result in report['evidence_scan'].values()
        )
        report['passed'] = all(report['checks'].values())
    except Exception:
        report['error'] = 'acceptance_failed_details_redacted'
    with (directory / 'report.json').open('x') as file:
        os.chmod(file.name, 0o600)
        json.dump(report, file, indent=2)
        file.write('\n')
    print(json.dumps({
        'passed': report['passed'], 'report': str(directory / 'report.json'), 'new_model_calls': 0,
    }), flush=True)
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    discovery = subparsers.add_parser('discovery', help='Verify a saved discovery bundle')
    discovery.add_argument('directory', type=Path)
    acceptance = subparsers.add_parser('acceptance', help='Run regression and browser acceptance checks')
    acceptance.add_argument('--evidence-root', type=Path, default=ROOT / 'runs')
    args = parser.parse_args()
    try:
        if args.command == 'discovery':
            print(json.dumps(verify_discovery(args.directory)))
            return 0
        return run_acceptance(args.evidence_root)
    except (ContractError, OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'Verification failed (details redacted)\n')


if __name__ == '__main__':
    raise SystemExit(main())
