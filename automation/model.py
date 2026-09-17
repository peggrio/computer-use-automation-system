"""Bounded model transport. Never log credentials, raw responses, or provider errors."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError


class DiscoveryError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def local_config(path):
    """Read a small dotenv subset as data, never execute/source shell code."""
    result = {}
    path = Path(path)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep or key.strip() not in {'OPENAI_API_KEY', 'OPENAI_MODEL', 'PARABANK_USERNAME', 'PARABANK_PASSWORD'}:
                raise DiscoveryError('invalid_local_config')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            result[key.strip()] = value
    for key in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'PARABANK_USERNAME', 'PARABANK_PASSWORD'):
        if os.environ.get(key):
            result[key] = os.environ[key]
    return result


@dataclass
class ModelReply:
    decision: dict | None
    receipt: dict
    rejection: str | None = None


class OpenAIModel:
    source = 'openai_responses'

    def __init__(self, key, model='gpt-5.4-mini', *, max_output_tokens=1024):
        if not key:
            raise DiscoveryError('missing_api_key')
        if model != 'gpt-5.4-mini':
            raise DiscoveryError('unsupported_model')
        self.model = model
        self.max_output_tokens = max_output_tokens
        # Explicit endpoint: an inherited OPENAI_BASE_URL cannot redirect the secret.
        self.client = AsyncOpenAI(api_key=key, base_url='https://api.openai.com/v1', max_retries=0)

    async def close(self):
        await self.client.close()

    async def decide(self, instructions, context, schema, timeout):
        try:
            response = await self.client.responses.create(
                model=self.model, instructions=instructions,
                input=json.dumps(context, sort_keys=True), store=False,
                reasoning={'effort': 'low'}, max_output_tokens=self.max_output_tokens,
                text={'format': {'type': 'json_schema', 'name': 'ui_decision', 'strict': True, 'schema': schema}},
                timeout=timeout)
        except AuthenticationError:
            raise DiscoveryError('api_authentication_failed') from None
        except RateLimitError as error:
            code = getattr(error, 'code', None)
            raise DiscoveryError('api_quota_exhausted' if code in {'insufficient_quota', 'credit_balance_exhausted'}
                                 else 'api_rate_limited') from None
        except APIError:
            raise DiscoveryError('api_request_failed') from None
        if response.status != 'completed':
            raise DiscoveryError('model_incomplete')
        if any(getattr(part, 'type', None) == 'refusal'
               for item in response.output if getattr(item, 'type', None) == 'message'
               for part in item.content):
            raise DiscoveryError('model_refused')
        rejection = None
        try:
            decision = json.loads(response.output_text)
        except (ValueError, TypeError):
            decision, rejection = None, 'invalid_json'
        if rejection is None and not Draft202012Validator(schema).is_valid(decision):
            decision, rejection = None, 'schema_mismatch'
        if (not re.fullmatch(r'resp_[a-zA-Z0-9_-]{1,200}', response.id)
                or not re.fullmatch(r'gpt-5\.4-mini(?:-[0-9-]+)?', response.model)):
            raise DiscoveryError('invalid_model_receipt')
        usage = response.usage
        if usage is None or any(type(x) is not int or x < 0 for x in (usage.input_tokens, usage.output_tokens)):
            raise DiscoveryError('invalid_model_receipt')
        return ModelReply(decision, {'response_id': response.id, 'model': response.model,
                                    'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens}, rejection)
