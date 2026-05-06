#!/bin/bash
cd /root/Lyudmula || exit 1
BEFORE=$(git rev-parse HEAD)
git pull --quiet
AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" != "$AFTER" ]; then
    /root/Lyudmula/venv/bin/pip install -q -r requirements.txt
    systemctl restart mybot.service
fi
