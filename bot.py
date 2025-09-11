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

# Логирование
logging.basicConfig(level=logging.INFO)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
DAYS_BEFORE = int(os.getenv("DAYS_BEFORE", 3))
TZ = os.getenv("TZ", "Europe/Moscow")

# Основные объекты
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TZ)

DB_FILE = "reminders.db"

# Инициализация базы
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS persons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, date TEXT)"
        )
        await db.commit()

# Добавление напоминания
async def add_person(name: str, date: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO persons (name, date) VALUES (?, ?)", (name, date))
        await db.commit()

# Проверка дат
async def check_dates():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT name, date FROM persons") as cursor:
            rows = await cursor.fetchall()
            for name, date in rows:
                try:
                    event_date = datetime.strptime(date, "%Y-%m-%d")
                except ValueError:
                    continue
                notify_date = event_date - timedelta(days=DAYS_BEFORE)
                if notify_date.date() == datetime.now(pytz.timezone(TZ)).date():
                    await bot.send_message(
                        chat_id=os.getenv("CHAT_ID"),
                        text=f"Напоминание: через {DAYS_BEFORE} дня событие у {name} ({date})!"
                    )

# Команда старт
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я бот-напоминалка. Напиши: имя и дату в формате ГГГГ-ММ-ДД.")

# Добавление события
@dp.message()
async def save_event(message: types.Message):
    try:
        name, date = message.text.split()
        await add_person(name, date)
        await message.answer(f"Событие сохранено: {name} — {date}")
    except Exception:
        await message.answer("Ошибка! Используй формат: Имя ГГГГ-ММ-ДД")

# Запуск
async def main():
    await init_db()
    scheduler.add_job(check_dates, trigger="interval", days=1)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

