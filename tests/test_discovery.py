"""Discovery regressions. Scripted deciders here are explicitly test doubles, never live evidence."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from automation.browser import BrowserAdapter
from automation.discovery import Discovery, Limits, compile_capability
from automation.model import DiscoveryError, ModelReply, OpenAIModel, local_config
from tools._evidence import scan
from tools.validate_contracts import ROOT, load, validate


def choice(operation, target=None, input=None):
    return {'operation': operation, 'target': target, 'input': input, 'reason': 'navigate'}


class FakeModel:
    source = 'test_double'
    def __init__(self, choices, delay=0):
        self.choices = iter(choices)
        self.contexts = []
        self.delay = delay
    async def decide(self, instructions, context, schema, timeout):
        self.contexts.append(copy.deepcopy(context))
        await asyncio.sleep(self.delay)
        return ModelReply(next(self.choices), {'untrusted': 'PRIVATE-CANARY'})


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.goal = load(ROOT / 'goals/lookup_transaction.json')
        self.cap = load(ROOT / 'examples/lookup_transaction.capability.json')
        self.profile = load(ROOT / 'profiles/parabank_local_browser.json')
        self.adapter = BrowserAdapter(self.cap, self.profile, {'account_id':'12345','transaction_id':'12145'},
                                      evidence_root=self.temp.name, evidence_source='discovery_attempt')
        await self.adapter.__aenter__()
        self.addAsyncCleanup(self.adapter.__aexit__, None, None, None)
        await self.adapter.login('john','demo')

    def runner(self, decisions, **kwargs):
        self.model = FakeModel(decisions)
        return Discovery(self.adapter, self.model, self.goal, **kwargs)

    async def test_model_selected_order_compiles_and_redacts(self):
        # Deliberately fill before select, opposite to the design example's order.
        runner = self.runner([choice('click','account_link'), choice('click','find_transactions'),
                              choice('fill','transaction_input','transaction_id'),
                              choice('select','account_select','account_id'), choice('click','search_by_id'),
                              choice('click','result_transaction'), choice('finish')])
        await self.adapter.page.locator('body').evaluate("e => { const p=document.createElement('p'); p.textContent='PRIVATE-CANARY@example.test'; e.append(p); }")
        initial = await runner.settled_state()
        result = await runner.run()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['outputs']['transaction_id'],'12145')
        self.assertNotIn('capability',result)  # A test double cannot generate claimed live evidence.
        cap, profile = compile_capability(self.goal,self.profile,initial,runner.trace,
                                          await runner.state(),'unit_test_only','0.2.0')
        validate(cap)
        validate(profile,cap)
        actions=[s['action']['type'] for s in cap['steps'] if s['action']['type'] in {'select','fill'}]
        self.assertEqual(actions,['fill','select'])
        self.assertNotIn('12345',json.dumps(cap))
        self.assertNotIn('12145',json.dumps(cap))
        # Scan parsed values so random hashes and timestamp digits cannot cause false positives.
        self.assertTrue(scan(self.adapter.evidence.directory)['passed'])
        for context in self.model.contexts:
            self.assertNotIn('steps',context)
            self.assertNotIn('PRIVATE-CANARY',json.dumps(context))
        summary=load(self.adapter.evidence.directory/'discovery-result.json')
        self.assertFalse(summary['contains_model_discovery'])

    async def test_premature_finish_never_saves_capability(self):
        result=await self.runner([choice('finish')]).run()
        self.assertEqual(result['code'],'completion_not_verified')
        self.assertFalse((self.adapter.evidence.directory/'capability.json').exists())

    async def test_step_limit(self):
        result=await self.runner([choice('wait','overview')]*2,limits=Limits(max_steps=2)).run()
        self.assertEqual(result['code'],'step_limit_exceeded')
        self.assertEqual(len(self.model.contexts),2)

    async def test_wall_clock_limit(self):
        runner=self.runner([choice('finish')],limits=Limits(max_seconds=1))
        self.model.delay=2
        result=await runner.run()
        self.assertEqual(result['code'],'time_limit_exceeded')

    async def test_token_limit_prevents_call(self):
        result=await self.runner([choice('finish')],limits=Limits(max_tokens=1024)).run()
        self.assertEqual(result['code'],'token_limit_exceeded')
        self.assertEqual(len(self.model.contexts),0)

    async def test_unlisted_action_blocked(self):
        result=await self.runner([choice('click','detail_amount')]).run()
        self.assertEqual(result['code'],'policy_denied')
        self.assertEqual(self.adapter._actions,0)

    async def test_model_extra_fields_never_persist(self):
        decision=choice('finish')|{'rationale':'PRIVATE-CANARY'}
        result=await self.runner([decision]).run()
        self.assertEqual(result['code'],'invalid_model_decision')
        self.assertNotIn('PRIVATE-CANARY',''.join(p.read_text() for p in self.adapter.evidence.directory.iterdir()))

    async def test_search_requires_explicit_input_application(self):
        result=await self.runner([choice('click','find_transactions'),choice('click','search_by_id')]).run()
        self.assertEqual(result['code'],'precondition_failed')

    async def test_missing_transaction_cannot_be_claimed_success(self):
        self.adapter.inputs['transaction_id']='999999999'
        runner=self.runner([choice('click','account_link'),choice('finish')])
        result=await runner.run()
        self.assertEqual(result['code'],'completion_not_verified')
        self.assertFalse(runner.membership)


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_request_is_structured_bounded_and_not_stored(self):
        from types import SimpleNamespace as NS
        model=OpenAIModel('unit-test-key')
        self.addAsyncCleanup(model.close)
        schema={'type':'object','properties':{'operation':{'const':'finish'}},'required':['operation'],'additionalProperties':False}
        response=NS(status='completed',output=[],output_text='{"operation":"finish"}',
                    id='resp_unit_test',model='gpt-5.4-mini',usage=NS(input_tokens=40,output_tokens=10))
        model.client.responses.create=AsyncMock(return_value=response)
        reply=await model.decide('instructions',{'safe':True},schema,2)
        self.assertEqual(reply.receipt['input_tokens'],40)
        kwargs=model.client.responses.create.call_args.kwargs
        self.assertFalse(kwargs['store'])
        self.assertEqual(kwargs['timeout'],2)
        self.assertTrue(kwargs['text']['format']['strict'])
        self.assertEqual(kwargs['model'],'gpt-5.4-mini')
        self.assertNotIn('unit-test-key',json.dumps(kwargs))
        response.status='incomplete'
        with self.assertRaises(DiscoveryError) as caught:
            await model.decide('',{},schema,2)
        self.assertEqual(caught.exception.code,'model_incomplete')

    def test_local_config_is_data_not_shell(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'.env.local'
            p.write_text("OPENAI_API_KEY='$(touch SHOULD_NOT_EXIST)'\nOPENAI_MODEL='gpt-5.4-mini'\n")
            self.assertEqual(local_config(p)['OPENAI_API_KEY'],'$(touch SHOULD_NOT_EXIST)')
            p.write_text('UNKNOWN=private\n')
            with self.assertRaises(DiscoveryError): local_config(p)

    def test_limits_reject_unbounded_settings(self):
        for kwargs in ({'max_steps':0},{'max_seconds':float('inf')},{'max_tokens':0}):
            with self.assertRaises(DiscoveryError): Limits(**kwargs)
