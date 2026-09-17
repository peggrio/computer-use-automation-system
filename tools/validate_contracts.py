"""Offline structural and semantic validation; never executes a UI or model call."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'contracts/schema.json').read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def load(path):
    return json.loads(Path(path).read_text())


def digest(document):
    # v1 defines this exact encoding; do not hash a pretty-printed file.
    encoded = json.dumps(document, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def pinned(document):
    return {key: document[key] for key in ('id', 'version')} | {'sha256': digest(document)}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def check_value(value, field, label):
    kind = field['type']
    valid = False
    if kind == 'string':
        valid = isinstance(value, str) and bool(value)
    elif kind == 'identifier':
        valid = isinstance(value, str) and re.fullmatch(r'[0-9]+', value) is not None
    elif kind == 'decimal':
        valid = isinstance(value, str) and re.fullmatch(r'-?(0|[1-9][0-9]*)\.[0-9]{2}', value) is not None
    elif kind == 'date':
        if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            try:
                date.fromisoformat(value)
                valid = True
            except ValueError:
                pass
    elif kind == 'integer':
        valid = type(value) is int
    elif kind == 'boolean':
        valid = type(value) is bool
    elif kind == 'enum':
        valid = isinstance(value, str) and value in field['values']
    require(valid, f'{label}: invalid {kind} value (value redacted)')


def check_fields(values, fields, label):
    require(set(values) == set(fields), f'{label}: missing or undeclared fields')
    for name, field in fields.items():
        check_value(values[name], field, f'{label}.{name}')


def check_references(value, capability):
    for node in walk(value):
        if 'target' in node:
            require(node['target'] in capability['targets'], f"Unknown target: {node['target']}")
        if 'input' in node:
            require(node['input'] in capability['inputs'], f"Unknown input: {node['input']}")


def validate_capability(doc):
    ids = [s['id'] for s in doc['steps']]
    require(len(ids) == len(set(ids)), 'Step IDs must be unique')
    provenance = doc['provenance']
    require((provenance['source'] == 'design_example' and provenance['evidence_run_id'] is None)
            or (provenance['source'] == 'llm_discovery' and bool(provenance['evidence_run_id'])),
            'Discovery requires an evidence run ID; design examples must not claim one')
    check_references(doc['steps'], doc)
    check_references(doc['success_conditions'], doc)
    declared_outcomes = set()
    producers = {}
    for index, step in enumerate(doc['steps']):
        action = step['action']
        if action['type'] != 'wait':
            require(step['retry']['max_attempts'] == 1,
                    f"{step['id']}: only waits may be automatically retried in v1")
        for condition in step['requires'] + step['ensures']:
            if condition['predicate'] == 'ready':
                require(doc['targets'][condition['target']]['purpose'] == 'view',
                        'Readiness must refer to a view')
        if step['outcomes']:
            require(action['type'] == 'wait' and any(
                c['predicate'] == 'ready' and c['target'] == action['target']
                for c in step['ensures']), 'Business outcomes require a successful readiness wait')
        for outcome in step['outcomes']:
            require(outcome['code'] in doc['known_outcomes'], 'Undeclared business outcome')
            declared_outcomes.add(outcome['code'])
        if action['type'] == 'extract':
            require(index == len(doc['steps']) - 1, 'Extraction must be the final step in v1')
            for name, source in action['fields'].items():
                require(name in doc['outputs'], f'Undeclared output: {name}')
                require(name not in producers, f'Duplicate output producer: {name}')
                producers[name] = source
                target_field = doc['outputs'][name]
                if 'literal' in source:
                    check_value(source['literal'], target_field, name)
                elif 'input' in source:
                    input_field = doc['inputs'][source['input']]
                    require(input_field['type'] == target_field['type'], 'Input/output type mismatch')
                    require(input_field.get('values') == target_field.get('values'), 'Input/output enum mismatch')
                    require(not input_field['sensitive'] or target_field['sensitive'],
                            'Cannot declassify a sensitive input')
                else:
                    allowed = {'text': {'identifier', 'string', 'enum'},
                               'date_mm_dd_yyyy': {'date'}, 'usd_amount': {'decimal'},
                               'credit_debit': {'enum'}}
                    require(target_field['type'] in allowed[source['transform']], 'Transform/output type mismatch')
                    if source['transform'] == 'credit_debit':
                        require(set(target_field.get('values', [])) == {'credit', 'debit'},
                                'credit_debit requires credit/debit output enum')
    require(set(producers) == set(doc['outputs']), 'Every output needs exactly one producer')
    require(declared_outcomes == set(doc['known_outcomes']), 'Every known outcome needs a handling rule')
    for condition in doc['success_conditions']:
        if condition['predicate'] == 'ready':
            require(doc['targets'][condition['target']]['purpose'] == 'view', 'Readiness must refer to a view')


def validate_profile(doc, capability):
    require(doc['capability'] == {k: capability[k] for k in ('id', 'version')}, 'Profile/capability version mismatch')
    require(doc['application']['family'] == capability['application']['family'], 'Application family mismatch')
    require(set(doc['bindings']) == set(capability['targets']), 'Profile must bind every target exactly once')
    needed = {n['target'] for n in walk(capability) if n.get('predicate') == 'ready'}
    require(set(doc['readiness']) == needed, 'Profile must define exactly the required readiness rules')
    check_references(doc['bindings'], capability)
    for name, binding in doc['bindings'].items():
        visited = {name}
        parent = binding['scope']
        while parent is not None:
            require(parent in doc['bindings'], f'Unknown scope: {parent}')
            require(parent not in visited, 'Cyclic target scope')
            visited.add(parent)
            parent = doc['bindings'][parent]['scope']
        for locator in binding['candidates']:
            if locator['strategy'] == 'css':
                require('{{' not in locator['selector'] and '${' not in locator['selector'],
                        'Selectors must be static; parameterize through typed filters')


def validate(document, capability=None, profile=None):
    error = next(VALIDATOR.iter_errors(document), None)
    if error:
        # jsonschema messages may include supplied secrets, so never echo them.
        raise ContractError('Invalid structure or unsupported contract version (values redacted)')
    kind = document['kind']
    if kind == 'capability':
        validate_capability(document)
        return
    require(capability is not None, f'{kind} requires capability context')
    validate(capability)
    if kind == 'surface_profile':
        validate_profile(document, capability)
        return
    require(profile is not None, f'{kind} requires profile context')
    validate(profile, capability)
    require(document['capability'] == pinned(capability), 'Capability content pin mismatch')
    require(document['profile'] == pinned(profile), 'Profile content pin mismatch')
    if kind == 'invocation':
        check_fields(document['inputs'], capability['inputs'], 'inputs')
        return
    steps = {s['id']: s for s in capability['steps']}
    status = document['status']
    if status == 'succeeded':
        check_fields(document['outputs'], capability['outputs'], 'outputs')
    elif status == 'business_outcome':
        step = steps.get(document['step_id'])
        require(step is not None and any(o['code'] == document['code'] for o in step['outcomes']),
                'Business outcome is not declared at this step')
    else:
        error_step = document['error']['step_id']
        require(error_step is None or error_step in steps, 'Unknown error step')
        if status == 'paused':
            require(document['intervention']['resume_step_id'] in steps, 'Unknown resume step')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('document', type=Path)
    parser.add_argument('--capability', type=Path)
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    try:
        validate(load(args.document), load(args.capability) if args.capability else None,
                 load(args.profile) if args.profile else None)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, 'Invalid contract: validation failed (details redacted)\n')
    print(f'Valid contract: {args.document.name}')


if __name__ == '__main__':
    main()
