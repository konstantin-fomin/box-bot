import asyncio

from src.database import Database


async def main():
    print("Before connect")
    db = Database("test.db")
    await db.connect()
    print("Connected")
    await db.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    print("Table created")
    await db.execute("DELETE FROM test")
    print("Table cleared")
    await db.execute("INSERT INTO test (value) VALUES (?)", ("ok",))
    print("Row inserted")
    await db.commit()
    print("Committed")
    row = await db.fetchone("SELECT value FROM test ORDER BY id DESC LIMIT 1")
    print(f"Read row: {row['value'] if row else None}")
    await db.close()
    print("Closed")


asyncio.run(main())
