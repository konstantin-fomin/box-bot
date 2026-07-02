from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import secrets
import sqlite3
import string
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


LOGGER = logging.getLogger(__name__)
DB_CONNECT_TIMEOUT_SECONDS = 10
_T = TypeVar("_T")

STATUSES = ("home", "storage", "moving", "unpacked")


STATUS_LABELS = {
    "home": "🏠 дома",
    "storage": "📦 склад",
    "moving": "🚚 в пути",
    "unpacked": "✅ распаковано",
}

_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    room TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'home',
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE,
    UNIQUE (household_id, code)
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (box_id) REFERENCES boxes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    FOREIGN KEY (box_id) REFERENCES boxes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    welcome_seen INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS room_counters (
    household_id INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    last_number INTEGER NOT NULL,
    PRIMARY KEY (household_id, prefix),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS household_members (
    household_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (household_id, user_id),
    UNIQUE (user_id),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_boxes_household_id ON boxes(household_id);
CREATE INDEX IF NOT EXISTS idx_boxes_room ON boxes(room);
CREATE INDEX IF NOT EXISTS idx_boxes_status ON boxes(status);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
CREATE INDEX IF NOT EXISTS idx_photos_box_id ON photos(box_id);
CREATE INDEX IF NOT EXISTS idx_household_members_user_id ON household_members(user_id);
"""


@dataclass(frozen=True)
class Box:
    id: int
    code: str
    room: str
    status: str
    created_at: str
    items: tuple[str, ...]
    photos: tuple[str, ...]


@dataclass(frozen=True)
class Household:
    id: int
    name: str
    invite_code: str
    created_at: str


@dataclass(frozen=True)
class HouseholdMember:
    user_id: int
    name: str | None
    joined_at: str


class DatabaseCursor:
    def __init__(self, db: "Database", cursor: sqlite3.Cursor):
        self._db = db
        self._cursor = cursor

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    async def fetchone(self) -> sqlite3.Row | None:
        return await self._db._run(self._cursor.fetchone)

    async def fetchall(self) -> list[sqlite3.Row]:
        return await self._db._run(self._cursor.fetchall)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn: sqlite3.Connection | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-db")
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await self._await_future(
            self._executor.submit(
                partial(sqlite3.connect, self.path, timeout=DB_CONNECT_TIMEOUT_SECONDS, check_same_thread=False)
            )
        )
        self._conn.row_factory = sqlite3.Row

    async def _await_future(self, future: concurrent.futures.Future[_T]) -> _T:
        while not future.done():
            await asyncio.sleep(0.001)
        return future.result()

    async def _run(self, func: Callable[..., _T], *args: Any) -> _T:
        if self._conn is None:
            raise RuntimeError("Database connection is not open")
        async with self._lock:
            return await self._await_future(self._executor.submit(partial(func, *args)))

    async def execute(self, query: str, params: Iterable[Any] = ()) -> DatabaseCursor:
        return DatabaseCursor(self, await self._run(self._conn.execute, query, tuple(params)))

    async def executescript(self, script: str) -> None:
        await self._run(self._conn.executescript, script)

    async def commit(self) -> None:
        await self._run(self._conn.commit)

    async def fetchone(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cursor = await self.execute(query, params)
        return await cursor.fetchone()

    async def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cursor = await self.execute(query, params)
        return await cursor.fetchall()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._run(self._conn.close)
        self._conn = None
        self._executor.shutdown(wait=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_invite_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


async def _wait_for_db(awaitable, description: str, db_path: Path):
    try:
        return await asyncio.wait_for(awaitable, timeout=DB_CONNECT_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        LOGGER.exception(
            "SQLite timeout за %s секунд при операции «%s»: %s",
            DB_CONNECT_TIMEOUT_SECONDS,
            description,
            db_path,
        )
        raise RuntimeError(
            f"SQLite не ответила за {DB_CONNECT_TIMEOUT_SECONDS} секунд при операции «{description}»: {db_path}"
        ) from exc


@asynccontextmanager
async def connect_db(db_path: Path) -> AsyncIterator[Database]:
    os.makedirs(db_path.parent, exist_ok=True)
    db = Database(db_path)
    try:
        await _wait_for_db(db.connect(), "подключение", db_path)
    except TimeoutError as exc:
        LOGGER.exception(
            "Не удалось подключиться к SQLite за %s секунд: %s",
            DB_CONNECT_TIMEOUT_SECONDS,
            db_path,
        )
        raise RuntimeError(
            f"Не удалось подключиться к SQLite за {DB_CONNECT_TIMEOUT_SECONDS} секунд: {db_path}"
        ) from exc
    except Exception as exc:
        LOGGER.exception("Не удалось подключиться к SQLite: %s", db_path)
        raise RuntimeError(f"Не удалось подключиться к SQLite: {db_path}") from exc

    try:
        yield db
    finally:
        await _wait_for_db(db.close(), "закрытие подключения", db_path)


async def init_db(db_path: Path) -> None:
    os.makedirs(db_path.parent, exist_ok=True)
    async with connect_db(db_path) as db:
        await _wait_for_db(db.execute("PRAGMA foreign_keys = ON"), "PRAGMA foreign_keys", db_path)
        await _wait_for_db(db.executescript(_MIGRATIONS_SQL), "миграции", db_path)
        await _wait_for_db(_ensure_user_columns(db), "миграции users", db_path)
        await _wait_for_db(db.commit(), "commit миграций", db_path)


async def _ensure_user_columns(db: Database) -> None:
    columns = await db.fetchall("PRAGMA table_info(users)")
    if "welcome_seen" not in {column["name"] for column in columns}:
        await db.execute("ALTER TABLE users ADD COLUMN welcome_seen INTEGER NOT NULL DEFAULT 0")


async def upsert_user(db_path: Path, user_id: int, name: str | None) -> None:
    async with connect_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET name = excluded.name
            """,
            (user_id, name),
        )
        await db.commit()


async def has_user_seen_welcome(db_path: Path, user_id: int) -> bool:
    async with connect_db(db_path) as db:
        row = await db.fetchone("SELECT welcome_seen FROM users WHERE user_id = ?", (user_id,))
        return bool(row["welcome_seen"]) if row else False


async def mark_user_welcome_seen(db_path: Path, user_id: int) -> None:
    async with connect_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, welcome_seen)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET welcome_seen = 1
            """,
            (user_id,),
        )
        await db.commit()


async def create_household(db_path: Path, name: str, creator_user_id: int) -> Household:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Название группы не может быть пустым")

    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("BEGIN IMMEDIATE")
        invite_code = generate_invite_code()
        while await db.fetchone("SELECT id FROM households WHERE invite_code = ?", (invite_code,)):
            invite_code = generate_invite_code()

        await db.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, NULL)", (creator_user_id,))
        cursor = await db.execute(
            "INSERT INTO households (name, invite_code, created_at) VALUES (?, ?, ?)",
            (clean_name, invite_code, utc_now()),
        )
        household_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO household_members (household_id, user_id, joined_at) VALUES (?, ?, ?)",
            (household_id, creator_user_id, utc_now()),
        )
        await db.commit()

    household = await get_household_by_id(db_path, int(household_id))
    if household is None:
        raise RuntimeError("Группа не найдена после создания")
    return household


async def get_household_by_id(db_path: Path, household_id: int) -> Household | None:
    async with connect_db(db_path) as db:
        row = await db.fetchone(
            "SELECT id, name, invite_code, created_at FROM households WHERE id = ?",
            (household_id,),
        )
        return _row_to_household(row) if row else None


async def get_household_by_invite_code(db_path: Path, invite_code: str) -> Household | None:
    async with connect_db(db_path) as db:
        row = await db.fetchone(
            "SELECT id, name, invite_code, created_at FROM households WHERE UPPER(invite_code) = UPPER(?)",
            (invite_code.strip(),),
        )
        return _row_to_household(row) if row else None


async def get_user_household(db_path: Path, user_id: int) -> Household | None:
    async with connect_db(db_path) as db:
        row = await db.fetchone(
            """
            SELECT h.id, h.name, h.invite_code, h.created_at
            FROM household_members hm
            JOIN households h ON h.id = hm.household_id
            WHERE hm.user_id = ?
            LIMIT 1
            """,
            (user_id,),
        )
        return _row_to_household(row) if row else None


async def join_household(db_path: Path, household_id: int, user_id: int) -> Household | None:
    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("BEGIN IMMEDIATE")
        household_row = await db.fetchone(
            "SELECT id, name, invite_code, created_at FROM households WHERE id = ?",
            (household_id,),
        )
        if household_row is None:
            await db.commit()
            return None

        await db.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, NULL)", (user_id,))
        await db.execute("DELETE FROM household_members WHERE user_id = ?", (user_id,))
        await db.execute(
            "INSERT INTO household_members (household_id, user_id, joined_at) VALUES (?, ?, ?)",
            (household_id, user_id, utc_now()),
        )
        await db.commit()
        return _row_to_household(household_row)


async def list_household_members(db_path: Path, household_id: int) -> list[HouseholdMember]:
    async with connect_db(db_path) as db:
        rows = await db.fetchall(
            """
            SELECT hm.user_id, u.name, hm.joined_at
            FROM household_members hm
            LEFT JOIN users u ON u.user_id = hm.user_id
            WHERE hm.household_id = ?
            ORDER BY hm.joined_at, hm.user_id
            """,
            (household_id,),
        )
        return [HouseholdMember(user_id=row["user_id"], name=row["name"], joined_at=row["joined_at"]) for row in rows]


async def create_box(
    db_path: Path,
    *,
    household_id: int,
    prefix: str,
    room: str,
    items: Iterable[str],
    photo_file_ids: Iterable[str],
) -> Box:
    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT last_number FROM room_counters WHERE household_id = ? AND prefix = ?",
            (household_id, prefix),
        )
        row = await cursor.fetchone()
        next_number = (row[0] if row else 0) + 1
        if row:
            await db.execute(
                "UPDATE room_counters SET last_number = ? WHERE household_id = ? AND prefix = ?",
                (next_number, household_id, prefix),
            )
        else:
            await db.execute(
                "INSERT INTO room_counters (household_id, prefix, last_number) VALUES (?, ?, ?)",
                (household_id, prefix, next_number),
            )

        code = f"{prefix}-{next_number:02d}"
        cursor = await db.execute(
            "INSERT INTO boxes (household_id, code, room, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (household_id, code, room, "home", utc_now()),
        )
        box_id = cursor.lastrowid

        for item in items:
            clean_item = item.strip()
            if clean_item:
                await db.execute(
                    "INSERT INTO items (box_id, name) VALUES (?, ?)",
                    (box_id, clean_item),
                )

        for file_id in photo_file_ids:
            await db.execute(
                "INSERT INTO photos (box_id, file_id) VALUES (?, ?)",
                (box_id, file_id),
            )

        await db.commit()

    box = await get_box_by_code(db_path, code, household_id)
    if box is None:
        raise RuntimeError(f"Коробка {code} не найдена после создания")
    return box


async def get_box_by_code(db_path: Path, code: str, household_id: int) -> Box | None:
    async with connect_db(db_path) as db:
        cursor = await db.execute(
            """
            SELECT id, code, room, status, created_at
            FROM boxes
            WHERE household_id = ? AND UPPER(code) = UPPER(?)
            """,
            (household_id, code),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await _hydrate_box(db, row)


async def get_box_by_id(db_path: Path, box_id: int, household_id: int) -> Box | None:
    async with connect_db(db_path) as db:
        cursor = await db.execute(
            "SELECT id, code, room, status, created_at FROM boxes WHERE id = ? AND household_id = ?",
            (box_id, household_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await _hydrate_box(db, row)


async def list_boxes(
    db_path: Path,
    *,
    household_id: int,
    status: str | None = None,
    room: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Box]:
    filters: list[str] = ["household_id = ?"]
    params: list[object] = [household_id]
    if status:
        filters.append("status = ?")
        params.append(status)
    if room:
        filters.append("room = ?")
        params.append(room)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.extend([limit, offset])

    async with connect_db(db_path) as db:
        rows = await db.fetchall(
            f"""
            SELECT id, code, room, status, created_at
            FROM boxes
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        return [await _hydrate_box(db, row) for row in rows]


async def list_all_boxes(db_path: Path, household_id: int) -> list[Box]:
    async with connect_db(db_path) as db:
        rows = await db.fetchall(
            """
            SELECT id, code, room, status, created_at
            FROM boxes
            WHERE household_id = ?
            ORDER BY room COLLATE NOCASE, code COLLATE NOCASE, id
            """,
            (household_id,),
        )
        return [await _hydrate_box(db, row) for row in rows]


async def list_rooms(db_path: Path, household_id: int) -> list[str]:
    async with connect_db(db_path) as db:
        rows = await db.fetchall(
            "SELECT DISTINCT room FROM boxes WHERE household_id = ? ORDER BY room",
            (household_id,),
        )
        return [row[0] for row in rows]


async def search_boxes(db_path: Path, query: str, household_id: int, limit: int = 10) -> list[Box]:
    like_query = f"%{query.strip().lower()}%"
    async with connect_db(db_path) as db:
        rows = await db.fetchall(
            """
            SELECT DISTINCT b.id, b.code, b.room, b.status, b.created_at
            FROM boxes b
            LEFT JOIN items i ON i.box_id = b.id
            WHERE b.household_id = ?
              AND (
                   LOWER(b.code) LIKE ?
                OR LOWER(b.room) LIKE ?
                OR LOWER(i.name) LIKE ?
              )
            ORDER BY b.created_at DESC, b.id DESC
            LIMIT ?
            """,
            (household_id, like_query, like_query, like_query, limit),
        )
        return [await _hydrate_box(db, row) for row in rows]


async def add_items(db_path: Path, box_id: int, household_id: int, items: Iterable[str]) -> bool:
    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        if await db.fetchone("SELECT id FROM boxes WHERE id = ? AND household_id = ?", (box_id, household_id)) is None:
            return False
        for item in items:
            clean_item = item.strip()
            if clean_item:
                await db.execute(
                    "INSERT INTO items (box_id, name) VALUES (?, ?)",
                    (box_id, clean_item),
                )
        await db.commit()
        return True


async def add_photos(db_path: Path, box_id: int, household_id: int, file_ids: Iterable[str]) -> bool:
    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        if await db.fetchone("SELECT id FROM boxes WHERE id = ? AND household_id = ?", (box_id, household_id)) is None:
            return False
        for file_id in file_ids:
            await db.execute(
                "INSERT INTO photos (box_id, file_id) VALUES (?, ?)",
                (box_id, file_id),
            )
        await db.commit()
        return True


async def update_status(db_path: Path, box_id: int, household_id: int, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус: {status}")
    async with connect_db(db_path) as db:
        cursor = await db.execute(
            "UPDATE boxes SET status = ? WHERE id = ? AND household_id = ?",
            (status, box_id, household_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_box(db_path: Path, box_id: int, household_id: int) -> bool:
    async with connect_db(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cursor = await db.execute("DELETE FROM boxes WHERE id = ? AND household_id = ?", (box_id, household_id))
        await db.commit()
        return cursor.rowcount > 0


def _row_to_household(row: sqlite3.Row) -> Household:
    return Household(
        id=row["id"],
        name=row["name"],
        invite_code=row["invite_code"],
        created_at=row["created_at"],
    )


async def _hydrate_box(db: Database, row: sqlite3.Row) -> Box:
    item_rows = await db.fetchall(
        "SELECT name FROM items WHERE box_id = ? ORDER BY id",
        (row["id"],),
    )
    photo_rows = await db.fetchall(
        "SELECT file_id FROM photos WHERE box_id = ? ORDER BY id",
        (row["id"],),
    )
    return Box(
        id=row["id"],
        code=row["code"],
        room=row["room"],
        status=row["status"],
        created_at=row["created_at"],
        items=tuple(item["name"] for item in item_rows),
        photos=tuple(photo["file_id"] for photo in photo_rows),
    )
