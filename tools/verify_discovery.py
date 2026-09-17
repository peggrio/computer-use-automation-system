"""Offline consistency check for a successful live discovery bundle (no model calls)."""
import argparse
import json
import re
from pathlib import Path

from automation.discovery import compile_capability
from tools.validate_contracts import ROOT, load, digest, validate, ContractError


def verify(directory):
    directory = Path(directory)
    summary = load(directory/'discovery-result.json')
    manifest = load(directory/'discovery-manifest.json')
    base_manifest = load(directory/'manifest.json')
    goal = load(ROOT/'goals/lookup_transaction.json')
    base_profile = load(ROOT/'profiles/parabank_local_browser.json')
    base_cap = load(ROOT/'examples/lookup_transaction.capability.json')
    def require(ok):
        if not ok:
            raise ContractError('Discovery evidence consistency check failed (details redacted)')
    require(summary['status'] == 'succeeded' and summary['contains_model_discovery'] is True)
    require(manifest['provider'] == 'openai_responses' and manifest['scripted_plan_supplied'] is False)
    require(summary['run_id'] == manifest['run_id'] == base_manifest['run_id'] == directory.name)
    require(manifest['goal_sha256'] == digest(goal))
    require(base_manifest['profile_sha256'] == digest(base_profile))
    require(base_manifest['capability_sha256'] == digest(base_cap))
    cap = load(directory/'capability.json')
    profile = load(directory/'surface-profile.json')
    validate(cap)
    validate(profile, cap)
    require(digest(cap) == summary['capability_sha256'] and digest(profile) == summary['profile_sha256'])
    trace, initial, final, tokens = [], None, None, 0
    from jsonschema import Draft202012Validator
    for index in range(summary['model_calls_completed']):
        request = load(directory/f'model-request-{index:03d}.json')
        response = load(directory/f'model-response-{index:03d}.json')
        require(digest(request['context']) == response['request_sha256'])
        require(digest(request['schema']) == manifest['decision_schema_sha256'])
        require(Draft202012Validator(request['schema']).is_valid(response['decision']))
        receipt = response['receipt']
        require(bool(re.fullmatch(r'resp_[a-zA-Z0-9_-]{1,200}',receipt['response_id'])))
        require(bool(re.fullmatch(r'gpt-5\.4-mini(?:-[0-9-]+)?',receipt['model'])))
        tokens += receipt['input_tokens'] + receipt['output_tokens']
        state = request['context']['observation']
        if initial is None: initial = state
        decision = response['decision']
        if decision['operation'] == 'finish':
            require(index == summary['model_calls_completed'] - 1)
            final = state
        else:
            entry = load(directory/f'execution-{index:03d}.json')
            expected = {'type':decision['operation'],'target':decision['target']}
            if decision['input'] is not None: expected['value']={'input':decision['input']}
            require(entry['action'] == expected and entry['before'] == state)
            require(entry['model_response'] == f'model-response-{index:03d}.json')
            if trace: require(trace[-1]['after'] == entry['before'])
            trace.append(entry)
    require(final is not None and trace and trace[-1]['after'] == final)
    require(tokens == summary['total_tokens'])
    compiled, compiled_profile = compile_capability(goal, base_profile, initial, trace, final,
                                                    summary['run_id'],cap['version'])
    require(compiled == cap and compiled_profile == profile)
    return {'status':'verified','model_calls':summary['model_calls_completed'],'total_tokens':tokens,
            'recorded_ui_actions':len(trace),'compiled_steps':len(cap['steps'])}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(verify(args.directory)))
    except (ContractError,OSError,ValueError,KeyError,TypeError):
        p.exit(1,'Evidence verification failed (details redacted)\n')
