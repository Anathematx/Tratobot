#!/usr/bin/env bash
# Установка бота на чистый сервер Ubuntu. Запускать от root из папки репозитория:
#   bash deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
    echo "==> Устанавливаю Docker (1–2 минуты)..."
    curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
    echo
    read -rp "Вставьте токен бота от @BotFather: " BOT_TOKEN
    read -rp "Ваш Telegram ID (число от @userinfobot): " ALLOWED_USERS
    read -rp "Дни зарплаты и аванса через запятую, например 5,20 (Enter — определить автоматически): " PAYDAYS
    sed -e "s|^BOT_TOKEN=.*|BOT_TOKEN=${BOT_TOKEN}|" \
        -e "s|^ALLOWED_USERS=.*|ALLOWED_USERS=${ALLOWED_USERS}|" \
        -e "s|^PAYDAYS=.*|PAYDAYS=${PAYDAYS}|" \
        .env.example > .env
    chmod 600 .env
fi

mkdir -p data backups
echo "==> Собираю и запускаю бота..."
docker compose up -d --build

# Ежедневная резервная копия базы в 03:00, хранится 30 последних
CRON="0 3 * * * cp $(pwd)/data/expenses.db $(pwd)/backups/expenses-\$(date +\\%F).db 2>/dev/null; ls -1t $(pwd)/backups/*.db | tail -n +31 | xargs -r rm"
( crontab -l 2>/dev/null | grep -v "tratobot-backup" ; echo "$CRON # tratobot-backup" ) | crontab -

sleep 5
docker compose ps
echo
echo "Готово! Напишите боту /start в Telegram."
echo "Логи: cd $(pwd) && docker compose logs -f"
