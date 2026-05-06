# Telegram-бот на Claude

Головний файл — bot.py. Залежності — requirements.txt.

## Сервер
Бот працює на VPS DigitalOcean.
- Шлях: /root/Lyudmula
- Сервіс: mybot.service (systemd)
- Автодеплой: /root/autodeploy.sh запускається щохвилини,
  робить git pull і перезапускає сервіс при змінах.

## Git Relay
Механізм для виконання команд на сервері через GitHub без термінала.
- `cmd_runner.py` — скрипт, що раз на 5 секунд читає `cmds/pending.json` через GitHub API
- Якщо `id` у pending відрізняється від попереднього — виконує `cmd` через shell
- Результат (stdout, stderr, returncode, ts) пише у `cmds/result.json`
- Формат pending: `{"id":"унікальний-id","cmd":"команда"}`
- Формат result: `{"id":"...", "stdout":"...", "stderr":"...", "returncode":0, "ts":"..."}`
- Сервіс: cmdrunner.service (systemd)

## Workflow
Усе через GitHub: коміт у main → за хвилину сервер оновиться.
Руками до сервера не лізьмо.

## Користувач
Говорить українською, не програміст — пояснювати простими словами.
