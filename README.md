# Telegram-бот "Коробки"

Бот для учёта коробок при переезде: создание коробок, список вещей, фото через Telegram `file_id`, поиск, статусы и QR-коды с deep link на карточку коробки.

## Локальный запуск

Требуется Python 3.11+.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`:

```env
BOT_TOKEN=токен_бота_от_BotFather
WHITELIST_USER_IDS=ваш_telegram_user_id
```

Запуск:

```bash
python -m src.main
```

База создаётся автоматически в `data/boxes.db`, логи пишутся в `logs/bot.log` с ротацией до 10 МБ на файл.

## Основные команды для теста

```bash
python -m compileall src
python -m src.main
```

В Telegram:
- `/start`
- `📦 Новая коробка`
- `🔍 Найти вещь`
- `📋 Список коробок`
- `где блендер?`

## systemd-деплой

Пример для каталога `/opt/box-bot`.

```bash
sudo mkdir -p /opt/box-bot
sudo cp -a . /opt/box-bot/
cd /opt/box-bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `/opt/box-bot/.env`, затем создайте unit-файл:

```bash
sudo nano /etc/systemd/system/box-bot.service
```

```ini
[Unit]
Description=Telegram bot for moving boxes
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/box-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/box-bot/.venv/bin/python -m src.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Запуск и автостарт:

```bash
sudo systemctl daemon-reload
sudo systemctl enable box-bot
sudo systemctl start box-bot
sudo systemctl status box-bot
```

Просмотр логов:

```bash
journalctl -u box-bot -f
tail -f /opt/box-bot/logs/bot.log
```
