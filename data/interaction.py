import aiosqlite
import time

class Database():
    def __init__(self):
        self.path = "data/.db"

    async def init_db(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    answers_limit INTEGER DEFAULT 10,
                    spent_today INTEGER DEFAULT 0,
                    limits_reset_time INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            await db.commit()

    async def add_user(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
            await db.commit()

    async def get_user(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user = await cursor.fetchone()
            if not user:
                await self.add_user(user_id)
                return await self.get_user(user_id)
            return user

    async def update_user(self, user_id: int, spent_today: int = None, limits_reset_time: str = None):
        async with aiosqlite.connect(self.path) as db:
            if spent_today is not None:
                await db.execute("UPDATE users SET spent_today = ? WHERE id = ?", (spent_today, user_id))
            if limits_reset_time is not None:
                await db.execute("UPDATE users SET limits_reset_time = ? WHERE id = ?", (limits_reset_time, user_id))
            await db.commit()

    async def reset_limit(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT spent_today, limits_reset_time FROM users WHERE id = ?",
                (user_id,)
            )
            user = await cursor.fetchone()

            if not user:
                return

            spent_today, reset_time = user
            now = int(time.time())

            # если время сброса прошло
            if reset_time is None or now >= reset_time:
                new_reset_time = now + 86400  # +24 часа

                await db.execute("""
                    UPDATE users 
                    SET spent_today = 0,
                        limits_reset_time = ?
                    WHERE id = ?
                """, (new_reset_time, user_id))

                await db.commit()