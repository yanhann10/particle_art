#!/usr/bin/env bash
# Quick deploy: commit all changes and push to Vercel.
# Usage: ./scripts/deploy.sh "message"
#   or:  ./scripts/deploy.sh   (opens diff for confirmation)
set -euo pipefail

MSG="${1:-}"
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"

if [[ -z "$(git status --short)" ]]; then
  echo "Nothing to commit." && exit 0
fi

if [[ -z "$MSG" ]]; then
  git status --short | head -20
  echo ""
  read -rp "Commit message: " MSG
  [[ -z "$MSG" ]] && echo "Aborted." && exit 1
fi

git add -A
git commit -m "$MSG

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
git push origin main

# Print Vercel URL for quick browser check
REPO_NAME=$(basename "$REPO")
echo ""
echo "Pushed. Vercel build starting — check:"
echo "  https://${REPO_NAME}.vercel.app"
echo "  (or your custom domain)"
