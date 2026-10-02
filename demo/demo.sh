#!/usr/bin/env bash
# Live demo: zero to a contribution plan in ~60 seconds.
#   GEMINI_API_KEY=... bash demo/demo.sh                 # Gemma 4 via Gemini API
#   BUDDY_PROVIDER=ollama bash demo/demo.sh              # local Gemma 4 via Ollama
#   bash demo/demo.sh --no-ai                            # facts only, no model
# Set GITHUB_TOKEN to avoid the 60 requests/hour unauthenticated GitHub limit.
set -euo pipefail

REPO="${DEMO_REPO:-mermaid-js/mermaid}"
EXTRA="${1:-}"
BUDDY="buddy"
command -v buddy >/dev/null 2>&1 || BUDDY="python -m buddy"

step() {
  printf '\n\033[1;36m$ %s\033[0m\n' "$*"
  read -r -p "(press Enter to run)" _ </dev/tty || true
  # shellcheck disable=SC2086
  $BUDDY "$@"
}

step analyze "$REPO" $EXTRA
step issues "$REPO" -n 5 $EXTRA

ISSUE_URL="$($BUDDY issues "$REPO" -n 5 --no-ai --json | python -c \
  'import json,sys; d=json.load(sys.stdin); print(d["issues"][0]["url"] if d["issues"] else "")')"
if [ -z "$ISSUE_URL" ]; then
  echo "No available beginner issues right now in $REPO. Try DEMO_REPO=owner/repo."
  exit 0
fi
step plan "$ISSUE_URL" -o plan.md $EXTRA
echo -e "\nPlan saved to plan.md. In your clone, finish with:  buddy check --pr-body pr.md"
