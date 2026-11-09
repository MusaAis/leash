#!/usr/bin/env bash
# Merge the current feature branch into main, push, tag and publish a release.
# Usage: scripts/release.sh vX.Y.Z "Release title" "Release notes"
set -euo pipefail

version="${1:?version required, for example v0.1.1}"
title="${2:?title required}"
notes="${3:?notes required}"
branch="$(git rev-parse --abbrev-ref HEAD)"

[ "$branch" != "main" ] || { echo "Run this from a feature branch."; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Commit your changes first."
  exit 1
fi

if git rev-parse -q --verify "refs/tags/${version}" >/dev/null || git ls-remote --exit-code --tags origin "${version}" >/dev/null 2>&1; then
  echo "Tag ${version} already exists."
  exit 1
fi

make check
git push -u origin "$branch"
git checkout main
git pull --ff-only origin main
git merge --no-ff "$branch" -m "merge: ${branch}"
git push
git tag -a "$version" -m "$title"
git push origin "$version"
gh release create "$version" --title "$title" --notes "$notes"
