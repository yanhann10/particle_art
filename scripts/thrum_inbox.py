#!/usr/bin/env python3
"""Process thrum inbox messages and apply steering directives.
Called by parallel_tick.sh before each mutation tick.
Reads stdin as JSON from `thrum inbox --unread --json`.
Handles comma-separated directives in a single message.
"""
import json, sys, subprocess, os, re

REPO = os.environ.get('PARTICLE_ART_REPO', os.path.expanduser('~/git_repo/particle_art'))
PREFS = os.path.join(REPO, 'scripts', 'preferences.json')
VENV_PYTHON = os.path.join(REPO, '.venv', 'bin', 'python3')
MUTATE_PY = os.path.join(REPO, 'scripts', 'mutate.py')

def load_prefs():
    with open(PREFS) as f:
        return json.load(f)

def save_prefs(prefs):
    with open(PREFS, 'w') as f:
        json.dump(prefs, f, indent=2)
    print('[thrum] preferences.json updated')

def apply_directive(part):
    part = part.strip()
    # keep <code>
    m = re.match(r'keep\s+([a-z0-9]{3})\b', part)
    if m:
        code = m.group(1)
        prefs = load_prefs()
        prefs.setdefault('marks', {}).setdefault(code, {})['favorite'] = True
        prefs['marks'][code].pop('drop', None)
        save_prefs(prefs)
        return
    # drop <code>
    m = re.match(r'drop\s+([a-z0-9]{3})\b', part)
    if m:
        code = m.group(1)
        prefs = load_prefs()
        prefs.setdefault('marks', {}).setdefault(code, {})['drop'] = True
        prefs['marks'][code].pop('favorite', None)
        save_prefs(prefs)
        return
    # mutate <code> → <directive>
    m = re.match(r'mutate\s+([a-z0-9]{3})\s*[→\-]>?\s*(.+)', part)
    if m:
        parent, directive = m.group(1), m.group(2).strip()
        print(f'[thrum] immediate mutate: {parent} → {directive}')
        subprocess.run([VENV_PYTHON, MUTATE_PY, '--parent', parent, '--directive', directive],
                       cwd=REPO, check=False)
        return
    if part:
        print(f'[thrum] no handler for: {part[:50]}')

data = json.load(sys.stdin)
messages = data if isinstance(data, list) else data.get('messages', [])

for msg in messages:
    body_raw = msg.get('body', '')
    body = (body_raw.get('content', '') if isinstance(body_raw, dict) else str(body_raw)).strip()
    sender = msg.get('agent_id', msg.get('from', '?'))
    print(f'[thrum] from @{sender}: {body[:100]}')
    # strip leading "steering:" prefix if present, then split by comma
    body_clean = re.sub(r'^steering:\s*', '', body, flags=re.IGNORECASE)
    for part in re.split(r',\s*', body_clean.lower()):
        apply_directive(part)
