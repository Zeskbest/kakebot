#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/tim/kakebo"
BRANCH="main"
SERVICE="kakebo-tg-bot"

cd "$REPO_DIR"

# Ensure this is a git repo on the expected branch
current_branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$current_branch" != "$BRANCH" ]; then
    echo "Not on $BRANCH branch (on $current_branch), skipping update."
    exit 0
fi

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Working tree has uncommitted changes, skipping update."
    exit 0
fi

git fetch origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "Already up to date."
    exit 0
fi

echo "Updating $LOCAL -> $REMOTE"
git pull origin "$BRANCH"

# Reinstall dependencies if requirements changed
if git diff --name-only "$LOCAL" "$REMOTE" | grep -q 'requirements.txt'; then
    echo "requirements.txt changed, installing dependencies..."
    "$REPO_DIR/venv/bin/pip" install -r requirements.txt
fi

echo "Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"
echo "Done."