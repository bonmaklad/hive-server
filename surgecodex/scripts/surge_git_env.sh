#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/surgecodex"
SURGE_SHARED_SSH_DIR="${SURGE_SHARED_SSH_DIR:-$ROOT/.ssh}"

# Never allow cron-driven git commands to hang on interactive prompts.
export GIT_TERMINAL_PROMPT=0

if [[ -d "$SURGE_SHARED_SSH_DIR" ]]; then
  config_file="$SURGE_SHARED_SSH_DIR/config"
  known_hosts_file="$SURGE_SHARED_SSH_DIR/known_hosts"
  identity_file="$SURGE_SHARED_SSH_DIR/id_ed25519"

  ssh_cmd=(ssh -F /dev/null)
  if [[ -f "$config_file" ]]; then
    ssh_cmd+=(-F "$config_file")
  fi
  if [[ -f "$known_hosts_file" ]]; then
    ssh_cmd+=(-o UserKnownHostsFile="$known_hosts_file")
  fi
  if [[ -f "$identity_file" ]]; then
    ssh_cmd+=(-i "$identity_file" -o IdentitiesOnly=yes)
  fi

  export GIT_SSH_COMMAND="${ssh_cmd[*]}"
fi
