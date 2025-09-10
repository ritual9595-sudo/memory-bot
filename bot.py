import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple

import aiosqlite
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dateutil.relativedelta import relativedelta
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

TIMEZONE = os.getenv("TZ", "Europe/Moscow")
TZ = pytz.timezone(TIMEZONE)

REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "9"))
REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))
DAYS_BEFORE = int(os.getenv("DAYS_BEFORE", "3"))

DB_PATH = os.getenv("DB_PATH", "reminders.db")

logging.basicConfig(level=logging.INFO)

@dataclass
class Person:
    id: int
    chat_id: int
    full_name: str
    birth: Optional[date]
    death: Optional[date]
    created_at: datetime

def parse_date(s: str) -> Optional[date]:
    s = s.strip()
    if not s:
        return None
    try:
        if "-" in s:
            return datetime.strptime(s, "%Y-%m-%d").date()
        if "." in s:
            return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        pass
    return None

def normalize_anniversary(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:
        if d.month == 2 and d.day == 29:
            return date(year, 2, 28)
        raise

def is_anniversary_in_days(d: Optional[date], today: date, days_ahead: int) -> bool:
    if d is None:
        return False
    target = today + timedelta(days=days_ahead)
    anniv = normalize_anniversary(d, target.year)
    return anniv.month == target.month and anniv.day == target.day

def fmt_date(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    birth TEXT,
    death TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_id ON persons(chat_id);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLE_SQL)
        await db.commit()

async def add_person(chat_id: int, full_name: str, birth: Optional[date], death: Optional[date]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO persons(chat_id, full_name, birth, death, created_at) VALUES (?,?,?,?,?)",
            (
                chat_id,
                full_name.strip(),
                birth.isoformat() if birth else None,
                death.isoformat() if death else None,
                datetime.now(TZ).isoformat(),
            ),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        row = await cur.fetchone()
        return int(row[0])

async def list_persons(chat_id: int) -> List[Person]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, chat_id, full_name, birth, death, created_at FROM persons WHERE chat_id=? ORDER BY id",
            (chat_id,),
        )
        rows = await cur.fetchall()
    res: List[Person] = []
    for r in rows:
        b = datetime.strptime(r[3], "%Y-%m-%d").date() if r[3] else None
        d = datetime.strptime(r[4], "%Y-%m-%d").date() if r[4] else None
        res.append(Person(id=r[0], chat_id=r[1], full_name=r[2], birth=b, death=d, created_at=datetime.fromisoformat(r[5])))
    return res

async def remove_person(chat_id: int, pid: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM persons WHERE chat_id=? AND id=?", (chat_id, pid))
        await db.commit()
        return cur.rowcount > 0

async def all_chat_ids() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT DISTINCT chat_id FROM persons")
        rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

HELP_TEXT = (
    f"Я буду напоминать за <b>{DAYS_BEFORE}</b> дня(дней) до дат рождения и смерти.\n\n"
    "Команды:\n"
    "<b>/add</b> ФИО; YYYY-MM-DD; YYYY-MM-DD — добавить запись\n"
    "Пример: <code>/add Иванова Мария Петровна; 1948-11-02; 2001-04-10</code>\n"
    "Допустим и такой ввод: <code>/add Иванов И.И.; 15.09.1950; 02.06.2010</code>\n\n"
    "<b>/list</b> — показать список с ID\n"
    "<b>/remove</b> ID — удалить запись\n"
    "<b>/help</b> — эта подсказка"
)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Здравствуйте!\n" + HELP_TEXT)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)

def parse_add_args(text: str) -> Tuple[str, Optional[date], Optional[date]]:
    payload = text.split(" ", 1)[1] if " " in text else ""
    if not payload:
        raise ValueError("Укажите: ФИО; дата рождения; дата смерти")
    parts = [p.strip() for p in payload.replace("\n", ";").split(";")]
    if len(parts) < 3:
        raise ValueError("Нужно три поля: ФИО; дата рождения; дата смерти")
    full_name = parts[0]
    b = parse_date(parts[1])
    d = parse_date(parts[2])
    if not full_name:
        raise ValueError("ФИО не должно быть пустым")
    if b is None or d is None:
        raise ValueError("Даты должны быть в формате YYYY-MM-DD или DD.MM.YYYY")
    return full_name, b, d

@dp.message(Command("add"))
async def cmd_add(message: Message):
    try:
        full_name, b, d = parse_add_args(message.text or "")
    except Exception as e:
        await message.answer(f"Ошибка: {e}\n\nПример:\n<code>/add Иванов Иван Иванович; 1950-09-15; 2010-06-02</code>")
        return
    pid = await add_person(message.chat.id, full_name, b, d)
    await message.answer((
        f"Добавлено (ID: <b>{pid}</b>): <b>{full_name}</b>\n"
        f"Рождение: {fmt_date(b)}\nСмерть: {fmt_date(d)}"
    ))

@dp.message(Command("list"))
async def cmd_list(message: Message):
    people = await list_persons(message.chat.id)
    if not people:
        await message.answer("Список пуст. Добавьте с помощью /add")
        return
    lines = ["<b>ID</b> | <b>ФИО</b> | <b>Рождение</b> | <b>Смерть</b>"]
    for p in people:
        lines.append(f"{p.id} | {p.full_name} | {fmt_date(p.birth)} | {fmt_date(p.death)}")
    await message.answer("\n".join(lines))

@dp.message(Command("remove"))
async def cmd_remove(message: Message):
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Укажите ID: /remove 12")
        return
    pid = int(parts[1])
    ok = await remove_person(message.chat.id, pid)
    await message.answer("Удалено" if ok else "Запись не найдена")

async def send_reminders():
    today_local = datetime.now(TZ).date()
    target = today_local + timedelta(days=DAYS_BEFORE)
    logging.info("Проверка напоминаний на %s (смотрим даты %s)", today_local.isoformat(), target.strftime("%d.%m"))
    chats = await all_chat_ids()
    for chat_id in chats:
        people = await list_persons(chat_id)
        messages = []
        for p in people:
            events = []
            if is_anniversary_in_days(p.birth, today_local, DAYS_BEFORE):
                anniv = normalize_anniversary(p.birth, target.year)
                age = (anniv.year - p.birth.year) if p.birth else None
                events.append(("дата рождения", anniv, age))
            if is_anniversary_in_days(p.death, today_local, DAYS_BEFORE):
                anniv = normalize_anniversary(p.death, target.year)
                years = (anniv.year - p.death.year) if p.death else None
                events.append(("дата смерти", anniv, years))
            for kind, anniv, years in events:
                years_str = f" — {years}-я годовщина" if years is not None else ""
                messages.append(
                    f"Напоминание: через {DAYS_BEFORE} дня — {kind} <b>{p.full_name}</b> (дата: {anniv.strftime('%d.%m')}){years_str}."
                )
        if messages:
            await bot.send_message(chat_id, "\n".join(messages))

async def scheduler_start():
    scheduler = AsyncIOScheduler(timezone=TZ)
    trigger = CronTrigger(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, timezone=TZ)
    scheduler.add_job(send_reminders, trigger, name="daily_reminders")
    scheduler.start()

async def main():
    await init_db()
    await scheduler_start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")
