#!/usr/bin/env python3
"""Send tick completion + piece thumbnails to @Hyaninny_bot.
Usage: notify_telegram.py <code1> [code2 ...] [--budget <msg>]
Called by parallel_tick.sh after each cron tick.
"""
import os, sys, json, requests
from pathlib import Path

TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_ID = os.environ['ALLOWED_CHAT_ID']
REPO    = Path(os.environ.get('PARTICLE_ART_REPO', '/home/ubuntu/git_repo/particle_art'))
API     = f'https://api.telegram.org/bot{TOKEN}'

def send_photo(path, caption):
    with open(path, 'rb') as f:
        r = requests.post(f'{API}/sendPhoto', data={
            'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'Markdown',
        }, files={'photo': f}, timeout=20)
    return r.ok

def send_text(text):
    requests.post(f'{API}/sendMessage', data={
        'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown',
    }, timeout=10)

def piece_caption(code):
    meta_path = REPO / 'pieces' / code / 'meta.json'
    lines = [f'🎨 `{code}`']
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        directive = meta.get('mutation_directive') or meta.get('direction', '')
        parent    = meta.get('parent_id', '')
        mode      = meta.get('mode', '')
        if directive:
            lines.append(f'_{directive}_')
        detail = ' · '.join(filter(None, [f'← {parent}' if parent else '', mode]))
        if detail:
            lines.append(detail)
    lines.append('')
    lines.append(f'Reply: `keep {code}` / `drop {code}` / `mutate {code} → <direction>`')
    return '\n'.join(lines)

piece_ids = [a for a in sys.argv[1:] if not a.startswith('--')]

if not piece_ids:
    send_text('🔁 tick complete — no new pieces this run')
    sys.exit(0)

for code in piece_ids[:4]:   # cap at 4 to avoid flooding
    thumb = REPO / 'thumbs' / f'{code}.png'
    caption = piece_caption(code)
    if thumb.exists():
        if not send_photo(thumb, caption):
            send_text(caption)   # fallback to text if photo fails
    else:
        send_text(caption)

if len(piece_ids) > 4:
    send_text(f'_...and {len(piece_ids)-4} more pieces this tick_')
