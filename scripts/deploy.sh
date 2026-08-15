#!/usr/bin/env bash
# Выкладка на сервер. Требует ssh-доступа root@78.17.11.130 по ключу vps_claudebot.
set -euo pipefail
HOST="${MOOD_HOST:-root@78.17.11.130}"
KEY="${MOOD_KEY:-$HOME/.ssh/vps_claudebot}"
DEST=/opt/mood-news

cd "$(dirname "$0")/.."
SHA=$(git rev-parse --short HEAD)

rsync -az --delete \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
  --exclude 'data/news.db*' --exclude 'data/snapshots' --exclude '.DS_Store' \
  --exclude 'logs' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
  ./ "$HOST:$DEST/"

ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" bash -s <<REMOTE
set -e
cd $DEST
# systemd не стартует, если каталога из ReadWritePaths нет: rsync --delete его вычищает.
mkdir -p $DEST/data $DEST/logs
.venv/bin/python -m pip install -q -e . 2>/dev/null || /usr/local/bin/uv pip install -q -e .
grep -q '^GIT_SHA=' /etc/mood-news.env 2>/dev/null \
  && sed -i "s/^GIT_SHA=.*/GIT_SHA=$SHA/" /etc/mood-news.env \
  || echo "GIT_SHA=$SHA" >> /etc/mood-news.env
chown -R mood:mood $DEST
systemctl restart mood-news
sleep 2
systemctl is-active mood-news
REMOTE

curl -sf -o /dev/null -w "https://bogemadumb.ru/mood/healthz -> HTTP %{http_code}\n" \
  https://bogemadumb.ru/mood/healthz
echo "выложена сборка $SHA"
