import copy
import unittest

from tools.validate_contracts import ContractError, ROOT, digest, load, pinned, validate


def profile_for(capability):
    # Deliberately synthetic locators: contract test fixture, never a ParaBank adapter.
    views = {key for key, value in capability['targets'].items() if value['purpose'] == 'view'}
    return {
        'kind': 'surface_profile', 'schema_version': '1.0.0',
        'id': 'contract_test_profile', 'version': '0.1.0',
        'capability': {key: capability[key] for key in ('id', 'version')},
        'application': {'family': 'parabank', 'release': 'synthetic-test-only'},
        'adapter': {'name': 'contract_test_adapter', 'contract_version': '1.0.0'},
        'readiness_contract': 'synthetic_readiness_v1',
        'bindings': {name: {'scope': None,
                           'candidates': [{'strategy': 'css', 'selector': f'[data-contract-test="{name}"]'}],
                           'filters': [], 'rationale': 'Synthetic schema test fixture.'}
                     for name in capability['targets']},
        'readiness': {name: {'rule': name + '_complete', 'description': 'Synthetic complete state.'}
                      for name in views},
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.cap = load(ROOT / 'examples/lookup_transaction.capability.json')
        self.profile = profile_for(self.cap)

    def invocation(self):
        return {'kind': 'invocation', 'schema_version': '1.0.0', 'run_id': 'test_run',
                'capability': pinned(self.cap), 'profile': pinned(self.profile),
                'session_id': 'test_session', 'policy_id': 'test_policy',
                'inputs': {'account_id': '12345', 'transaction_id': '12145'},
                'limits': {'max_duration_ms': 120000, 'max_actions': 40}}

    def result(self):
        return {'kind': 'run_result', 'schema_version': '1.0.0', 'run_id': 'test_run',
                'capability': pinned(self.cap), 'profile': pinned(self.profile),
                'evidence_refs': [], 'status': 'succeeded',
                'outputs': {'account_id': '12345', 'transaction_id': '12145',
                            'date': '2025-12-11', 'description': 'Synthetic check',
                            'type': 'credit', 'amount': '300.00', 'currency': 'USD'}}

    def check(self, doc):
        validate(doc, self.cap, self.profile)

    def test_valid_contract_chain(self):
        validate(self.cap)
        validate(self.profile, self.cap)
        self.check(self.invocation())
        self.check(self.result())

    def test_unknown_action_or_executable_field_rejected(self):
        for modification in ({'type': 'evaluate', 'script': 'arbitrary code'},
                             {**self.cap['steps'][0]['action'], 'script': 'arbitrary code'}):
            with self.subTest(modification=modification):
                doc = copy.deepcopy(self.cap)
                doc['steps'][0]['action'] = modification
                with self.assertRaises(ContractError): validate(doc)

    def test_unknown_schema_version_rejected(self):
        self.cap['schema_version'] = '2.0.0'
        with self.assertRaises(ContractError): validate(self.cap)

    def test_undefined_references_rejected(self):
        for key in ('input', 'target'):
            doc = copy.deepcopy(self.cap)
            doc['steps'][-1]['action']['fields']['transaction_id'] = (
                {'input': 'unknown'} if key == 'input' else {'target': 'unknown', 'transform': 'text'})
            with self.subTest(key=key), self.assertRaises(ContractError): validate(doc)

    def test_duplicate_step_id_rejected(self):
        self.cap['steps'][1]['id'] = self.cap['steps'][0]['id']
        with self.assertRaises(ContractError): validate(self.cap)

    def test_missing_output_producer_rejected(self):
        del self.cap['steps'][-1]['action']['fields']['amount']
        with self.assertRaises(ContractError): validate(self.cap)

    def test_transform_must_match_output_type(self):
        self.cap['steps'][-1]['action']['fields']['amount']['transform'] = 'date_mm_dd_yyyy'
        with self.assertRaises(ContractError): validate(self.cap)

    def test_mutation_retry_rejected(self):
        self.cap['steps'][1]['retry']['max_attempts'] = 2
        with self.assertRaises(ContractError): validate(self.cap)

    def test_negative_outcome_requires_readiness(self):
        self.cap['steps'][0]['ensures'] = [{'predicate': 'visible', 'target': 'overview'}]
        with self.assertRaises(ContractError): validate(self.cap)

    def test_unknown_outcome_rejected(self):
        self.cap['steps'][0]['outcomes'][0]['code'] = 'not_declared'
        with self.assertRaises(ContractError): validate(self.cap)

    def test_discovery_requires_provenance(self):
        self.cap['provenance']['source'] = 'llm_discovery'
        with self.assertRaises(ContractError): validate(self.cap)

    def test_profile_completeness_and_readiness(self):
        for key in ('bindings', 'readiness'):
            doc = copy.deepcopy(self.profile)
            del doc[key]['overview']
            with self.subTest(key=key), self.assertRaises(ContractError): validate(doc, self.cap)

    def test_profile_scope_cycle_rejected(self):
        self.profile['bindings']['account_link']['scope'] = 'overview'
        self.profile['bindings']['overview']['scope'] = 'account_link'
        with self.assertRaises(ContractError): validate(self.profile, self.cap)

    def test_selector_interpolation_rejected(self):
        self.profile['bindings']['account_link']['candidates'][0]['selector'] = '#account-${account_id}'
        with self.assertRaises(ContractError): validate(self.profile, self.cap)

    def test_stale_content_pin_rejected(self):
        invocation = self.invocation()
        self.profile['bindings']['overview']['rationale'] = 'Changed profile'
        with self.assertRaises(ContractError): self.check(invocation)

    def test_hash_ignores_key_order(self):
        self.assertEqual(digest(self.cap), digest(dict(reversed(list(self.cap.items())))))

    def test_input_type_and_extra_fields(self):
        for inputs in ({'account_id': 12345, 'transaction_id': '12145'},
                       {'account_id': '12345', 'transaction_id': '12145', 'password': 'DO_NOT_ECHO'}):
            doc = self.invocation()
            doc['inputs'] = inputs
            with self.subTest(inputs=list(inputs)), self.assertRaises(ContractError) as raised:
                self.check(doc)
            self.assertNotIn('DO_NOT_ECHO', str(raised.exception))

    def test_invalid_output_formats_rejected(self):
        for key, value in [('date', '2025-02-30'), ('amount', 300.0),
                           ('type', 'unknown'), ('transaction_id', 'abc')]:
            doc = self.result()
            doc['outputs'][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError): self.check(doc)

    def test_business_outcome_must_belong_to_step(self):
        doc = self.result()
        del doc['outputs']
        doc.update(status='business_outcome', code='account_not_available', step_id='check_account')
        self.check(doc)
        doc['step_id'] = 'open_result'
        with self.assertRaises(ContractError): self.check(doc)

    def test_status_payloads_are_exclusive(self):
        doc = self.result()
        doc['error'] = {'code': 'app_error', 'step_id': None, 'expected': 'Ready',
                        'observed': 'Error', 'evidence_refs': []}
        with self.assertRaises(ContractError): self.check(doc)

    def test_paused_run_requires_human_ownership_fields(self):
        doc = self.result()
        del doc['outputs']
        doc.update(status='paused', error={'code': 'session_expired', 'step_id': 'open_search',
                   'expected': 'Authenticated session', 'observed': 'Login required', 'evidence_refs': []},
                   intervention={'request_id': 'test_request', 'session_id': 'test_session',
                                 'owner': 'human', 'control_epoch': 1,
                                 'resume_step_id': 'open_search', 'resume_mode': 'revalidate'})
        self.check(doc)
        doc['intervention']['owner'] = 'automation'
        with self.assertRaises(ContractError): self.check(doc)


if __name__ == '__main__':
    unittest.main()
