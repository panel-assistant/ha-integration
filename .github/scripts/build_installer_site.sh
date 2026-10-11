#!/usr/bin/env bash
# Build the whole installer site from release tags: every release that opens its
# own copy gets <version>/, built from that tag, and the root serves the newest
# one for releases up to 0.9.0, which open the root. A deploy replaces the whole site, so
# rebuilding every copy is what keeps the earlier ones published.
#
# Usage: build_installer_site.sh <output-directory>
#        build_installer_site.sh --list-tags   (print the tags that get a copy)
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
frontend=custom_components/panel_assistant/frontend
# Only a release whose default address carries its own version has a copy.
marker='install.panel-assistant.io/{INTEGRATION_VERSION}/'

# The one rule for which tags get a copy, oldest first. A prerelease sorts
# before its release, so the root never moves back to one.
eligible_tags() {
  local tag
  for tag in $(git -C "$repo" -c versionsort.suffix=- tag --list --sort=version:refname); do
    if git -C "$repo" grep -q -F "$marker" "$tag" -- custom_components/panel_assistant/browser_panel.py; then
      echo "$tag"
    fi
  done
}

if [ "${1:-}" = --list-tags ]; then
  eligible_tags
  exit 0
fi

out=$(realpath -m "$1")
work=$(mktemp -d)
trap 'git -C "$repo" worktree prune; rm -rf "$work"' EXIT

rm -rf "$out"
mkdir -p "$out"
newest=
for tag in $(eligible_tags); do
  git -C "$repo" worktree add --quiet --detach "$work/$tag" "$tag"
  (cd "$work/$tag/$frontend" && npm ci --ignore-scripts --no-audit --no-fund >/dev/null && npx vite build >/dev/null)
  cp -R "$work/$tag/$frontend/dist" "$out/$tag"
  git -C "$repo" worktree remove --force "$work/$tag"
  newest=$tag
  echo "built $tag"
done

if [ -z "$newest" ]; then
  echo "no release tag opens its own installer copy" >&2
  exit 1
fi
cp -R "$out/$newest/." "$out/"
echo "root serves $newest"
