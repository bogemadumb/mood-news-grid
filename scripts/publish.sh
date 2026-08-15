#!/usr/bin/env bash
# Публикация: снимок локальной базы уезжает на сервер.
#
# Конвейер (сбор, переписывание, сверка) живёт на машине автора, сервер только показывает
# готовое. Из этого следует главное: токен подписки, которым переписываются тексты,
# на публичной машине не нужен вообще — там нечему его читать.
#
# Снимок делается через sqlite3 .backup, а не cp: база в режиме WAL, и обычная копия
# приехала бы без последних транзакций.
set -euo pipefail

HOST="${MOOD_HOST:-root@78.17.11.130}"
KEY="${MOOD_KEY:-$HOME/.ssh/vps_claudebot}"
DEST=/opt/mood-news
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no)

cd "$(dirname "$0")/.."
SRC=data/news.db
[ -f "$SRC" ] || { echo "нет базы $SRC"; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "снимаю базу..."
sqlite3 "$SRC" ".backup '$TMP/news.db'"
sqlite3 "$TMP/news.db" "PRAGMA wal_checkpoint(TRUNCATE); VACUUM;" >/dev/null
gzip -9 -c "$TMP/news.db" > "$TMP/news.db.gz"

READY=$(sqlite3 "$TMP/news.db" "SELECT COUNT(*) FROM articles WHERE moods_ready >= (SELECT COUNT(*) FROM moods WHERE is_active=1 AND is_service=0)")
PUB=$(sqlite3 "$TMP/news.db" "SELECT COUNT(*) FROM rewrites WHERE status='published'")
SIZE=$(du -h "$TMP/news.db.gz" | cut -f1)
echo "готовых новостей: $READY, проверенных пересказов: $PUB, размер архива: $SIZE"

if [ "$READY" -lt 1 ]; then
  echo "в базе нет ни одной полностью готовой новости — публикация отменена"
  exit 1
fi

echo "заливаю..."
scp -q -i "$KEY" -o StrictHostKeyChecking=no "$TMP/news.db.gz" "$HOST:/tmp/news.db.gz"

# Подмена атомарная: распаковали рядом, переставили, перезапустили.
"${SSH[@]}" "$HOST" bash -s <<REMOTE
set -e
gunzip -f -c /tmp/news.db.gz > $DEST/data/news.db.new
rm -f /tmp/news.db.gz
chown mood:mood $DEST/data/news.db.new
systemctl stop mood-news
rm -f $DEST/data/news.db-wal $DEST/data/news.db-shm
mv $DEST/data/news.db.new $DEST/data/news.db
systemctl start mood-news
sleep 3
systemctl is-active mood-news
REMOTE

curl -sf -o /dev/null -w "https://bogemadumb.ru/mood/healthz -> HTTP %{http_code}\n" \
  https://bogemadumb.ru/mood/healthz || true
echo "опубликовано"
