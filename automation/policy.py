"""Trusted configuration, separate from model-produced capabilities and profiles."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .errors import UIError

DEFAULT_POLICY = Path(__file__).resolve().parents[1] / 'policies/parabank-read-only.json'


class SafetyPolicy:
    def __init__(self, base_url, config=None):
        self.config = copy.deepcopy(config if config is not None else json.loads(DEFAULT_POLICY.read_text()))
        c = self.config
        if (set(c) != {'version', 'id', 'allowed_origins', 'routes', 'actions', 'allowed_effects',
                       'risky_action', 'allowed_operations', 'max_actions'}
                or c['version'] != '1.0.0' or c['risky_action'] != 'block'
                or c['allowed_effects'] != ['read_only'] or type(c['max_actions']) is not int
                or not 1 <= c['max_actions'] <= 1000):
            raise UIError('invalid_policy')
        parsed = urlsplit(base_url)
        self.origin = f'{parsed.scheme}://{parsed.netloc}'
        if (base_url.rstrip('/') != self.origin or self.origin not in c['allowed_origins']
                or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password):
            raise UIError('policy_denied')
        try:
            for rule in c['routes']:
                if set(rule) != {'method', 'path_pattern', 'query', 'required_query'}:
                    raise ValueError()
                re.compile(rule['path_pattern'])
                for value in rule['query'].values():
                    re.compile(value)
        except (ValueError, TypeError, re.error):
            raise UIError('invalid_policy') from None

    def parsed(self, url):
        try:
            if not isinstance(url, str) or any(ord(ch) < 33 for ch in url) or '\\' in url:
                raise ValueError()
            p = urlsplit(url)
            if f'{p.scheme}://{p.netloc}' != self.origin or p.username or p.password or p.fragment:
                raise ValueError()
            # Refuse encoded paths/dot-segments instead of guessing how the server normalizes them.
            path = re.sub(r';jsessionid=[A-Fa-f0-9]+(?=/|$)', '', p.path)
            if '%' in path or ';' in path or any(x in {'.', '..'} for x in path.split('/')):
                raise ValueError()
            if re.search(r'%(?![0-9a-fA-F]{2})', p.query):
                raise ValueError()
            pairs = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True, errors='strict')
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError()
            return path, dict(pairs)
        except (ValueError, UnicodeError):
            raise UIError('policy_denied') from None

    def allows_request(self, url, method):
        try:
            path, query = self.parsed(url)
            for rule in self.config['routes']:
                if method != rule['method'] or not re.fullmatch(rule['path_pattern'], path):
                    continue
                if not set(query) <= set(rule['query']) or not set(rule['required_query']) <= set(query):
                    continue
                if all(re.fullmatch(rule['query'][key], value) for key, value in query.items()):
                    return True
        except UIError:
            pass
        return False

    def operation(self, name):
        if name not in self.config['allowed_operations']:
            raise UIError('policy_denied')

    def authorize(self, action, target, control, *, inputs, page_url):
        kind = action['type']
        self.operation(kind)
        rule = self.config['actions'].get(target)
        if not rule or rule['action'] != kind or rule['effect'] not in self.config['allowed_effects']:
            raise UIError('policy_denied', target)
        if control['tag'] != rule['tag'] or (rule.get('id') and control['id'] != rule['id']):
            raise UIError('policy_denied', target)
        page_path, _ = self.parsed(page_url)
        if rule.get('view') and rule['view'] != page_path:
            raise UIError('policy_denied', target)
        if control.get('download') or control.get('new_context'):
            raise UIError('policy_denied', target)
        if rule.get('path'):
            path, query = self.parsed(control['href'])
            if path != rule['path'] or not self.allows_request(control['href'], 'GET'):
                raise UIError('policy_denied', target)
            if rule.get('query_input') and query.get('id') != inputs[rule['query_input']]:
                raise UIError('policy_denied', target)
        if rule.get('input') and action.get('value') != {'input': rule['input']}:
            # Prevent a model from typing unrelated data into a permitted field.
            raise UIError('policy_denied', target)
        if control.get('form_action') and not self.allows_request(control['form_action'], control['form_method']):
            raise UIError('policy_denied', target)
