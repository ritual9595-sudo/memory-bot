import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import aiosqlite
import pytz
from datetime import datetime, timedelta
import os

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DAYS_BEFORE = int(os.getenv("DAYS_BEFORE", 3))
TZ = os.getenv("TZ", "Europe/Moscow")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TZ)

DB_FILE = "reminders.db"


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS persons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, birth DATE, death DATE)"
        )
        await db.commit()


async def send_reminder(chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logging.error(f"Ошибка отправки напоминания: {e}")


async def schedule_reminders():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT id, name, birth, death FROM persons") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                pid, name, birth, death = row
                now = datetime.now(pytz.timezone(TZ)).date()

                for date_str, label in [(birth, "день рождения"), (death, "день памяти")]:
                    if date_str:
                        date = datetime.strptime(date_str, "%Y-%m-%d").date().replace(year=now.year)
                        reminder_date = date - timedelta(days=DAYS_BEFORE)
                        if reminder_date >= now:
                            scheduler.add_job(
                                send_reminder,
                                trigger=DateTrigger(run_date=datetime.combine(reminder_date, datetime.min.time(), tzinfo=pytz.timezone(TZ))),
                                args=[chat_id_global, f"Через {DAYS_BEFORE} дня: {label} {name} ({date_str})"]
                            )


@dp.message(Command("start"))
async def start(message: types.Message):
    global chat_id_global
    chat_id_global = message.chat.id
    await message.answer("Привет! Я буду напоминать о днях рождения и памяти усопших за несколько дней.")


@dp.message(Command("add"))
async def add_person(message: types.Message):
    try:
        data = message.text.replace("/add", "").strip().split(";")
        name, birth, death = [x.strip() for x in data]
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO persons (name, birth, death) VALUES (?, ?, ?)", (name, birth, death))
            await db.commit()
        await message.answer(f"{name} добавлен.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.message(Command("list"))
async def list_persons(message: types.Message):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT id, name, birth, death FROM persons") as cursor:
            rows = await cursor.fetchall()
            if not rows:
                await message.answer("Список пуст.")
            else:
                text = "\n".join([f"{pid}. {name} (рождение: {birth}, смерть: {death})" for pid, name, birth, death in rows])
                await message.answer(text)


@dp.message(Command("remove"))
async def remove_person(message: types.Message):
    try:
        pid = int(message.text.replace("/remove", "").strip())
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("DELETE FROM persons WHERE id = ?", (pid,))
            await db.commit()
        await message.answer("Удалено.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


async def main():
    await init_db()
    scheduler.start()
    await schedule_reminders()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
