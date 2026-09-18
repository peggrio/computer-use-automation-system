import asyncio
import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from automation.browser import BrowserAdapter
from automation.evidence import EvidenceWriter
from automation.errors import UIError
from automation.policy import DEFAULT_POLICY, SafetyPolicy
from tools._ui import act, walkthrough
from tools.validate_contracts import ROOT, load, validate

CANARY = 'PRIVATE-CANARY-example.person@example.test-999-88-7777-secret-token'


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SafetyPolicy('http://127.0.0.1:8080')

    def test_closed_destinations_methods_and_query_fields(self):
        base = self.policy.origin
        self.assertTrue(self.policy.allows_request(base+'/parabank/activity.htm?id=12345', 'GET'))
        self.assertTrue(self.policy.allows_request(base+'/parabank/index.htm?ConnType=JDBC', 'GET'))
        self.assertFalse(self.policy.allows_request(base+'/parabank/index.htm?ConnType='+CANARY, 'GET'))
        for suffix in ['/parabank/transfer.htm', '/parabank/overview.htm?secret='+CANARY,
                       '/parabank/activity.htm?id=1&id=2', '/parabank/activity.htm?id=abc',
                       '/parabank/%2e%2e/admin', '/parabank/images/../transfer.htm',
                       '/parabank/services_proxy/bank/transfer?fromAccountId=1&toAccountId=2&amount=3']:
            with self.subTest(suffix=suffix): self.assertFalse(self.policy.allows_request(base+suffix,'GET'))
        for url in ['https://example.test/', 'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x',
                    'http://127.0.0.1:8080@evil.test/parabank/overview.htm']:
            with self.subTest(url=url): self.assertFalse(self.policy.allows_request(url,'GET'))
        self.assertFalse(self.policy.allows_request(base+'/parabank/overview.htm','POST'))

    def test_policy_is_configurable_and_can_only_block_risky_actions(self):
        config = load(DEFAULT_POLICY)
        config['allowed_operations'].remove('fill')
        policy = SafetyPolicy(self.policy.origin, config)
        with self.assertRaises(UIError): policy.operation('fill')
        config['risky_action'] = 'allow'
        with self.assertRaises(UIError): SafetyPolicy(self.policy.origin, config)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cap = load(ROOT/'examples/lookup_transaction.capability.json')
        self.profile = load(ROOT/'profiles/parabank_local_browser.json')
        self.sink = EvidenceWriter(self.temp.name,self.cap,self.profile,load(DEFAULT_POLICY))

    def test_event_fields_never_accept_raw_text(self):
        self.sink.emit('operation_failed',target=CANARY,code=CANARY)
        with self.assertRaises(TypeError): self.sink.emit('operation_failed',message=CANARY)
        with self.assertRaises(UIError): self.sink.emit('operation_failed',reason=CANARY)
        text=''.join(p.read_text() for p in self.sink.directory.iterdir())
        self.assertNotIn(CANARY,text)
        self.assertNotIn('example.person',text)

    def test_snapshot_defense_rejects_raw_strings(self):
        with self.assertRaises(UIError):
            self.sink.snapshot({'format':'redacted_dom_v1','root':{'tag':'p','text':CANARY}})
        self.assertFalse(list(self.sink.directory.glob('failure-*')))

    def test_artifact_export_strips_model_prose_and_rejects_sensitive_literals(self):
        draft=copy.deepcopy(self.cap)
        draft['description']=CANARY
        draft['name']=CANARY
        draft['steps'][0]['description']=CANARY
        draft['steps'][0]['id']='sensitive_model_identifier'
        name=self.sink.export_capability(draft,self.cap)
        exported=load(self.sink.directory/name)
        self.assertTrue(exported['requires_new_version_and_review'])
        validate(exported['candidate'])
        text=(self.sink.directory/name).read_text()
        self.assertNotIn(CANARY,text)
        self.assertNotIn('sensitive_model_identifier',text)
        draft['steps'][5]['action']['value']={'literal':CANARY}
        before=set(self.sink.directory.iterdir())
        with self.assertRaises(UIError): self.sink.export_capability(draft,self.cap)
        self.assertEqual(before,set(self.sink.directory.iterdir()))

    def test_unknown_sensitive_metadata_requires_review(self):
        draft=copy.deepcopy(self.cap)
        draft['outputs']['description']['description']=CANARY
        with self.assertRaises(UIError): self.sink.export_capability(draft,self.cap)

    def test_evidence_permissions(self):
        self.assertEqual(self.sink.directory.stat().st_mode & 0o777,0o700)
        for file in self.sink.directory.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777,0o600)


class BrowserSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cap=load(ROOT/'examples/lookup_transaction.capability.json')
        self.profile=load(ROOT/'profiles/parabank_local_browser.json')
        self.adapter=BrowserAdapter(self.cap,self.profile,{'account_id':'12345','transaction_id':'12145'},evidence_root=self.temp.name)
        await self.adapter.__aenter__()
        self.addAsyncCleanup(self.adapter.__aexit__,None,None,None)
        await self.adapter.login('john','demo')

    def all_evidence(self):
        return '\n'.join(file.read_text() for file in self.adapter.evidence.directory.iterdir())

    async def test_failure_evidence_removes_text_attributes_urls_and_values(self):
        await self.adapter.page.evaluate('''s => {
            const e=document.createElement('input'); e.value=s; e.setAttribute('value',s);
            e.setAttribute('title',s); e.setAttribute('aria-label',s); document.body.append(e);
            const p=document.createElement('p');p.textContent=s;document.body.append(p);
        }''',CANARY)
        await self.adapter.page.route('**/accounts/12345/transactions/**',lambda route:route.fulfill(status=500,body=CANARY))
        with self.assertRaises(UIError) as caught: await walkthrough(self.adapter)
        self.assertEqual(caught.exception.code,'app_error')
        snapshots=list(self.adapter.evidence.directory.glob('failure-*.json'))
        self.assertTrue(snapshots)
        text=self.all_evidence()
        for secret in [CANARY,'example.person','999-88-7777','John Smith','12345','12145']:
            self.assertNotIn(secret,text)
        self.assertIn('[REDACTED]',text)
        self.assertIn('"tag": "table"',text)
        self.assertIn('operation_failed',text)

    async def test_disk_failure_prevents_action(self):
        await act(self.adapter,'click','find_transactions')
        await self.adapter.wait({'predicate':'ready','target':'search'})
        resolution=await self.adapter.resolve('transaction_input',await self.adapter.observe())
        with patch.object(self.adapter.evidence,'emit',side_effect=UIError('evidence_unavailable')):
            with self.assertRaises(UIError) as caught:
                await self.adapter.perform({'type':'fill','target':'transaction_input','value':{'input':'transaction_id'}},resolution,epoch=self.adapter.epoch)
        self.assertEqual(caught.exception.code,'evidence_unavailable')
        self.assertEqual(await self.adapter.page.locator('#transactionId').input_value(),'')

    async def test_modified_form_destination_blocked_before_typing(self):
        await act(self.adapter,'click','find_transactions')
        await self.adapter.wait({'predicate':'ready','target':'search'})
        await self.adapter.page.locator('#transactionForm').evaluate("e=>e.action='https://example.test/collect'")
        with self.assertRaises(UIError) as caught:
            await act(self.adapter,'fill','transaction_input',{'input':'transaction_id'})
        self.assertEqual(caught.exception.code,'policy_denied')
        self.assertEqual(await self.adapter.page.locator('#transactionId').input_value(),'')

    async def test_rebound_transfer_and_unrelated_literal_are_blocked(self):
        self.adapter.profile['bindings']['find_transactions']['candidates']=[{'strategy':'role','role':'link','name':{'literal':'Transfer Funds'}}]
        with self.assertRaises(UIError) as caught: await act(self.adapter,'click','find_transactions')
        self.assertEqual(caught.exception.code,'policy_denied')
        self.assertIn('policy_blocked',self.all_evidence())
        self.adapter.profile=copy.deepcopy(self.profile)
        await act(self.adapter,'click','find_transactions')
        await self.adapter.wait({'predicate':'ready','target':'search'})
        with self.assertRaises(UIError): await act(self.adapter,'fill','transaction_input',{'literal':CANARY})
        self.assertNotIn(CANARY,self.all_evidence())

    async def test_risky_capability_is_rejected_at_construction(self):
        self.cap['effect']='irreversible_write'
        with self.assertRaises(UIError) as caught:
            BrowserAdapter(self.cap,self.profile,{'account_id':'12345','transaction_id':'12145'},evidence_root=self.temp.name)
        self.assertEqual(caught.exception.code,'policy_denied')


class RedirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hits=[]
        hits=self.hits
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                if self.path=='/parabank/index.htm':
                    self.send_response(302);self.send_header('Location','/parabank/overview.htm');self.end_headers()
                elif self.path=='/parabank/overview.htm':
                    self.send_response(302);self.send_header('Location','/forbidden?secret='+CANARY);self.end_headers()
                else:
                    self.send_response(200);self.end_headers();self.wfile.write(b'<body>fixture</body>')
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        origin='http://127.0.0.1:'+str(self.server.server_port)
        config=load(DEFAULT_POLICY);config['allowed_origins']=[origin]
        self.adapter=BrowserAdapter(load(ROOT/'examples/lookup_transaction.capability.json'),load(ROOT/'profiles/parabank_local_browser.json'),
                                  {'account_id':'12345','transaction_id':'12145'},base_url=origin,policy_config=config,evidence_root=self.temp.name)
        await self.adapter.__aenter__();self.addAsyncCleanup(self.adapter.__aexit__,None,None,None)

    async def test_multi_hop_redirect_is_stopped_before_forbidden_request(self):
        with self.assertRaises(UIError): await self.adapter.login('synthetic','synthetic')
        self.assertIn('/parabank/overview.htm',self.hits)
        self.assertFalse(any(hit.startswith('/forbidden') for hit in self.hits))
        self.assertEqual(self.adapter._fault,'policy_denied')
        text=''.join(p.read_text() for p in self.adapter.evidence.directory.iterdir())
        self.assertNotIn(CANARY,text)

    async def test_popup_request_blocked_before_server(self):
        # Start at a harmless allowed document then request a new browsing context.
        await self.adapter.page.goto(self.adapter.policy.origin+'/parabank/findtrans.htm')
        self.hits.clear()
        await self.adapter.page.evaluate("window.open('/parabank/findtrans.htm','_blank')")
        await asyncio.sleep(0.15)
        self.assertEqual(self.hits,[])
        self.assertEqual(self.adapter._fault,'policy_denied')

    async def test_websocket_never_connects(self):
        await self.adapter.page.goto(self.adapter.policy.origin+'/parabank/findtrans.htm')
        self.hits.clear()
        await self.adapter.page.evaluate("new WebSocket(location.origin.replace('http:','ws:')+'/parabank/findtrans.htm')")
        await asyncio.sleep(0.1)
        self.assertEqual(self.hits,[])
        self.assertEqual(self.adapter._fault,'policy_denied')
