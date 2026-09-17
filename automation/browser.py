"""Playwright UI adapter shared by discovery and replay clients.

No workflow interpreter, model calls, response-body extraction or persistent browser profile.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from functools import wraps
import copy
from datetime import datetime
from decimal import Decimal
from time import monotonic
from uuid import uuid4
from urllib.parse import parse_qsl, urlsplit
from contextvars import ContextVar
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, async_playwright

from tools.validate_contracts import SCHEMA, check_fields, validate
from jsonschema import Draft202012Validator
from .errors import UIError
from .parabank import IMAGE, RULES, ready
from .policy import SafetyPolicy
from .evidence import EvidenceWriter, STRUCTURAL_SNAPSHOT


ACTION_VALIDATOR = Draft202012Validator({'$defs': SCHEMA['$defs'], '$ref': '#/$defs/action'})
CONDITION_VALIDATOR = Draft202012Validator({'$defs': SCHEMA['$defs'], '$ref': '#/$defs/condition'})


def safe_driver_errors(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        depth = self._audit_depth.get()
        token = self._audit_depth.set(depth + 1)
        target = None
        if args:
            target = args[0].get('target') if isinstance(args[0], dict) else args[0] if isinstance(args[0], str) and method.__name__ != 'login' else None
        operation = method.__name__
        try:
            self.policy.operation(operation if operation != 'perform' else (args[0].get('type', 'invalid') if args and isinstance(args[0], dict) else 'invalid'))
            if depth == 0:
                self.evidence.emit('operation_started', operation=operation, target=target, reason='requested_operation', epoch=self.epoch)
            try:
                result = await method(self, *args, **kwargs)
            except PlaywrightError:
                raise UIError(self._fault or 'observation_failed') from None
            if depth == 0:
                self.evidence.emit('operation_completed', operation=operation, target=target, reason='policy_checked', epoch=self.epoch)
            return result
        except UIError as error:
            if depth == 0 and error.code != 'evidence_unavailable':
                self.evidence.emit('policy_blocked' if error.code == 'policy_denied' else 'operation_failed',
                                   operation=operation, target=target, code=error.code, reason='execution_error', epoch=self.epoch)
                if self._browser is not None:
                    error.evidence = await self.capture_failure()
            raise
        finally:
            self._audit_depth.reset(token)
    return wrapped


@dataclass(frozen=True)
class Observation:
    session_id: str
    generation: int
    document_generation: int
    signature: str
    path: str
    snapshot: str = field(repr=False)  # Transient, potentially sensitive.


@dataclass(frozen=True)
class Resolution:
    target: str
    count: int
    observation: Observation = field(repr=False)


def transform(value, name):
    value = value.strip()
    if name == 'text':
        return value
    if name == 'date_mm_dd_yyyy':
        if not re.fullmatch(r'[0-9]{2}-[0-9]{2}-[0-9]{4}', value):
            raise UIError('output_invalid')
        try:
            return datetime.strptime(value, '%m-%d-%Y').date().isoformat()
        except ValueError:
            raise UIError('output_invalid') from None
    if name == 'usd_amount':
        if not re.fullmatch(r'-?\$(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)\.[0-9]{2}', value):
            raise UIError('output_invalid')
        return format(Decimal(value.replace('$', '').replace(',', '')), '.2f')
    if name == 'credit_debit' and value.lower() in {'credit', 'debit'}:
        return value.lower()
    raise UIError('output_invalid')


class BrowserAdapter:
    def __init__(self, capability, profile, inputs, base_url='http://127.0.0.1:8080', *,
                 headless=True, timeout_ms=10000, timezone_id='UTC', policy_config=None, evidence_root=None,
                 evidence_source='adapter_execution'):
        validate(profile, capability)
        check_fields(inputs, capability['inputs'], 'inputs')
        if profile['adapter'] != {'name': 'playwright_browser', 'contract_version': '1.0.0'}:
            raise UIError('adapter_unsupported')
        if profile['application']['release'] != IMAGE or profile['readiness_contract'] != 'parabank_browser_v1':
            raise UIError('adapter_unsupported')
        if any(x['rule'] not in RULES for x in profile['readiness'].values()):
            raise UIError('adapter_unsupported')
        if any(c['strategy'] not in {'css', 'role'} for b in profile['bindings'].values() for c in b['candidates']):
            raise UIError('adapter_unsupported')
        self.capability, self.profile, self.inputs = copy.deepcopy(capability), copy.deepcopy(profile), dict(inputs)
        self.policy = SafetyPolicy(base_url, policy_config)
        if capability['effect'] not in self.policy.config['allowed_effects']:
            raise UIError('policy_denied')
        self.evidence = EvidenceWriter(evidence_root or Path(__file__).resolve().parents[1] / 'runs',
                                       capability, profile, self.policy.config, source=evidence_source)
        self._audit_depth = ContextVar('audit_depth', default=0)
        self._actions = 0
        self._login_active = False
        self._human_login_active = False
        self._closing = False
        self.headless, self.timeout_ms, self.timezone_id = headless, timeout_ms, timezone_id
        self.session_id = uuid4().hex
        self.epoch, self.owner = 0, 'automation'
        self._lock = asyncio.Lock()
        self._generation = self._document_generation = self._request_sequence = 0
        self._records = {}
        self.search_after = 0
        self._fault = None
        self._pw = self._browser = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            self.context = await self._browser.new_context(timezone_id=self.timezone_id, service_workers='block', accept_downloads=False)
            await self.context.route('**/*', self._route)
            await self.context.route_web_socket('**/*', self._websocket)
            self.page = await self.context.new_page()
            self._cdp = await self.context.new_cdp_session(self.page)
            tree = await self._cdp.send('Page.getFrameTree')
            self._main_frame_id = tree['frameTree']['frame']['id']
            self._cdp.on('Fetch.requestPaused', self._fetch_guard)
            await self._cdp.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})
            self.context.on('page', self._extra_page)
            self.page.on('download', self._download)
            self.page.set_default_timeout(self.timeout_ms)
            self.page.on('request', self._request)
            self.page.on('response', self._response)
            self.page.on('requestfinished', self._finished)
            self.page.on('requestfailed', self._failed)
            self.page.on('dialog', self._dialog)
            self.page.on('pageerror', lambda _: setattr(self, '_fault', 'app_error'))
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        self._closing = True
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
        self.evidence.emit('run_finished', reason='finished')

    def describe(self):
        return {'name': 'playwright_browser', 'contract_version': '1.0.0',
                'strategies': ['role', 'css'], 'actions': ['click', 'fill', 'select', 'wait', 'extract'],
                'readiness_contract': 'parabank_browser_v1', 'readiness_rules': sorted(RULES)}

    def _network_denied(self, reason='destination_not_allowed'):
        self._fault = 'policy_denied'
        try:
            self.evidence.emit('network_blocked', reason=reason, code='policy_denied')
        except UIError:
            self._fault = 'evidence_unavailable'

    async def _websocket(self, websocket):
        self._network_denied('unsupported_context')
        await websocket.close()

    async def _extra_page(self, page):
        self._network_denied('unsupported_context')
        await page.close()

    async def _download(self, download):
        self._network_denied('unsupported_context')
        await download.cancel()

    async def _route(self, route):
        request = route.request
        try:
            main_context = request.frame.page == self.page and request.frame == self.page.main_frame
        except (PlaywrightError, AttributeError):
            main_context = False
        allowed = main_context and self.policy.allows_request(request.url, request.method)
        if self.owner == 'human' and self._fault:
            allowed = False
        if request.method == 'POST' and not (self._login_active or (self.owner == 'human' and self._human_login_active)):
            allowed = False
        if not allowed:
            self._network_denied()
            await route.abort('blockedbyclient')
            return
        await route.continue_()

    async def _fetch_guard(self, event):
        # Chromium's request-stage interception runs again on EACH redirect hop.
        # Playwright routing alone does not: do not replace this with route.continue_.
        request = event['request']
        allowed = (event.get('frameId') == self._main_frame_id
                   and self.policy.allows_request(request['url'], request['method'])
                   and (request['method'] != 'POST' or self._login_active or (self.owner == 'human' and self._human_login_active)))
        if self.owner == 'human' and self._fault:
            allowed = False
        try:
            if allowed:
                await self._cdp.send('Fetch.continueRequest', {'requestId': event['requestId']})
            else:
                self._network_denied()
                await self._cdp.send('Fetch.failRequest', {'requestId': event['requestId'], 'errorReason': 'BlockedByClient'})
        except PlaywrightError:
            if not self._closing:
                self._fault = 'policy_denied'

    def _request(self, request):
        self._request_sequence += 1
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            self._document_generation += 1
            self._records.clear()
        self._records[request] = {'path': urlsplit(request.url).path, 'sequence': self._request_sequence,
                                  'document': request.is_navigation_request(), 'status': None,
                                  'finished': False, 'failed': False}

    def _response(self, response):
        record = self._records.get(response.request)
        if record:
            record['status'] = response.status

    def _finished(self, request):
        if request in self._records:
            self._records[request]['finished'] = True

    def _failed(self, request):
        if request in self._records:
            self._records[request]['failed'] = True

    async def _dialog(self, dialog):
        self._fault = 'unexpected_dialog'
        await dialog.dismiss()  # Never accept an unexpected confirmation.

    def completed(self, path_pattern, *, allow_not_found=False, after=0):
        matching = [r for r in self._records.values() if re.fullmatch(path_pattern, r['path']) and r['sequence'] > after]
        if not matching:
            return False
        record = matching[-1]
        if record['failed']:
            raise UIError('load_timeout')
        if not record['finished']:
            return False
        status = record['status']
        if status in {401, 403}:
            raise UIError('session_expired' if status == 401 else 'permission_denied')
        if not (status is not None and (200 <= status < 300 or (status == 404 and allow_not_found))):
            raise UIError('app_error')
        return True

    def document_complete(self):
        records = [r for r in self._records.values() if r['document']]
        return bool(records and records[-1]['finished'] and records[-1]['status'] == 200)

    async def _check(self, *, login_ok=False):
        if self.page.is_closed():
            raise UIError('session_lost')
        if self._fault:
            raise UIError(self._fault)
        if self.page.url != 'about:blank' and not self.policy.allows_request(self.page.url, 'GET'):
            raise UIError('policy_denied')
        if not login_ok and await self.page.locator('input[type="password"]').is_visible():
            raise UIError('session_expired')
        errors = self.page.locator('#showError:visible, #error:visible, #errorContainer:visible, .error:visible')
        for error in await errors.all():
            if (await error.inner_text()).strip():
                raise UIError('app_error')

    def _ownership(self, epoch):
        if self.owner != 'automation' or epoch != self.epoch:
            raise UIError('control_denied')

    async def set_owner(self, owner):
        """Drain active adapter actions under the lock before publishing a new owner."""
        if owner not in {'automation', 'human'}:
            raise ValueError('Invalid owner')
        async with self._lock:
            next_epoch = self.epoch + 1
            self.evidence.emit('ownership_changed', status=owner, epoch=next_epoch, reason='explicit_transfer')
            self.epoch = next_epoch
            self.owner = owner
            return self.epoch

    @safe_driver_errors
    async def login(self, username, password):
        """Explicit local sandbox bootstrap; values never enter observations or artifacts."""
        async with self._lock:
            self._ownership(self.epoch)
            self._login_active = True
            try:
                await self.page.goto(self.policy.origin + '/parabank/index.htm', wait_until='load')
                await self._check(login_ok=True)
                if await self.page.locator('input[type="password"]').is_visible():
                    for selector in ('input[name="username"]', 'input[name="password"]'):
                        control = self.page.locator(selector)
                        form = await control.evaluate('(e) => e.form ? {action:e.form.action, method:e.form.method.toUpperCase(), target:e.form.target} : null')
                        if (not form or form['method'] != 'POST' or form['target'] not in {'', '_self'}
                                or self.policy.parsed(form['action']) != ('/parabank/login.htm', {})
                                or not self.policy.allows_request(form['action'], 'POST')):
                            raise UIError('policy_denied')
                    await self.page.locator('input[name="username"]').fill(username)
                    await self.page.locator('input[name="password"]').fill(password)
                    await self.page.get_by_role('button', name='Log In', exact=True).click()
                else:
                    await self.page.goto(self.policy.origin + '/parabank/overview.htm', wait_until='load')
                await self.page.wait_for_url('**/parabank/overview.htm', timeout=self.timeout_ms)
                await self.wait({'predicate': 'ready', 'target': 'overview'})
            except PlaywrightError:
                raise UIError(self._fault or 'session_expired') from None
            finally:
                self._login_active = False

    @safe_driver_errors
    async def observe(self):
        await self._check()
        try:
            snapshot = await self.page.locator('body').aria_snapshot()
            # Include form values for stale detection, excluding passwords completely.
            values = await self.page.locator('input:not([type="password"]), select, textarea').evaluate_all(
                '(els) => els.map(e => [e.tagName, e.id, e.value])')
            import json
            signature = hashlib.sha256((self.page.url + snapshot + json.dumps(values)).encode()).hexdigest()
            # Navigation or a network denial may occur while collecting the snapshot.
            await self._check()
            self._generation += 1
            return Observation(self.session_id, self._generation, self._document_generation, signature,
                               urlsplit(self.page.url).path, snapshot)
        except PlaywrightError:
            raise UIError('observation_failed') from None

    def _value(self, reference):
        if 'input' in reference:
            if reference['input'] not in self.inputs:
                raise UIError('invalid_input')
            return self.inputs[reference['input']]
        return reference['literal']

    async def _matches(self, target):
        if target not in self.profile['bindings']:
            raise UIError('target_missing', target)
        binding = self.profile['bindings'][target]
        scope = self.page
        if binding['scope']:
            parents = await self._matches(binding['scope'])
            if len(parents) == 0:
                return []
            if len(parents) != 1:
                raise UIError('target_ambiguous', target)
            scope = parents[0]
        for candidate in binding['candidates']:
            locator = (scope.locator(candidate['selector']) if candidate['strategy'] == 'css'
                       else scope.get_by_role(candidate['role'], name=self._value(candidate['name']), exact=True))
            matches = []
            for element in await locator.all():
                if not await element.is_visible():
                    continue
                accepted = True
                for filter_ in binding['filters']:
                    expected = self._value(filter_['value'])
                    if filter_['type'] == 'text_equals':
                        accepted &= (await element.inner_text()).strip() == expected
                    else:
                        href = await element.get_attribute('href') or ''
                        if re.search(r'%(?![0-9A-Fa-f]{2})', href):
                            raise UIError('checkpoint_failed', target)
                        try:
                            pairs = parse_qsl(urlsplit(href).query, keep_blank_values=True, strict_parsing=True,
                                              encoding='utf-8', errors='strict')
                        except (ValueError, UnicodeError):
                            raise UIError('checkpoint_failed', target) from None
                        keys = [k for k, _ in pairs]
                        if len(keys) != len(set(keys)):
                            raise UIError('checkpoint_failed', target)
                        accepted &= dict(pairs).get(filter_['parameter']) == expected
                if accepted:
                    matches.append(element)
            if matches:
                # Do not try a fallback after multiple matches.
                return matches
        return []

    @safe_driver_errors
    async def resolve(self, target, observation):
        current = await self.observe()
        if (current.session_id != observation.session_id or current.signature != observation.signature
                or current.document_generation != observation.document_generation):
            raise UIError('stale_observation', target)
        matches = await self._matches(target)
        return Resolution(target, len(matches), current)

    async def _one(self, target):
        matches = await self._matches(target)
        if not matches:
            raise UIError('target_missing', target)
        if len(matches) != 1:
            raise UIError('target_ambiguous', target)
        return matches[0]

    @safe_driver_errors
    async def read(self, target):
        await self._check()
        element = await self._one(target)
        if await element.get_attribute('type') == 'password':
            raise UIError('policy_denied', target)
        return (await element.inner_text()).strip()

    @safe_driver_errors
    async def evaluate(self, condition):
        if not CONDITION_VALIDATOR.is_valid(condition):
            raise UIError('invalid_input')
        await self._check()
        target = condition['target']
        predicate = condition['predicate']
        if predicate == 'ready':
            if target not in self.profile['readiness']:
                raise UIError('adapter_unsupported', target)
            return await ready(self, self.profile['readiness'][target]['rule'])
        matches = await self._matches(target)
        if predicate == 'count_equals':
            scope = self.profile['bindings'][target]['scope']
            if scope in self.profile['readiness'] and not await ready(self, self.profile['readiness'][scope]['rule']):
                return None  # Unknown is not an empty business result.
            return len(matches) == condition['expected']
        if predicate == 'visible':
            return bool(matches)
        if len(matches) == 0:
            return False
        if len(matches) != 1:
            raise UIError('target_ambiguous', target)
        if predicate == 'text_equals':
            return (await matches[0].inner_text()).strip() == self._value(condition['expected'])
        if predicate == 'value_equals':
            if await matches[0].get_attribute('type') == 'password':
                raise UIError('policy_denied', target)
            return await matches[0].input_value() == self._value(condition['expected'])
        raise UIError('adapter_unsupported', target)

    @safe_driver_errors
    async def wait(self, condition, timeout_ms=None):
        deadline = monotonic() + (self.timeout_ms if timeout_ms is None else timeout_ms) / 1000
        while monotonic() < deadline:
            if await self.evaluate(condition):
                return
            await asyncio.sleep(min(0.05, max(0, deadline - monotonic())))
        raise UIError('load_timeout', condition['target'])

    @safe_driver_errors
    async def perform(self, action, resolution=None, *, epoch):
        """Policy gate and live ownership check immediately precede dispatch."""
        if not ACTION_VALIDATOR.is_valid(action):
            raise UIError('invalid_input')
        if action['type'] in {'fill', 'select'} and not isinstance(self._value(action['value']), str):
            raise UIError('invalid_input')
        async with self._lock:
            self._ownership(epoch)
            await self._check()
            kind = action['type']
            self._actions += 1
            if self._actions > self.policy.config['max_actions']:
                raise UIError('limit_exceeded')
            if kind == 'wait':
                await self.wait({'predicate': 'ready', 'target': action['target']})
                return {'status': 'completed', 'epoch': self.epoch}
            if kind == 'extract':
                if action != self.capability['steps'][-1]['action']:
                    raise UIError('policy_denied')
                values = {}
                for name, source in action['fields'].items():
                    values[name] = (transform(await self.read(source['target']), source['transform'])
                                    if 'target' in source else self._value(source))
                check_fields(values, self.capability['outputs'], 'outputs')
                return {'status': 'completed', 'epoch': self.epoch, 'outputs': values}
            if kind not in {'click', 'fill', 'select'}:
                raise UIError('adapter_unsupported')
            target = action['target']
            if resolution is None or resolution.target != target:
                raise UIError('stale_observation', target)
            await self.resolve(target, resolution.observation)
            locator = await self._one(target)
            # Freeze the chosen DOM node. A re-render must fail, not silently retarget an nth locator.
            handle = await locator.element_handle()
            if handle is None:
                raise UIError('stale_observation', target)
            try:
                control = await handle.evaluate('''(e) => ({tag:e.tagName,id:e.id,name:e.name,type:e.type,href:e.href || '',
                    download:e.hasAttribute('download'),new_context:!!e.target && e.target !== '_self',
                    form_action:e.form ? (e.hasAttribute('formaction') ? e.formAction : e.form.action) : '',
                    form_method:e.form ? (e.hasAttribute('formmethod') ? e.formMethod : (e.form.method || 'get')).toUpperCase() : ''})''')
                self.policy.authorize(action, target, control, inputs=self.inputs, page_url=self.page.url)
                self.evidence.emit('operation_started',operation=kind,target=target,reason='policy_checked',epoch=self.epoch)
                self._ownership(epoch)
                if kind == 'click':
                    if target == 'search_by_id':
                        self.search_after = self._request_sequence
                    await handle.click(timeout=self.timeout_ms)
                elif kind == 'fill':
                    await handle.fill(self._value(action['value']), timeout=self.timeout_ms)
                else:
                    await handle.select_option(value=self._value(action['value']), timeout=self.timeout_ms)
                await self._check()
                return {'status': 'dispatched', 'epoch': self.epoch}
            except PlaywrightError:
                # The driver may have dispatched before timing out. Never retry blindly.
                raise UIError(self._fault or 'indeterminate_action', target) from None
            finally:
                await handle.dispose()

    async def capture_failure(self):
        try:
            value = await self.page.evaluate(STRUCTURAL_SNAPSHOT)
            name = self.evidence.snapshot(value)
            return {'captured': True, 'reference': name, 'format': 'redacted_dom_v1'}
        except PlaywrightError:
            return {'captured': False, 'reason': 'session_unavailable'}
