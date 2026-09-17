"""Same-session human handoff and redacted DOM-event capture. No model dependency."""
import asyncio
import copy
from time import monotonic
from datetime import datetime, timezone

from .errors import UIError
from .replay import RECOVERABLE

# Only a closed projection leaves the page: no text, values, keys, URLs or arbitrary IDs.
RECORDER = r'''(() => {
  if (window.__takeoverRecorder) return;
  const controls = {accountId:'account_select', transactionId:'transaction_input', findById:'search_by_id'};
  const tags = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','FORM']);
  const state = {mode:'automation', epoch:0, pending:[], count:0};
  window.__takeoverRecorder = state;
  const send = data => {
    const promise = window.__recordHumanAction(data).catch(() => {state.mode='sealed';});
    state.pending.push(promise);
    promise.finally(() => {state.pending=state.pending.filter(p=>p!==promise);});
  };
  for (const event of ['click','input','change','submit','keydown']) {
    document.addEventListener(event, e => {
      if (state.mode === 'sealed') { e.preventDefault(); e.stopImmediatePropagation(); return; }
      if (state.mode !== 'human' || !e.isTrusted) return;
      if (event === 'keydown' && !['Enter','Tab','Escape'].includes(e.key)) return;
      if (++state.count > 1000) {state.mode='sealed'; send({event:'capture_limit'}); return;}
      const node = e.target.closest ? e.target.closest('a,button,input,select,textarea,form') : null;
      let control = node && controls[node.id] || 'other';
      if (node && node.tagName==='INPUT' && node.name==='username') control='login_username';
      if (node && node.tagName==='INPUT' && node.type==='password') control='login_password';
      send({event,control,tag:node && tags.has(node.tagName) ? node.tagName : 'OTHER',epoch:state.epoch});
    }, true);
  }
  return window.__recordHumanAction({event:'document_ready'}).then(s => {state.mode=s.mode; state.epoch=s.epoch;});
})();'''


class Takeover:
    def __init__(self, runner, *, max_human_seconds=300, source='human_operator'):
        if not 1 <= max_human_seconds <= 3600 or source not in {'human_operator','test_harness'}:
            raise UIError('invalid_input')
        self.runner = runner
        self.adapter = runner.adapter
        self.limit = max_human_seconds
        self.source = source
        self.active = False
        self.sealed = False
        self.count = 0
        self.sequence = 0
        self.deadline = None
        self.ticket = None
        self._lock = asyncio.Lock()
        self._installed = False
        self._watchdog = None
        self.expired = False
        self.terminal_result = None

    def record(self, event, **data):
        self.sequence += 1
        self.adapter.evidence._write(f'human-event-{self.sequence:04d}.json', {
            'format':'human_event_v1','source':self.source,'event':event,
            'timestamp':datetime.now(timezone.utc).isoformat(),
            'session_id':self.adapter.session_id,'control_epoch':self.adapter.epoch,**data})

    async def install(self):
        if self._installed:
            return
        await self.adapter.page.expose_binding('__recordHumanAction', self._receive)
        await self.adapter.page.add_init_script(RECORDER)
        await self.adapter.page.evaluate(RECORDER)
        self._installed = True

    async def _receive(self, source, data):
        if source['page'] != self.adapter.page or source['frame'] != self.adapter.page.main_frame:
            return {'mode':'sealed','epoch':self.adapter.epoch}
        if data == {'event':'capture_limit'} and self.active:
            self.adapter._fault='limit_exceeded'
            self.adapter._human_login_active=False
            self.record('capture_stopped',reason='event_limit')
            return {'mode':'sealed','epoch':self.adapter.epoch}
        if data == {'event':'document_ready'}:
            if self.active:
                self.record('navigation', destination='approved_document' if self.adapter.policy.allows_request(self.adapter.page.url,'GET') else 'unclassified')
            return {'mode':'sealed' if self.sealed else 'human' if self.active else 'automation','epoch':self.adapter.epoch}
        valid = (isinstance(data,dict) and set(data)=={'event','control','tag','epoch'}
                 and data['event'] in {'click','input','change','submit','keydown'}
                 and data['control'] in {'account_select','transaction_input','search_by_id','other','login_username','login_password'}
                 and data['tag'] in {'A','BUTTON','INPUT','SELECT','TEXTAREA','FORM','OTHER'}
                 and type(data['epoch']) is int)
        if not valid:
            return {'mode':'sealed','epoch':self.adapter.epoch}
        if not self.active or self.adapter.owner!='human' or data['epoch']!=self.adapter.epoch:
            return {'mode':'sealed','epoch':self.adapter.epoch}
        self.count += 1
        if self.count > 1000:
            self.adapter._fault = 'limit_exceeded'
            raise UIError('limit_exceeded')
        try:
            self.record('manual_action', action=data['event'], control=data['control'], tag=data['tag'], values='[REDACTED]')
        except UIError:
            self.adapter._fault = 'evidence_unavailable'
            self.adapter._human_login_active = False
            raise
        return {'mode':'human','epoch':self.adapter.epoch}

    async def mode(self, mode):
        # Seal synchronously before draining callbacks so UI edits cannot race the resume check.
        await self.adapter.page.evaluate('''async ({mode,epoch}) => {
          const s=window.__takeoverRecorder; if (!s) throw new Error('recorder unavailable');
          s.mode=mode; s.epoch=epoch;
          if (mode==='sealed') await Promise.all(s.pending);
        }''', {'mode':mode,'epoch':self.adapter.epoch})

    async def begin(self, result):
        async with self._lock:
            if self.active or not self._installed or result != self.runner.pending or result['status']!='paused':
                raise UIError('control_denied')
            self.ticket=copy.deepcopy(result['intervention'])
            if (self.adapter.owner!='human' or self.adapter.epoch!=self.ticket['control_epoch']
                    or self.adapter.session_id!=self.ticket['session_id']):
                raise UIError('control_denied')
            self.record('takeover_started', request_id=self.ticket['request_id'], failed_step=result['error']['step_id'],
                        code=result['error']['code'], restart_step=self.runner.cap['steps'][0]['id'])
            self.active=True; self.sealed=False; self.expired=False
            self.deadline=monotonic()+self.limit
            # Only the paused session-expiry recovery path grants the existing login POST.
            self.adapter._human_login_active=result['error']['code']=='session_expired'
            await self.mode('human')
            self._watchdog=asyncio.create_task(self._expire())
            return self.context()

    def context(self):
        return {'status':'awaiting_human','run_id':self.adapter.evidence.run_id,
                'session_id':self.adapter.session_id,'request_id':self.ticket['request_id'],
                'control_epoch':self.ticket['control_epoch'],'owner':self.adapter.owner,
                'capability':self.runner.cap['id'],'failed_step':self.runner.step['id'],
                'phase':self.runner.phase,'code':self.runner.pending['error']['code'],
                'action':copy.deepcopy(self.runner.step['action']),
                'expected':self.runner.pending['error']['expected'],
                'observed':self.runner.pending['error']['observed'],
                'evidence_refs':self.runner.pending['evidence_refs'],
                'actions_remaining':self.runner.invocation['limits']['max_actions']-self.runner.actions,
                'instruction':'Use this same browser. Restore Accounts Overview, then explicitly resume. All read-only checks will run again.',
                'manual_values_recorded':False,'human_timeout_seconds':self.limit}

    async def _expire(self):
        try:
            await asyncio.sleep(max(0,self.deadline-monotonic()))
            async with self._lock:
                if self.active:
                    self.expired=True
                    self.terminal_result=await self._abort('human_timeout')
        except asyncio.CancelledError:
            pass

    def _cancel_watchdog(self):
        if self._watchdog and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()

    async def resume(self, *, request_id, session_id, control_epoch):
        async with self._lock:
            if not self.active or self.expired or monotonic()>=self.deadline:
                raise UIError('control_denied')
            if {'request_id':request_id,'session_id':session_id,'control_epoch':control_epoch} != {
                    k:self.ticket[k] for k in ('request_id','session_id','control_epoch')}:
                raise UIError('control_denied')
            if self.adapter.page.is_closed() or self.adapter._fault:
                self.terminal_result=await self._abort('session_fault',self.adapter._fault or 'session_lost')
                return self.terminal_result
            self.sealed=True
            await self.mode('sealed')
            self.adapter._human_login_active=False
            self.record('return_requested', request_id=request_id)
            # Leave native input sealed during revalidation. Unseal only when ownership changes.
            async def returned():
                self.active=False; self.sealed=False
                self._cancel_watchdog()
                await self.mode('automation')
            try:
                result=await self.runner.resume(request_id=request_id,session_id=session_id,
                                               control_epoch=control_epoch,on_control_return=returned)
            except (UIError,TimeoutError) as error:
                code=error.code if isinstance(error,UIError) else 'load_timeout'
                self.record('return_rejected', code=code if code in RECOVERABLE else 'control_denied')
                if code not in RECOVERABLE:
                    self.terminal_result=await self._abort('hard_failure',code)
                    return self.terminal_result
                self.sealed=False
                self.adapter._human_login_active=self.runner.pending['error']['code']=='session_expired'
                await self.mode('human')
                raise UIError(code) from None
            self.record('automation_result', status=result['status'])
            return result

    async def _abort(self, reason, code='limit_exceeded'):
        self.sealed=True
        self.adapter._human_login_active=False
        try:
            await self.mode('sealed')
        except Exception:
            pass
        self.active=False
        self._cancel_watchdog()
        self.record('takeover_aborted', reason=reason)
        result=await self.runner.abort(code)
        return result

    async def abort(self):
        async with self._lock:
            if not self.active:
                raise UIError('control_denied')
            return await self._abort('operator_abort')

    async def close(self):
        self._cancel_watchdog()
        self.adapter._human_login_active=False
        if self.active:
            await self.abort()
