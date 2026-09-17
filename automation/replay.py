"""Deterministic v1 interpreter. No model, discovery planner, or application selectors."""
import asyncio
import copy
from time import monotonic
from uuid import uuid4

from .errors import UIError
from tools.validate_contracts import validate, pinned, check_fields, ContractError


RECOVERABLE = {'session_expired', 'load_timeout', 'target_missing', 'precondition_failed',
               'checkpoint_failed', 'stale_observation'}


def invocation_for(adapter, inputs=None, *, max_duration_ms=120000, max_actions=40):
    return {'kind': 'invocation', 'schema_version': '1.0.0', 'run_id': adapter.evidence.run_id,
            'capability': pinned(adapter.capability), 'profile': pinned(adapter.profile),
            'session_id': adapter.session_id, 'policy_id': adapter.policy.config['id'],
            'inputs': copy.deepcopy(adapter.inputs if inputs is None else inputs),
            'limits': {'max_duration_ms': max_duration_ms, 'max_actions': max_actions}}


class Replay:
    def __init__(self, adapter, *, allow_pause=False, cancel_event=None):
        self.adapter = adapter
        self.cap = copy.deepcopy(adapter.capability)
        self.profile = copy.deepcopy(adapter.profile)
        self.allow_pause = allow_pause
        self.cancel_event = cancel_event
        self.epoch = adapter.epoch
        self.step = None
        self.phase = 'preflight'
        self.action_in_progress = False
        self.actions = 0
        self.attempt = 0
        self.classification = None
        self.invocation = None
        self._used = False
        self.sequence = 0
        self.pending = None
        self.active_seconds = 0
        self.segment = 0
        self._resume_lock = asyncio.Lock()

    def base_result(self):
        return {'kind': 'run_result', 'schema_version': '1.0.0', 'run_id': self.adapter.evidence.run_id,
                'capability': pinned(self.cap), 'profile': pinned(self.profile), 'evidence_refs': []}

    def event(self, event, **safe):
        # Call sites supply only executor-owned labels, validated indices and booleans.
        self.sequence += 1
        self.adapter.evidence._write(f'replay-event-{self.sequence:03d}.json', {
            'format': 'replay_event_v1', 'event': event,
            'step_id': self.step['id'] if self.step else None, 'phase': self.phase, **safe})

    def check_live(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise UIError('limit_exceeded')
        self.adapter._ownership(self.epoch)
        if (pinned(self.adapter.capability) != pinned(self.cap)
                or pinned(self.adapter.profile) != pinned(self.profile)
                or self.adapter.inputs != self.invocation['inputs']):
            raise UIError('policy_denied')

    async def checks(self, conditions, *, poll, deadline, failure):
        while True:
            self.check_live()
            values = [await self.adapter.evaluate(c) for c in conditions]
            if all(value is True for value in values):
                self.event('checkpoints_passed', count=len(values))
                return
            if not poll or monotonic() >= deadline:
                self.event('checkpoints_rejected', results=[
                    'true' if value is True else 'false' if value is False else 'unknown' for value in values])
                raise UIError(failure)
            await asyncio.sleep(min(0.05, max(0, deadline-monotonic())))

    async def dispatch(self, action):
        self.check_live()
        if self.actions >= self.invocation['limits']['max_actions']:
            raise UIError('limit_exceeded')
        self.actions += 1
        self.event('action_started', operation=action['type'], attempt=self.attempt)
        resolution = None
        if action['type'] in {'click', 'fill', 'select'}:
            observation = await self.adapter.observe()
            resolution = await self.adapter.resolve(action['target'], observation)
        self.check_live()
        self.action_in_progress = action['type'] in {'click', 'fill', 'select'}
        result = await self.adapter.perform(action, resolution, epoch=self.epoch)
        self.action_in_progress = False
        self.event('action_completed', operation=action['type'], attempt=self.attempt)
        return result

    async def execute_step(self, step):
        self.step = step
        deadline = monotonic() + step['timeout_ms']/1000
        self.phase = 'requires'
        self.event('step_started')
        try:
            async with asyncio.timeout_at(deadline):
                await self.checks(step['requires'], poll=False, deadline=deadline, failure='precondition_failed')
                self.phase = 'action'
                attempts = step['retry']['max_attempts']
                receipt = None
                for attempt in range(1, attempts+1):
                    self.attempt = attempt
                    try:
                        # Only wait actions can have retries (contract validator enforces it).
                        # Divide the remaining time; retries never reset the step/run deadline.
                        allowance = max(0, deadline-monotonic())/(attempts-attempt+1)
                        async with asyncio.timeout(allowance):
                            receipt = await self.dispatch(step['action'])
                        break
                    except (TimeoutError, UIError) as error:
                        retryable = isinstance(error, TimeoutError) or error.code == 'load_timeout'
                        if step['action']['type'] != 'wait' or not retryable or attempt == attempts:
                            raise
                        self.event('wait_retry', attempt=attempt)
                        await asyncio.sleep(min(step['retry']['delay_ms']/1000, max(0, deadline-monotonic())))
                self.phase = 'ensures'
                failure = 'load_timeout' if any(c['predicate']=='ready' for c in step['ensures']) else 'checkpoint_failed'
                await self.checks(step['ensures'], poll=True, deadline=deadline, failure=failure)
                self.phase = 'outcomes'
                matches = []
                for outcome in step['outcomes']:
                    self.check_live()
                    value = await self.adapter.evaluate(outcome['when'])
                    if value is None:
                        raise UIError('checkpoint_failed')
                    if value is True:
                        matches.append(outcome['code'])
                if len(matches) > 1:
                    raise UIError('app_error')
                self.event('step_completed', outcome=matches[0] if matches else None)
                return receipt, matches[0] if matches else None
        except TimeoutError:
            code = 'indeterminate_action' if self.action_in_progress else 'load_timeout'
            raise UIError(code) from None

    async def failure(self, code):
        self.classification = 'recoverable_condition' if code in RECOVERABLE else 'hard_failure'
        mapped = UIError(code).result_code
        allowed = {'invalid_input','policy_denied','target_missing','target_ambiguous','precondition_failed',
                   'checkpoint_failed','session_expired','permission_denied','load_timeout','app_error',
                   'adapter_unsupported','output_invalid','limit_exceeded','indeterminate_action'}
        if mapped not in allowed:
            mapped = 'app_error'
        refs = []
        if code != 'evidence_unavailable':
            try:
                async with asyncio.timeout(3):
                    evidence = await self.adapter.capture_failure()
                    if evidence.get('captured'):
                        refs = [evidence['reference']]
            except Exception:
                pass
        result = self.base_result() | {'status': 'failed', 'evidence_refs': refs,
                 'error': {'code': mapped, 'step_id': self.step['id'] if self.step else None,
                           'expected': 'Validated contract and verified checkpoints for the current phase.',
                           'observed': f'Execution stopped during {self.phase}; values redacted.', 'evidence_refs': refs}}
        if (self.allow_pause and self.step and self.step['on_failure'] == 'intervene'
                and self.classification == 'recoverable_condition' and not self.adapter.page.is_closed()
                and self.adapter.owner == 'automation' and self.adapter.epoch == self.epoch):
            epoch = await self.adapter.set_owner('human')
            result['status'] = 'paused'
            result['intervention'] = {'request_id': uuid4().hex, 'session_id': self.adapter.session_id,
                                      'owner': 'human', 'control_epoch': epoch,
                                      'resume_step_id': self.step['id'], 'resume_mode': 'revalidate'}
        return result

    async def run(self, invocation):
        if self._used:
            raise UIError('precondition_failed')  # No accidental duplicate execution or implicit resume.
        self._used = True
        self.invocation = copy.deepcopy(invocation)
        started = monotonic()
        try:
            validate(self.cap)
            validate(self.profile, self.cap)
            validate(invocation, self.cap, self.profile)
            if (invocation['run_id'] != self.adapter.evidence.run_id or invocation['session_id'] != self.adapter.session_id
                    or invocation['policy_id'] != self.adapter.policy.config['id']):
                raise UIError('policy_denied')
            self.check_live()
            if self.cap['effect'] not in self.adapter.policy.config['allowed_effects']:
                raise UIError('policy_denied')
            self.adapter.evidence._write('replay-manifest.json' if self.segment == 0 else f'replay-manifest-{self.segment:03d}.json', {
                'format':'replay_v1', 'run_id':self.adapter.evidence.run_id,
                'capability':pinned(self.cap),'profile':pinned(self.profile),
                'model_calls':0,'inputs_persisted':False,'limits':invocation['limits']})
            outputs = None
            remaining = invocation['limits']['max_duration_ms']/1000 - self.active_seconds
            if remaining <= 0:
                raise UIError('limit_exceeded')
            async with asyncio.timeout(remaining):
                for step in self.cap['steps']:
                    receipt, outcome = await self.execute_step(step)
                    if outcome:
                        self.classification = 'business_outcome'
                        result = self.base_result() | {'status':'business_outcome','code':outcome,'step_id':step['id']}
                        break
                    if receipt and 'outputs' in receipt:
                        outputs = receipt['outputs']
                else:
                    self.phase = 'success_conditions'
                    await self.checks(self.cap['success_conditions'], poll=False, deadline=monotonic(), failure='checkpoint_failed')
                    try:
                        check_fields(outputs or {},self.cap['outputs'],'outputs')
                    except ContractError:
                        raise UIError('output_invalid') from None
                    self.classification = 'success'
                    result = self.base_result() | {'status':'succeeded','outputs':outputs}
        except ContractError:
            result = await self.failure('invalid_input')
        except TimeoutError:
            result = await self.failure('indeterminate_action' if self.action_in_progress else 'limit_exceeded')
        except UIError as error:
            result = await self.failure(error.code)
        except Exception:
            result = await self.failure('app_error')
        self.active_seconds += monotonic() - started
        self.pending = copy.deepcopy(result) if result['status'] == 'paused' else None
        validate(result,self.cap,self.profile)
        summary = {key:value for key,value in result.items() if key != 'outputs'}
        summary.update({'kind':'replay_summary','format':'replay_summary_v1','classification':self.classification,
                        'output_fields':sorted(result.get('outputs',{})), 'actions_attempted':self.actions,
                        'model_calls':0,'elapsed_seconds':round(monotonic()-started,3),
                        'live_session_retained':result['status']=='paused'})
        try:
            self.adapter.evidence._write('replay-result.json' if self.segment == 0 else f'replay-result-{self.segment:03d}.json',summary)
        except UIError:
            # A result that could not be audited must not be reported as success.
            result = await self.failure('evidence_unavailable')
        self.pending = copy.deepcopy(result) if result['status'] == 'paused' else None
        return result

    async def resume(self, *, request_id, session_id, control_epoch, on_control_return=None):
        async with self._resume_lock:
            return await self._resume(request_id=request_id, session_id=session_id, control_epoch=control_epoch, on_control_return=on_control_return)

    async def _resume(self, *, request_id, session_id, control_epoch, on_control_return=None):
        """Explicit read-only restart after human control; never skip unverified steps."""
        pending = self.pending
        if not pending or pending['status'] != 'paused':
            raise UIError('control_denied')
        ticket = pending['intervention']
        if (request_id != ticket['request_id'] or session_id != ticket['session_id']
                or session_id != self.adapter.session_id or control_epoch != ticket['control_epoch']
                or self.adapter.owner != 'human' or self.adapter.epoch != control_epoch):
            raise UIError('control_denied')
        if self.adapter.page.is_closed():
            raise UIError('session_lost')
        if (self.cap['effect'] != 'read_only' or pinned(self.adapter.capability) != pinned(self.cap)
                or pinned(self.adapter.profile) != pinned(self.profile)
                or self.adapter.inputs != self.invocation['inputs']):
            raise UIError('policy_denied')
        if self.actions >= self.invocation['limits']['max_actions'] or self.active_seconds >= self.invocation['limits']['max_duration_ms']/1000:
            raise UIError('limit_exceeded')
        first = self.cap['steps'][0]
        # The supported restart boundary must be a verified, observation-only entry wait.
        if first['action']['type'] != 'wait':
            raise UIError('adapter_unsupported')
        checks = first['requires'] + [{'predicate':'ready','target':first['action']['target']}]
        checking_started = monotonic()
        try:
            async with asyncio.timeout(min(10, max(0.001, self.invocation['limits']['max_duration_ms']/1000-self.active_seconds))):
                for check in checks:
                    if await self.adapter.evaluate(check) is not True:
                        self.event('resume_rejected', reason='entry_checkpoint_not_ready')
                        raise UIError('precondition_failed')
        finally:
            self.active_seconds += monotonic() - checking_started
        self.event('resume_revalidated', reason='read_only_restart_from_entry')
        self.epoch = await self.adapter.set_owner('automation')
        if on_control_return is not None:
            await on_control_return()
        self.pending = None
        self.action_in_progress = False
        self.segment += 1
        self._used = False
        return await self.run(self.invocation)

    async def abort(self, code='limit_exceeded'):
        """Revoke a pending handoff; no subsequent resume is valid."""
        self.allow_pause = False
        self.pending = None
        if self.adapter.owner == 'human':
            self.epoch = await self.adapter.set_owner('automation')
        result = await self.failure(code)
        self.event('handoff_aborted', reason='explicit_abort_or_handoff_limit')
        self.adapter.evidence._write(f'replay-aborted-{self.segment:03d}.json', result)
        return result
