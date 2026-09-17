"""Verify recorded real discovery, run regressions and isolated live replay, scan evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from tools.check_evidence import scan
from tools.validate_contracts import ROOT, digest, load

DISCOVERY = ROOT/'evidence/discovery/dbce8a9d535c40a4ba91a003792854ea'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence-root', type=Path, default=ROOT/'runs')
    args = p.parse_args()
    directory = args.evidence_root.resolve()/('acceptance-'+uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    report = {'format': 'complete_flow_acceptance_v1', 'passed': False,
              'new_model_calls': 0, 'actual_human_participated': False, 'checks': {}}
    try:
        from tools.verify_discovery import verify
        from automation.registry import load_registered
        report['recorded_discovery'] = verify(DISCOVERY)
        report['discovery_mode'] = 'verify_previous_real_run; regression tests use model doubles'
        cap, profile, _ = load_registered()
        report['checks']['replay_uses_discovered_artifact'] = (
            digest(cap) == digest(load(DISCOVERY/'capability.json')) and
            digest(profile) == digest(load(DISCOVERY/'surface-profile.json')))
        print('Verified real discovery and exact registered artifact pins.', flush=True)
        tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                               cwd=ROOT, capture_output=True, text=True, timeout=600)
        report['checks']['regression_suite'] = tests.returncode == 0
        # Keep only unittest's aggregate count; failures may contain sensitive assertions.
        import re
        match = re.search(r'Ran (\d+) tests? in ([0-9.]+)s', tests.stderr)
        report['regression_tests'] = int(match[1]) if match else None
        report['regression_failures'] = re.findall(r'^(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)', tests.stderr, re.MULTILINE)
        print('Regression suite completed: '+str(report['checks']['regression_suite']), flush=True)
        browser_root = directory/'browser'
        worker = subprocess.run([sys.executable, '-m', 'tools.acceptance_worker',
                                 '--evidence-root', str(browser_root)], cwd=ROOT,
                                capture_output=True, text=True, timeout=300)
        # Only consume machine-readable summaries, never raw errors.
        records = [json.loads(line) for line in worker.stdout.splitlines() if line.startswith('{')]
        report['checks']['isolated_browser_runs'] = (worker.returncode == 0 and len(records) == 12
            and all(r.get('passed', r.get('guard_passed', False)) for r in records))
        report['browser_results'] = records
        secrets = []
        local = ROOT/'.env.local'
        if local.exists():
            for line in local.read_text().splitlines():
                name, sep, value = line.partition('=')
                if sep and name.strip() == 'OPENAI_API_KEY': secrets.append(value.strip().strip('\"\''))
        if os.environ.get('OPENAI_API_KEY'): secrets.append(os.environ['OPENAI_API_KEY'])
        report['evidence_scan'] = {'discovery': scan(DISCOVERY, secrets),
                                   'browser': scan(browser_root, secrets)}
        report['checks']['evidence_redacted'] = all(s['passed'] for s in report['evidence_scan'].values())
        report['passed'] = all(report['checks'].values())
    except Exception:
        report['error'] = 'acceptance_failed_details_redacted'
    with (directory/'report.json').open('x') as file:
        os.chmod(file.name, 0o600)
        json.dump(report, file, indent=2)
        file.write('\n')
    print(json.dumps({'passed': report['passed'], 'report': str(directory/'report.json'),
                      'new_model_calls': 0}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
