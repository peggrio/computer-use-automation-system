"""Read reviewed, immutable local capability pins. No discovery or model imports."""
from tools.validate_contracts import ROOT, load, pinned, digest, validate, ContractError
from .errors import UIError


def load_registered(capability_id='lookup_transaction', version='0.2.0'):
    try:
        registry = load(ROOT / 'capabilities/registry.json')
        matches = [entry for entry in registry['entries'] if entry['capability']['id'] == capability_id
                   and entry['capability']['version'] == version]
        if registry['format'] != 'local_registry_v1' or len(matches) != 1:
            raise UIError('invalid_input')
        entry = matches[0]
        directory = (ROOT / entry['directory']).resolve()
        if not directory.is_relative_to((ROOT / 'capabilities').resolve()):
            raise UIError('policy_denied')
        cap, profile = load(directory/'capability.json'), load(directory/'surface-profile.json')
        validate(cap)
        validate(profile, cap)
        if pinned(cap) != entry['capability'] or pinned(profile) != entry['profile']:
            raise UIError('policy_denied')
        return cap, profile, entry
    except (OSError, ValueError, KeyError, TypeError, ContractError):
        raise UIError('invalid_input') from None


def check_registered_policy(entry, config):
    if digest(config) != entry['policy_sha256']:
        raise UIError('policy_denied')
