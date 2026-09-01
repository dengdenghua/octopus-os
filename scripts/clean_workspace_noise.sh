#!/usr/bin/env bash
# Audit A-09: remove workspace noise and report stray root entries.
#
# Removes:
#   * .DS_Store (macOS Finder noise) anywhere under the repo
#   * __pycache__ directories and *.pyc bytecode
# Then lists root-level UNTRACKED entries so a human can gitignore or
# delete them (the root_hygiene gate tracks the committed allow-list).
#
# Safe to run anytime; never touches tracked files.
set -u

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(cd "$(dirname "$0")/.." && pwd)")"
cd "$repo_root" || exit 1

removed=0
while IFS= read -r -d '' f; do
  rm -f "$f" && removed=$((removed + 1))
done < <(find . -name .DS_Store -type f -not -path "./node_modules/*" -not -path "./.git/*" -print0)

while IFS= read -r -d '' d; do
  rm -rf "$d"
  removed=$((removed + 1))
done < <(find . -type d -name __pycache__ -not -path "./node_modules/*" -not -path "./.git/*" -not -path "./.venv/*" -print0)

find . -type f -name "*.pyc" -not -path "./node_modules/*" -not -path "./.git/*" -not -path "./.venv/*" -delete

if [ "$removed" -gt 0 ]; then
  echo "cleaned $removed noise entrie(s)"
fi

echo "--- root-level untracked entries (review & gitignore/remove) ---"
git status --porcelain | awk '$1 == "??" {print $2}' | head -40

