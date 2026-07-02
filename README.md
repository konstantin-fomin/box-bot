# Telegram-бот "Коробки"

Бот для учёта коробок при переезде: создание групп, коробок, списков вещей, фото через Telegram `file_id`, поиск, статусы и QR-коды с deep link на карточку коробки.

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
- `/help`
- `🏠 Создать свою группу` или `🔑 У меня есть код приглашения`
- `📦 Новая коробка`
- `🔍 Найти вещь`
- `📋 Список коробок`
- `⚙️ Ещё` → `👥 Моя группа`
- `где блендер?`

Новый пользователь при первом `/start` видит приветствие и выбирает: создать свою группу или присоединиться по invite-коду. Пользователь в группе сразу получает главное меню. `/help` доступна и до вступления в группу.

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

## Troubleshooting

### SQLite зависает в sandbox-среде

В ограниченной sandbox-среде, где разрабатывался проект, SQLite-операции могли зависать до выполнения SQL. Это ограничение окружения, а не ожидаемое поведение на обычном VPS. В коде используется стандартный `sqlite3` в single-worker executor, а также добавлены:
- явное создание папки `data/` перед подключением к SQLite;
- `timeout=10` для подключения к SQLite;
- ограничение ожидания подключения и понятная ошибка в лог `logs/bot.log`.

Для реального теста на VPS выполните:

```bash
cd /opt/box-bot
source .venv/bin/activate
python - <<'PY'
import asyncio
from pathlib import Path
from src.database import create_box, create_household, init_db, search_boxes

async def main():
    db_path = Path("data/boxes.db")
    await init_db(db_path)
    household = await create_household(db_path, "Тестовая группа", 123456789)
    box = await create_box(
        db_path,
        household_id=household.id,
        prefix="TEST",
        room="Тест",
        items=["проверочная вещь"],
        photo_file_ids=[],
    )
    found = await search_boxes(db_path, "проверочная", household.id)
    print(box.code, len(found))

asyncio.run(main())
PY
```

Ожидаемый результат: команда завершается без зависания и печатает код тестовой коробки, например `TEST-01 1`.
