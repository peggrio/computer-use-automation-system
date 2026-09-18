"""Internal fail-closed evidence scanning helpers."""
import json
import re
from datetime import datetime
from pathlib import Path

KEY = re.compile(r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}')
PRIVATE = ('PRIVATE-CANARY', 'STEP8-SECRET-CANARY', 'John Smith', 'Check #')
IDS = re.compile(r'(?<![A-Za-z0-9])(?:12345|12145|12256|12678|12367|12456|999999999|100\.00)(?![A-Za-z0-9])')


def scan(root, secrets=()):
    root = Path(root)
    files = 0
    violations = 0

    def inspect(value):
        nonlocal violations
        if isinstance(value, dict):
            for key, item in value.items():
                inspect(key)
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value)
            if KEY.search(text) or any(secret and secret in text for secret in (*PRIVATE, *secrets)):
                violations += 1
                return
            if re.fullmatch(r'\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:\d\d)', text):
                try:
                    datetime.fromisoformat(text.replace('Z', '+00:00'))
                    return
                except ValueError:
                    pass
            if text in {'john', 'demo'} or IDS.search(text):
                violations += 1

    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            violations += 1
        elif path.is_file():
            files += 1
            try:
                if path.suffix == '.json':
                    inspect(json.loads(path.read_text()))
                elif path.suffix == '.jsonl':
                    for line in path.read_text().splitlines():
                        inspect(json.loads(line))
                else:
                    violations += 1
            except (OSError, UnicodeError, ValueError):
                violations += 1
    return {
        'passed': files > 0 and violations == 0,
        'files_scanned': files,
        'violations': violations,
        'matched_values_reported': False,
    }
