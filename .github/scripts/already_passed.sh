#!/usr/bin/env bash
# Writes run=false to GITHUB_OUTPUT when this is a tag push and this workflow
# already passed on a branch push of the same commit, so a release tag does not
# repeat checks main has just run. Anything else, an API failure included, runs.
set -euo pipefail
run=true
if [ "$GITHUB_EVENT_NAME" = push ] && [ "$GITHUB_REF_TYPE" = tag ]; then
  if passed=$(gh api -X GET "repos/$GITHUB_REPOSITORY/actions/runs" \
    -f head_sha="$GITHUB_SHA" -f event=push -f status=success -f per_page=100 \
    --jq "[.workflow_runs[] | select(.name == env.GITHUB_WORKFLOW and .head_branch != env.GITHUB_REF_NAME)] | length"); then
    if [ "$passed" -gt 0 ]; then
      run=false
      echo "$GITHUB_WORKFLOW already passed for $GITHUB_SHA on a branch push; skipping."
    fi
  else
    echo "::warning::Could not query earlier runs; running the checks."
  fi
fi
echo "run=$run" >> "$GITHUB_OUTPUT"
