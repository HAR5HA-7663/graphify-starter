#!/usr/bin/env bash
# Set up a Linux machine (e.g. a Raspberry Pi) as a read-mostly REPLICA of a brain that
# already lives in a private git repo. It pulls/pushes markdown every 10 min and rebuilds
# its own .chroma from vectors/ — it never embeds, so it needs no OpenAI key.
#
#   ./linux/install-replica.sh git@github.com:<you>/<private-brain-repo>.git
#
# Pushing needs write access: add ~/.ssh/brain_deploy.pub (printed below) to the repo as a
# deploy key with write access, then re-run.
set -euo pipefail
REMOTE="${1:?usage: install-replica.sh <git remote of your private brain repo>}"
BRAIN="${BRAIN_ROOT:-$HOME/brain}"

[ -f ~/.ssh/brain_deploy ] || ssh-keygen -q -t ed25519 -N "" -C "brain-sync@$(hostname -s)" -f ~/.ssh/brain_deploy
grep -q "Host github-brain" ~/.ssh/config 2>/dev/null || \
  printf "\nHost github-brain\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/brain_deploy\n  IdentitiesOnly yes\n" >> ~/.ssh/config
chmod 600 ~/.ssh/config
REMOTE="${REMOTE/git@github.com:/git@github-brain:}"
if ! ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T github-brain 2>&1 | grep -q "successfully authenticated"; then
  echo "Add this deploy key (with write access) to the repo, then re-run:"; cat ~/.ssh/brain_deploy.pub; exit 1
fi

if [ ! -d "$BRAIN/.git" ]; then
  mkdir -p "$BRAIN" && cd "$BRAIN"
  git init -q -b main && git remote add origin "$REMOTE"
  git fetch -q origin && git reset -q origin/main && git checkout -q -- .
fi
cd "$BRAIN"
git config user.name "brain-sync ($(hostname -s))"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p ~/.config && echo replica > ~/.config/brain-sync-role
bash scripts/brain_sync.sh && .venv/bin/python -m scripts.vector_sync import --force
(crontab -l 2>/dev/null | grep -v brain_sync.sh; echo "*/10 * * * * /bin/bash $BRAIN/scripts/brain_sync.sh >/dev/null 2>&1") | crontab -
echo "Replica ready: $BRAIN (syncs every 10 min). Query: cd $BRAIN && .venv/bin/python -m scripts.query \"...\""
