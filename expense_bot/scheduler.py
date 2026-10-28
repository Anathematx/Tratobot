"""Фоновые задачи: утренний свод, еженедельный отчёт, напоминания о платежах и автоудаление сообщений."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from html import escape

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import Message
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .db import Database, Recurring
from .parsing import fmt_money
from .reports import daily_report, fmt_day, month_key, next_due, weekly_report

log = logging.getLogger(__name__)

# Сводки, отчёты и напоминания остаются в чате: отправленные внутри keep_in_chat() не удаляются
_keep = ContextVar("keep_in_chat", default=False)


@contextmanager
def keep_in_chat():
    token = _keep.set(True)
    try:
        yield
    finally:
        _keep.reset(token)


class TrackSentMessages(BaseRequestMiddleware):
    """Запоминает отправленные ботом сообщения, чтобы потом удалить их из чата."""

    def __init__(self, db: Database):
        self.db = db

    async def __call__(self, make_request, bot, method):
        response = await make_request(bot, method)
        # В зависимости от версии aiogram сессия возвращает объект ответа или сразу результат
        result = response if isinstance(response, Message) else getattr(response, "result", None)
        if isinstance(method, (SendMessage, SendDocument)) and isinstance(result, Message) and not _keep.get():
            self.db.track_message(result.chat.id, result.message_id)
        return response


async def delete_old_messages(bot: Bot, db: Database, config: Config) -> None:
    if config.autodelete_minutes <= 0:
        return
    old = db.messages_older_than(time.time() - config.autodelete_minutes * 60)
    for chat_id, ids in old.items():
        for i in range(0, len(ids), 100):  # Telegram удаляет не больше 100 сообщений за раз
            chunk = ids[i:i + 100]
            try:
                await bot.delete_messages(chat_id, chunk)
            except Exception:
                # Сообщения старше 48 часов или уже удалённые Telegram не даёт удалить — просто забываем их
                log.warning("Не удалось удалить сообщения в чате %s", chat_id, exc_info=True)
            db.forget_messages(chat_id, chunk)


def reminder_due(rec: Recurring, today: date, days_before: int) -> tuple[bool, date]:
    """Напоминаем за days_before дней, в день платежа и каждый день, пока просроченный платёж не отмечен."""
    due = next_due(rec, today)
    if rec.last_reminded == today.isoformat():
        return False, due
    delta = (due - today).days
    return delta in (days_before, 0) or delta < 0, due


def reminder_text(rec: Recurring, due: date, today: date) -> str:
    delta = (due - today).days
    when = (
        "сегодня" if delta == 0
        else f"через {delta} дн., {fmt_day(due)}" if delta > 0
        else f"был {fmt_day(due)} — просрочен"
    )
    return f"🔔 Плановый платёж {when}:\n<b>{escape(rec.name)}</b> — {fmt_money(rec.amount)}"


async def send_reminders(bot: Bot, db: Database, config: Config) -> None:
    today = datetime.now(config.tz).date()
    for rec in db.recurring():
        need, due = reminder_due(rec, today, config.remind_days_before)
        if not need:
            continue
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Оплачено", callback_data=f"paid:{rec.id}:{month_key(due)}"),
        ]])
        try:
            with keep_in_chat():
                await bot.send_message(rec.chat_id, reminder_text(rec, due, today), reply_markup=kb)
            db.mark_recurring_reminded(rec.id, today)
        except Exception:
            log.exception("Не удалось отправить напоминание %s", rec.id)


async def send_weekly_reports(bot: Bot, db: Database, config: Config) -> None:
    today = datetime.now(config.tz).date()
    for chat_id in db.users():
        try:
            with keep_in_chat():
                await bot.send_message(chat_id, weekly_report(db, chat_id, today, config.paydays))
        except Exception:
            log.exception("Не удалось отправить отчёт в чат %s", chat_id)


async def send_daily_reports(bot: Bot, db: Database, config: Config) -> None:
    today = datetime.now(config.tz).date()
    for chat_id in db.users():
        try:
            with keep_in_chat():
                await bot.send_message(chat_id, daily_report(db, chat_id, today, config.paydays))
        except Exception:
            log.exception("Не удалось отправить утренний свод в чат %s", chat_id)


def setup_scheduler(bot: Bot, db: Database, config: Config) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.tz)
    scheduler.add_job(
        send_weekly_reports, CronTrigger(day_of_week=config.report_day, hour=config.report_hour, timezone=config.tz),
        args=(bot, db, config), id="weekly_report", misfire_grace_time=3600,
    )
    scheduler.add_job(
        send_reminders, CronTrigger(hour=config.reminder_hour, timezone=config.tz),
        args=(bot, db, config), id="reminders", misfire_grace_time=3600,
    )
    scheduler.add_job(
        send_daily_reports, CronTrigger(hour=config.daily_hour, timezone=config.tz),
        args=(bot, db, config), id="daily_report", misfire_grace_time=3600,
    )
    scheduler.add_job(
        delete_old_messages, "interval", minutes=1, args=(bot, db, config), id="autodelete",
        max_instances=1, coalesce=True,
    )
    return scheduler
