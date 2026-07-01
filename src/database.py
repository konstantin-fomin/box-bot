from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiosqlite


STATUSES = ("home", "storage", "moving", "unpacked")


STATUS_LABELS = {
    "home": "🏠 дома",
    "storage": "📦 склад",
    "moving": "🚚 в пути",
    "unpacked": "✅ распаковано",
}


@dataclass(frozen=True)
class Box:
    id: int
    code: str
    room: str
    status: str
    created_at: str
    items: tuple[str, ...]
    photos: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS boxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                room TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'home',
                created_at TEXT NOT NULL
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
                name TEXT
            );

            CREATE TABLE IF NOT EXISTS room_counters (
                prefix TEXT PRIMARY KEY,
                last_number INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_boxes_room ON boxes(room);
            CREATE INDEX IF NOT EXISTS idx_boxes_status ON boxes(status);
            CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
            CREATE INDEX IF NOT EXISTS idx_photos_box_id ON photos(box_id);
            """
        )
        await db.commit()


async def upsert_user(db_path: Path, user_id: int, name: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET name = excluded.name
            """,
            (user_id, name),
        )
        await db.commit()


async def create_box(
    db_path: Path,
    *,
    prefix: str,
    room: str,
    items: Iterable[str],
    photo_file_ids: Iterable[str],
) -> Box:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT last_number FROM room_counters WHERE prefix = ?",
            (prefix,),
        )
        row = await cursor.fetchone()
        next_number = (row[0] if row else 0) + 1
        if row:
            await db.execute(
                "UPDATE room_counters SET last_number = ? WHERE prefix = ?",
                (next_number, prefix),
            )
        else:
            await db.execute(
                "INSERT INTO room_counters (prefix, last_number) VALUES (?, ?)",
                (prefix, next_number),
            )

        code = f"{prefix}-{next_number:02d}"
        cursor = await db.execute(
            "INSERT INTO boxes (code, room, status, created_at) VALUES (?, ?, ?, ?)",
            (code, room, "home", utc_now()),
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

    box = await get_box_by_code(db_path, code)
    if box is None:
        raise RuntimeError(f"Коробка {code} не найдена после создания")
    return box


async def get_box_by_code(db_path: Path, code: str) -> Box | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, code, room, status, created_at FROM boxes WHERE UPPER(code) = UPPER(?)",
            (code,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await _hydrate_box(db, row)


async def get_box_by_id(db_path: Path, box_id: int) -> Box | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, code, room, status, created_at FROM boxes WHERE id = ?",
            (box_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await _hydrate_box(db, row)


async def list_boxes(
    db_path: Path,
    *,
    status: str | None = None,
    room: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Box]:
    filters: list[str] = []
    params: list[object] = []
    if status:
        filters.append("status = ?")
        params.append(status)
    if room:
        filters.append("room = ?")
        params.append(room)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.extend([limit, offset])

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
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


async def list_rooms(db_path: Path) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall("SELECT DISTINCT room FROM boxes ORDER BY room")
        return [row[0] for row in rows]


async def search_boxes(db_path: Path, query: str, limit: int = 10) -> list[Box]:
    like_query = f"%{query.strip().lower()}%"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT DISTINCT b.id, b.code, b.room, b.status, b.created_at
            FROM boxes b
            LEFT JOIN items i ON i.box_id = b.id
            WHERE LOWER(b.code) LIKE ?
               OR LOWER(b.room) LIKE ?
               OR LOWER(i.name) LIKE ?
            ORDER BY b.created_at DESC, b.id DESC
            LIMIT ?
            """,
            (like_query, like_query, like_query, limit),
        )
        return [await _hydrate_box(db, row) for row in rows]


async def add_items(db_path: Path, box_id: int, items: Iterable[str]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        for item in items:
            clean_item = item.strip()
            if clean_item:
                await db.execute(
                    "INSERT INTO items (box_id, name) VALUES (?, ?)",
                    (box_id, clean_item),
                )
        await db.commit()


async def add_photos(db_path: Path, box_id: int, file_ids: Iterable[str]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        for file_id in file_ids:
            await db.execute(
                "INSERT INTO photos (box_id, file_id) VALUES (?, ?)",
                (box_id, file_id),
            )
        await db.commit()


async def update_status(db_path: Path, box_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус: {status}")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE boxes SET status = ? WHERE id = ?", (status, box_id))
        await db.commit()


async def delete_box(db_path: Path, box_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("DELETE FROM boxes WHERE id = ?", (box_id,))
        await db.commit()


async def _hydrate_box(db: aiosqlite.Connection, row: aiosqlite.Row) -> Box:
    item_rows = await db.execute_fetchall(
        "SELECT name FROM items WHERE box_id = ? ORDER BY id",
        (row["id"],),
    )
    photo_rows = await db.execute_fetchall(
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
