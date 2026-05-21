#!/usr/bin/env python3
"""Process thrum inbox messages and apply steering directives.
Called by parallel_tick.sh before each mutation tick.
Reads stdin as JSON array from `thrum inbox --unread --json`.
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
    print(f'[thrum] preferences.json updated')

data = json.load(sys.stdin)
messages = data if isinstance(data, list) else data.get('messages', [])

for msg in messages:
    body = msg.get('body', '').strip()
    body_low = body.lower()
    sender = msg.get('from', '?')
    print(f'[thrum] message from {sender}: {body[:80]}')

    # keep <code>
    m = re.match(r'keep\s+([a-z0-9]{3})\b', body_low)
    if m:
        code = m.group(1)
        prefs = load_prefs()
        prefs.setdefault('marks', {}).setdefault(code, {})['favorite'] = True
        prefs['marks'][code].pop('drop', None)
        save_prefs(prefs)
        continue

    # drop <code>
    m = re.match(r'drop\s+([a-z0-9]{3})\b', body_low)
    if m:
        code = m.group(1)
        prefs = load_prefs()
        prefs.setdefault('marks', {}).setdefault(code, {})['drop'] = True
        prefs['marks'][code].pop('favorite', None)
        save_prefs(prefs)
        continue

    # mutate <code> → <directive>  (also accepts ->)
    m = re.match(r'mutate\s+([a-z0-9]{3})\s*[→\-]>?\s*(.+)', body_low)
    if m:
        parent = m.group(1)
        directive = m.group(2).strip()
        print(f'[thrum] immediate mutate: {parent} → {directive}')
        subprocess.run(
            [VENV_PYTHON, MUTATE_PY, '--parent', parent, '--directive', directive],
            cwd=REPO, check=False
        )
        continue

    print(f'[thrum] no handler for message (ignored)')
