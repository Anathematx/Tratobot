"""Фоновые задачи: еженедельный отчёт и напоминания о плановых платежах."""
import logging
from datetime import date, datetime
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .db import Database, Recurring
from .parsing import fmt_money
from .reports import fmt_day, month_key, next_due, weekly_report

log = logging.getLogger(__name__)


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
            await bot.send_message(rec.chat_id, reminder_text(rec, due, today), reply_markup=kb)
            db.mark_recurring_reminded(rec.id, today)
        except Exception:
            log.exception("Не удалось отправить напоминание %s", rec.id)


async def send_weekly_reports(bot: Bot, db: Database, config: Config) -> None:
    today = datetime.now(config.tz).date()
    for chat_id in db.users():
        try:
            await bot.send_message(chat_id, weekly_report(db, chat_id, today, config.paydays))
        except Exception:
            log.exception("Не удалось отправить отчёт в чат %s", chat_id)


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
    return scheduler
