#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/surgecodex"
CLIENTS_ROOT="$ROOT/clients"

source "$ROOT/scripts/surge_git_env.sh"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <client-slug>" >&2
  exit 64
fi

slug="$1"
repo="$CLIENTS_ROOT/$slug"

if [[ "$PWD" != "$ROOT" && "$PWD" != "$repo" ]]; then
  echo "this script may only be run from $ROOT or a client repo under it" >&2
  exit 65
fi

if [[ ! -d "$repo/.git" ]]; then
  echo "missing git repo for client: $slug" >&2
  exit 66
fi

if [[ "$repo" != "$CLIENTS_ROOT/"* ]]; then
  echo "refusing to run outside clients root" >&2
  exit 67
fi

origin_url="$(git -C "$repo" remote get-url origin 2>/dev/null || true)"

if [[ -z "$origin_url" ]]; then
  echo "missing origin remote for client: $slug" >&2
  exit 71
fi

if [[ "$origin_url" == https://github.com/* ]]; then
  echo "origin remote for $slug uses HTTPS ($origin_url); configure SSH under /opt/surgecodex/.ssh and switch the remote to git@github.com:OWNER/REPO.git for cron-safe auth" >&2
  exit 72
fi

current_branch="$(git -C "$repo" branch --show-current)"

if [[ -z "$current_branch" ]]; then
  echo "unable to determine current branch" >&2
  exit 68
fi

tracked_dirty="$(git -C "$repo" status --porcelain)"

if [[ -n "$tracked_dirty" ]]; then
  echo "repo has uncommitted changes, refusing to sync" >&2
  exit 69
fi

git -C "$repo" fetch --all --prune

if git -C "$repo" show-ref --verify --quiet refs/remotes/origin/dev; then
  if [[ "$current_branch" != "dev" ]]; then
    git -C "$repo" checkout dev
  fi
  git -C "$repo" fetch origin dev
  git -C "$repo" merge --ff-only FETCH_HEAD
  exit 0
fi

if git -C "$repo" show-ref --verify --quiet refs/remotes/origin/main; then
  if git -C "$repo" show-ref --verify --quiet refs/heads/dev; then
    git -C "$repo" checkout dev
  else
    git -C "$repo" checkout -b dev origin/main
  fi
  git -C "$repo" push -u origin dev
  git -C "$repo" fetch origin dev
  git -C "$repo" merge --ff-only FETCH_HEAD
  exit 0
fi

if git -C "$repo" show-ref --verify --quiet refs/remotes/origin/master; then
  if git -C "$repo" show-ref --verify --quiet refs/heads/dev; then
    git -C "$repo" checkout dev
  else
    git -C "$repo" checkout -b dev origin/master
  fi
  git -C "$repo" push -u origin dev
  git -C "$repo" fetch origin dev
  git -C "$repo" merge --ff-only FETCH_HEAD
  exit 0
fi

echo "no safe sync target found for $slug" >&2
exit 70
