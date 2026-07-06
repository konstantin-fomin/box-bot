from __future__ import annotations

import sqlite3

if __package__ in {None, ""}:
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.database import LEGACY_STATUS_MAP, generate_invite_code, utc_now


FIRST_HOUSEHOLD_NAME = "Семья Фоминых"


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def create_base_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT
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
        """
    )


def ensure_first_household(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, invite_code FROM households WHERE name = ? ORDER BY id LIMIT 1",
        (FIRST_HOUSEHOLD_NAME,),
    ).fetchone()
    if row:
        return row

    invite_code = generate_invite_code()
    while conn.execute("SELECT id FROM households WHERE invite_code = ?", (invite_code,)).fetchone():
        invite_code = generate_invite_code()

    cursor = conn.execute(
        "INSERT INTO households (name, invite_code, created_at) VALUES (?, ?, ?)",
        (FIRST_HOUSEHOLD_NAME, invite_code, utc_now()),
    )
    return conn.execute(
        "SELECT id, name, invite_code FROM households WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()


def rebuild_boxes(conn: sqlite3.Connection, default_household_id: int) -> None:
    columns = table_columns(conn, "boxes") if table_exists(conn, "boxes") else set()
    conn.execute("DROP TABLE IF EXISTS boxes_new")
    conn.execute(
        """
        CREATE TABLE boxes_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            household_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            room TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'with_me',
            created_at TEXT NOT NULL,
            FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE,
            UNIQUE (household_id, code)
        )
        """
    )
    if columns:
        household_expr = "COALESCE(household_id, ?)" if "household_id" in columns else "?"
        conn.execute(
            f"""
            INSERT INTO boxes_new (id, household_id, code, room, status, created_at)
            SELECT id, {household_expr}, code, room, status, created_at
            FROM boxes
            """,
            (default_household_id,),
        )
        for old_status, new_status in LEGACY_STATUS_MAP.items():
            conn.execute("UPDATE boxes_new SET status = ? WHERE status = ?", (new_status, old_status))
        conn.execute("DROP TABLE boxes")
    conn.execute("ALTER TABLE boxes_new RENAME TO boxes")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_boxes_household_id ON boxes(household_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_boxes_room ON boxes(room)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_boxes_status ON boxes(status)")


def rebuild_room_counters(conn: sqlite3.Connection, default_household_id: int) -> None:
    columns = table_columns(conn, "room_counters") if table_exists(conn, "room_counters") else set()
    conn.execute("DROP TABLE IF EXISTS room_counters_new")
    conn.execute(
        """
        CREATE TABLE room_counters_new (
            household_id INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            last_number INTEGER NOT NULL,
            PRIMARY KEY (household_id, prefix),
            FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
        )
        """
    )
    if columns:
        household_expr = "COALESCE(household_id, ?)" if "household_id" in columns else "?"
        conn.execute(
            f"""
            INSERT INTO room_counters_new (household_id, prefix, last_number)
            SELECT {household_expr}, prefix, last_number
            FROM room_counters
            """,
            (default_household_id,),
        )
        conn.execute("DROP TABLE room_counters")
    conn.execute("ALTER TABLE room_counters_new RENAME TO room_counters")


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def migrate() -> None:
    config = load_config()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(config.database_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        create_base_tables(conn)
        household = ensure_first_household(conn)
        household_id = int(household["id"])

        for user_id in config.whitelist_user_ids:
            conn.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, NULL)", (user_id,))
            conn.execute(
                """
                INSERT OR IGNORE INTO household_members (household_id, user_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (household_id, user_id, utc_now()),
            )

        rebuild_boxes(conn, household_id)
        rebuild_room_counters(conn, household_id)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_box_id ON photos(box_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_household_members_user_id ON household_members(user_id)")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        print(f"household_id={household_id}")
        print(f"invite_code={household['invite_code']}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
