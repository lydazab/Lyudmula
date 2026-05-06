#!/bin/bash
# VPS setup script: update system, install Python3/pip/git/venv, create bot user

set -e

echo "=== КРОК 1: Оновлення системи ==="
apt-get update -y
apt-get upgrade -y

echo ""
echo "=== КРОК 2: Встановлення Python3, pip, git, venv ==="
apt-get install -y python3 python3-pip python3-venv git

echo ""
echo "=== КРОК 3: Створення користувача bot ==="
if id "bot" &>/dev/null; then
    echo "Користувач bot вже існує"
else
    useradd -m -s /bin/bash bot
    echo "Користувача bot успішно створено"
fi

echo ""
echo "=== КРОК 4: Версії ==="
python3 --version
git --version
pip3 --version

echo ""
echo "=== Інформація про користувача bot ==="
id bot

echo ""
echo "=== Налаштування завершено ==="
