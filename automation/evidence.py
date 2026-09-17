"""Allowlisted evidence: never serialize observations, driver errors or input/output values."""
from __future__ import annotations
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import UIError
from tools.validate_contracts import digest, validate

EVENTS = {'run_started','run_finished','operation_started','operation_completed','operation_failed',
          'policy_blocked','network_blocked','ownership_changed','failure_captured','artifact_saved'}
OPERATIONS = {'login','observe','resolve','read','evaluate','wait','perform','extract','click','fill','select'}
REASONS = {'requested_operation','checkpoint_evaluation','policy_checked','execution_error',
           'destination_not_allowed','unsupported_context','risky_or_unlisted_action','explicit_transfer',
           'structural_snapshot','validated_parameterized_artifact','finished'}
CODES = {'invalid_input','policy_denied','target_missing','target_ambiguous','precondition_failed',
         'checkpoint_failed','session_expired','permission_denied','load_timeout','app_error','adapter_unsupported',
         'output_invalid','limit_exceeded','indeterminate_action','stale_observation','control_denied',
         'session_lost','unexpected_dialog','observation_failed','evidence_unavailable','invalid_policy'}

# Data is discarded IN THE BROWSER before it crosses the persistence boundary.
STRUCTURAL_SNAPSHOT = r'''() => {
  const tags = new Set(['html','body','div','span','p','a','button','input','select','option',
    'textarea','label','table','thead','tbody','tfoot','tr','th','td','form','h1','h2','h3',
    'h4','ul','ol','li','b','strong','br','hr','img','iframe','svg','canvas']);
  const roles = new Set(['button','link','textbox','combobox','dialog','alert','table','row',
    'cell','heading','navigation','main','list','listitem','checkbox','radio']);
  let count = 0;
  function visit(el, depth) {
    if (++count > 2000 || depth > 40) return {tag:'omitted'};
    const tag = el.tagName.toLowerCase();
    if (['script','style','noscript'].includes(tag)) return null;
    const item = {tag: tags.has(tag) ? tag : 'other', visible: !!el.getClientRects().length};
    const role = el.getAttribute('role');
    if (roles.has(role)) item.role = role;
    if (['input','textarea','select','button'].includes(tag)) {
      item.enabled = !el.disabled;
      item.value = '[REDACTED]';
    }
    if (['img','iframe','svg','canvas'].includes(tag)) {item.content = '[OMITTED]'; return item;}
    item.children = [];
    for (const node of el.childNodes) {
      if (count >= 2000) {item.children.push({tag:'omitted'}); break;}
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) item.children.push({text:'[REDACTED]'});
      else if (node.nodeType === Node.ELEMENT_NODE) {
        const child = visit(node, depth+1); if (child) item.children.push(child);
      }
    }
    return item;
  }
  return {format:'redacted_dom_v1', root: document.body ? visit(document.body, 0) : {tag:'omitted'}};
}'''


class EvidenceWriter:
    def __init__(self, root, capability, profile, policy_config, *, source='adapter_execution'):
        if source not in {'adapter_execution', 'discovery_attempt', 'deterministic_replay'}:
            raise UIError('invalid_input')
        self.run_id = uuid4().hex
        self.directory = Path(root) / self.run_id
        self.targets = frozenset(capability['targets'])
        self.sequence = 0
        try:
            self.directory.mkdir(parents=True, mode=0o700)
            os.chmod(self.directory, 0o700)
            self._write('manifest.json', {'format':'evidence_v1','run_id':self.run_id,
                        'capability_sha256':digest(capability),'profile_sha256':digest(profile),
                        'policy_sha256':digest(policy_config),'source':source,
                        **({'contains_model_discovery':False} if source != 'discovery_attempt'
                           else {'discovery_result_reference':'discovery-result.json'})})
            self.emit('run_started')
        except OSError:
            raise UIError('evidence_unavailable') from None

    def _write(self, filename, value):
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(self.directory / filename, flags, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(value, file, indent=2, allow_nan=False)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
        except OSError:
            raise UIError('evidence_unavailable') from None

    def emit(self, event, *, operation=None, target=None, reason=None, code=None, epoch=None, status=None):
        # Do not accept arbitrary text or **kwargs. Unknown labels are never written.
        if event not in EVENTS or (operation is not None and operation not in OPERATIONS):
            raise UIError('invalid_input')
        if reason is not None and reason not in REASONS:
            raise UIError('invalid_input')
        if status not in {None,'started','completed','dispatched','failed','succeeded','business_outcome','human','automation'}:
            raise UIError('invalid_input')
        record={'sequence':self.sequence+1,'timestamp':datetime.now(timezone.utc).isoformat(),'event':event}
        if operation is not None: record['operation']=operation
        if target is not None: record['target']=target if target in self.targets else '[REDACTED]'
        if reason is not None: record['reason']=reason
        if code is not None: record['code']=code if code in CODES else 'app_error'
        if epoch is not None:
            if type(epoch) is not int or epoch < 0: raise UIError('invalid_input')
            record['control_epoch']=epoch
        if status is not None: record['status']=status
        try:
            fd=os.open(self.directory/'events.jsonl',os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'a') as file:
                file.write(json.dumps(record,allow_nan=False)+'\n')
                file.flush()
                os.fsync(file.fileno())
            self.sequence += 1
        except OSError:
            raise UIError('evidence_unavailable') from None

    def snapshot(self, value):
        # The JS projection is trusted code; independently restrict the persistence shape too.
        def valid(node):
            if not isinstance(node,dict) or not set(node) <= {'tag','visible','role','enabled','value','content','children','text'}:
                return False
            if 'text' in node and node['text'] != '[REDACTED]': return False
            if 'value' in node and node['value'] != '[REDACTED]': return False
            if 'content' in node and node['content'] != '[OMITTED]': return False
            if 'tag' in node and node['tag'] not in {'html','body','div','span','p','a','button','input','select','option','textarea','label','table','thead','tbody','tfoot','tr','th','td','form','h1','h2','h3','h4','ul','ol','li','b','strong','br','hr','img','iframe','svg','canvas','other','omitted'}: return False
            if 'role' in node and node['role'] not in {'button','link','textbox','combobox','dialog','alert','table','row','cell','heading','navigation','main','list','listitem','checkbox','radio'}: return False
            if any(k in node and type(node[k]) is not bool for k in ('visible','enabled')): return False
            return all(valid(child) for child in node.get('children',[]))
        if set(value) != {'format','root'} or value['format'] != 'redacted_dom_v1' or not valid(value['root']):
            raise UIError('invalid_input')
        name='failure-'+uuid4().hex+'.json'
        self._write(name,value)
        self.emit('failure_captured',reason='structural_snapshot')
        return name

    def export_capability(self, document, trusted_contract):
        """Persist a parameterized draft using reviewed metadata, never model free text.

        Step 5 will supply genuine provenance. This export alone does not approve execution.
        """
        validate(document)
        validate(trusted_contract)
        result=copy.deepcopy(document)
        # A discovery candidate cannot redefine the reviewed business contract or logical targets.
        for key in ('id','version','application','requires_session','effect','inputs','outputs','targets','known_outcomes'):
            if result[key] != trusted_contract[key]:
                raise UIError('policy_denied')
        for key in ('name','description','provenance'):
            result[key]=copy.deepcopy(trusted_contract[key])
        for index, step in enumerate(result['steps']):
            # Strip arbitrary model rationale and free-form identifiers from persisted steps.
            step['id']='step_'+str(index)
            step['description']='Validated UI step; see structured action and conditions.'
        def inspect(value):
            if isinstance(value,dict):
                if 'literal' in value and value['literal'] not in {'USD'}:
                    raise UIError('policy_denied')
                for child in value.values(): inspect(child)
            elif isinstance(value,list):
                for child in value: inspect(child)
        inspect(result)
        validate(result)
        name='capability-draft-'+uuid4().hex+'.json'
        self._write(name,{'format':'sanitized_capability_candidate_v1',
                          'requires_new_version_and_review':True,'candidate':result})
        self.emit('artifact_saved',reason='validated_parameterized_artifact')
        return name
