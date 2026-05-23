#!/usr/bin/env python3
"""Interactive Pinterest OAuth setup.

Guides you through creating a Pinterest Developer app and storing the
long-lived access token in Doppler (ai-api/dev).

Steps:
  1. Create an app at https://developers.pinterest.com/
  2. Get an access token (app dashboard → Generate Token)
  3. This script stores it in Doppler and verifies it works

Usage:
    python setup_pinterest_auth.py [--doppler-project ai-api] [--doppler-config dev]
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request


PINTEREST_API = "https://api.pinterest.com/v5"


def verify_token(token: str) -> bool:
    """Test that the token can reach Pinterest API."""
    req = urllib.request.Request(
        f"{PINTEREST_API}/user_account",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            username = data.get("username", "(unknown)")
            print(f"  ✓ Connected as Pinterest user: {username}")
            return True
    except Exception as e:
        print(f"  ✗ Token verification failed: {e}")
        return False


def store_in_doppler(token: str, project: str, config: str) -> None:
    result = subprocess.run(
        ["doppler", "secrets", "set", "PINTEREST_ACCESS_TOKEN", token,
         "--project", project, "--config", config],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Doppler error: {result.stderr.strip()}")
        print(f"  Manually set: PINTEREST_ACCESS_TOKEN={token}")
    else:
        print(f"  ✓ Stored PINTEREST_ACCESS_TOKEN in Doppler {project}/{config}")


def main():
    ap = argparse.ArgumentParser(description="Pinterest OAuth setup")
    ap.add_argument("--doppler-project", default="ai-api")
    ap.add_argument("--doppler-config",  default="dev")
    ap.add_argument("--token", help="Provide token directly (skip interactive prompt)")
    args = ap.parse_args()

    print("Pinterest Agent Setup")
    print("=" * 50)

    token = args.token
    if not token:
        print("""
Steps to get a Pinterest access token:
  1. Go to https://developers.pinterest.com/apps/
  2. Create a new app (or open an existing one)
  3. In the app dashboard, click 'Generate Token'
  4. Select scopes: boards:read, pins:read
  5. Complete the OAuth flow and copy the access token
""")
        token = input("Paste your Pinterest access token: ").strip()

    if not token:
        sys.exit("No token provided.")

    print("\nVerifying token...")
    if not verify_token(token):
        sys.exit("Token invalid — re-check the value and try again.")

    print("\nStoring in Doppler...")
    store_in_doppler(token, args.doppler_project, args.doppler_config)

    print("""
Setup complete. Next steps:
  1. Find your inspiration board ID:
       doppler run --project ai-api --config dev -- python scripts/pinterest_agent.py --list-boards
  2. Set PINTEREST_BOARD_ID in Doppler or pass --board-id on each run
  3. Dry-run the agent:
       doppler run --project ai-api --config dev -- python scripts/pinterest_agent.py --board-id <ID> --dry-run
  4. Add to cron (runs every 6 hours on VM):
       0 */6 * * * cd ~/git_repo/particle_art && doppler run --project ai-api --config dev -- python scripts/pinterest_agent.py --board-id $PINTEREST_BOARD_ID
""")


if __name__ == "__main__":
    main()
