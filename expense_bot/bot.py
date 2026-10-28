"""Обработчики команд и сообщений Telegram."""
import csv
import io
from datetime import date, datetime, timedelta
from html import escape

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from .config import Config
from .db import Database
from .parsing import ParseError, fmt_money, parse_entry, parse_recurring
from .reports import budget_block, fmt_day, month_key, next_due, period_report, upcoming_payments, weekly_report

DEFAULT_CATEGORIES = ["продукты", "кафе", "транспорт", "дом", "здоровье", "развлечения", "одежда", "связь"]

HELP = """<b>Как пользоваться</b>

<b>Расход</b> — просто напишите сумму и категорию:
<code>500 продукты</code>
<code>1 200 такси до работы</code>
<code>350 кафе вчера</code> или <code>350 кафе 25.10</code>
Если написать только сумму, бот предложит выбрать категорию.

<b>Доход</b> — со знаком плюс или командой:
<code>+60000 зарплата</code>
<code>/income 30000 аванс</code>

<b>Регулярные платежи</b>
<code>/regular 15 1200 связь Мобильный</code> — каждое 15-е число, 1 200 ₽
/regulars — список и удаление
За пару дней до платежа бот напомнит, кнопкой «Оплачено» платёж запишется в расходы.

<b>Отчёты</b>
/balance — сколько осталось до следующей зарплаты
/week — анализ за прошлую неделю (приходит сам по понедельникам)
/month — расходы за текущий месяц
/lastmonth — за прошлый месяц
/categories — ваши категории
/export — выгрузка в CSV
/undo — удалить последнюю запись"""


class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed: frozenset[int]):
        self.allowed = allowed

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if self.allowed and (user is None or user.id not in self.allowed):
            return None
        return await handler(event, data)


def build_router(db: Database, config: Config) -> Router:
    router = Router()
    router.message.middleware(AccessMiddleware(config.allowed_users))
    router.callback_query.middleware(AccessMiddleware(config.allowed_users))

    # Суммы, для которых ждём выбор категории: chat_id -> (сумма, дата, список категорий на кнопках)
    pending: dict[int, tuple[int, date, list[str]]] = {}

    def today() -> date:
        return datetime.now(config.tz).date()

    def undo_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Отменить", callback_data="undo")]])

    def categories_for(chat_id: int) -> list[str]:
        cats = db.categories(chat_id)
        return (cats + [c for c in DEFAULT_CATEGORIES if c not in cats])[:12]

    async def save_income(message: Message, text: str) -> None:
        try:
            entry = parse_entry(text, today(), default_label="доход")
        except ParseError as e:
            await message.answer(f"{e}\nПример: <code>+60000 зарплата</code>")
            return
        db.add_income(message.chat.id, entry.amount, entry.label, entry.day, entry.note)
        await message.answer(
            f"💰 Доход {fmt_money(entry.amount)} — {escape(entry.label)}, {fmt_day(entry.day)}\n\n"
            + budget_block(db, message.chat.id, today(), config.paydays),
            reply_markup=undo_kb(),
        )

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        db.add_user(message.chat.id)
        await message.answer("Привет! Я помогу вести учёт доходов и расходов.\n\n" + HELP)

    @router.message(Command("help"))
    async def help_(message: Message) -> None:
        await message.answer(HELP)

    @router.message(Command("income"))
    async def income(message: Message, command: CommandObject) -> None:
        db.add_user(message.chat.id)
        await save_income(message, command.args or "")

    @router.message(Command("regular"))
    async def regular_add(message: Message, command: CommandObject) -> None:
        db.add_user(message.chat.id)
        try:
            p = parse_recurring(command.args or "")
        except ParseError as e:
            await message.answer(f"{e}\nПример: <code>/regular 15 1200 связь Мобильный</code>")
            return
        rec_id = db.add_recurring(message.chat.id, p.name, p.amount, p.category, p.day_of_month)
        t = today()
        # Если дата платежа в этом месяце уже прошла, считаем, что за этот месяц он уже оплачен
        if p.day_of_month < t.day:
            db.mark_recurring_paid(rec_id, month_key(t))
        rec = db.get_recurring(message.chat.id, rec_id)
        await message.answer(
            f"🔁 Регулярный платёж добавлен: {escape(p.name)} — {fmt_money(p.amount)} "
            f"каждое {p.day_of_month}-е число (категория «{escape(p.category)}»).\n"
            f"Ближайший: {fmt_day(next_due(rec, t))}."
        )

    @router.message(Command("regulars"))
    async def regular_list(message: Message) -> None:
        recs = db.recurring(message.chat.id)
        if not recs:
            await message.answer("Регулярных платежей нет. Добавьте: <code>/regular 15 1200 связь Мобильный</code>")
            return
        t = today()
        lines = ["<b>Регулярные платежи</b>"]
        buttons = []
        for r in recs:
            lines.append(
                f"• {r.day_of_month}-го: {escape(r.name)} — {fmt_money(r.amount)} "
                f"(следующий {fmt_day(next_due(r, t))})"
            )
            buttons.append([InlineKeyboardButton(text=f"🗑 {r.name}", callback_data=f"rdel:{r.id}")])
        lines.append(f"\nВсего в месяц: <b>{fmt_money(sum(r.amount for r in recs))}</b>")
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("rdel:"))
    async def regular_delete(call: CallbackQuery) -> None:
        rec_id = int(call.data.split(":")[1])
        ok = db.delete_recurring(call.message.chat.id, rec_id)
        await call.answer("Удалено" if ok else "Уже удалён")
        if ok:
            await call.message.answer("Регулярный платёж удалён. Актуальный список: /regulars")

    @router.callback_query(F.data.startswith("paid:"))
    async def regular_paid(call: CallbackQuery) -> None:
        _, rec_id, month = call.data.split(":")
        rec = db.get_recurring(call.message.chat.id, int(rec_id))
        if rec is None:
            await call.answer("Платёж не найден")
            return
        if rec.last_paid is not None and rec.last_paid >= month:
            await call.answer("Уже отмечен как оплаченный")
            return
        db.add_expense(call.message.chat.id, rec.amount, rec.category, today(), rec.name, recurring_id=rec.id)
        db.mark_recurring_paid(rec.id, month)
        await call.answer("Записано")
        await call.message.edit_text(
            call.message.html_text + f"\n\n✅ Оплачено, {fmt_money(rec.amount)} записано в «{escape(rec.category)}».",
        )

    @router.message(Command("balance"))
    async def balance(message: Message) -> None:
        t = today()
        text = budget_block(db, message.chat.id, t, config.paydays)
        upcoming = upcoming_payments(db.recurring(message.chat.id), t, t + timedelta(days=14))
        if upcoming:
            text += "\n\n<b>Ближайшие платежи</b>\n" + "\n".join(
                f"• {fmt_day(d)}: {escape(r.name)} — {fmt_money(r.amount)}" for r, d in upcoming
            )
        await message.answer(text)

    @router.message(Command("week"))
    async def week(message: Message) -> None:
        await message.answer(weekly_report(db, message.chat.id, today(), config.paydays))

    @router.message(Command("month"))
    async def month(message: Message) -> None:
        t = today()
        start = t.replace(day=1)
        await message.answer(period_report(db, message.chat.id, start, t + timedelta(days=1), "Расходы за месяц"))

    @router.message(Command("lastmonth"))
    async def last_month(message: Message) -> None:
        end = today().replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        await message.answer(period_report(db, message.chat.id, start, end, "Расходы за прошлый месяц"))

    @router.message(Command("categories"))
    async def categories(message: Message) -> None:
        cats = db.categories(message.chat.id)
        if not cats:
            await message.answer("Категорий пока нет. Они появляются сами, когда вы записываете расходы.")
            return
        await message.answer("Ваши категории: " + ", ".join(escape(c) for c in cats))

    @router.message(Command("undo"))
    async def undo(message: Message) -> None:
        await message.answer(_undo_text(db.delete_last_entry(message.chat.id)))

    @router.callback_query(F.data == "undo")
    async def undo_button(call: CallbackQuery) -> None:
        await call.answer()
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(_undo_text(db.delete_last_entry(call.message.chat.id)))

    @router.message(Command("export"))
    async def export(message: Message) -> None:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["дата", "тип", "сумма", "категория", "комментарий"])
        rows = [
            (e.day, "расход", e.amount, e.category, e.note)
            for e in db.expenses(message.chat.id, date.min, date.max)
        ] + [(i.day, "доход", i.amount, i.kind, i.note) for i in db.incomes(message.chat.id, date.min, date.max)]
        for day, kind, amount, label, note in sorted(rows):
            w.writerow([day.isoformat(), kind, f"{amount / 100:.2f}".replace(".", ","), label, note])
        data = buf.getvalue().encode("utf-8-sig")  # BOM, чтобы Excel открыл кириллицу
        await message.answer_document(BufferedInputFile(data, filename=f"expenses_{today().isoformat()}.csv"))

    @router.callback_query(F.data.startswith("cat:"))
    async def choose_category(call: CallbackQuery) -> None:
        item = pending.pop(call.message.chat.id, None)
        if item is None:
            await call.answer("Сумма устарела, введите заново")
            return
        amount, day, cats = item
        category = cats[int(call.data.split(":")[1])]
        db.add_expense(call.message.chat.id, amount, category, day)
        await call.answer()
        await call.message.edit_text(
            f"✍️ {fmt_money(amount)} — {escape(category)}, {fmt_day(day)}", reply_markup=undo_kb()
        )

    @router.message(F.text & ~F.text.startswith("/"))
    async def free_text(message: Message) -> None:
        db.add_user(message.chat.id)
        text = message.text.strip()
        if text.startswith("+"):
            await save_income(message, text[1:])
            return
        t = today()
        words = text.split()
        # Только сумма (и, возможно, дата) — предлагаем выбрать категорию кнопками
        try:
            entry = parse_entry(text, t, default_label="")
        except ParseError as e:
            await message.answer(f"{e}\nСправка: /help")
            return
        if not entry.label:
            cats = categories_for(message.chat.id)
            pending[message.chat.id] = (entry.amount, entry.day, cats)
            rows = [
                [InlineKeyboardButton(text=c, callback_data=f"cat:{i}") for i, c in enumerate(cats)][j:j + 3]
                for j in range(0, len(cats), 3)
            ]
            await message.answer(
                f"{fmt_money(entry.amount)} — выберите категорию или напишите, например, "
                f"<code>{words[0]} продукты</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            return
        db.add_expense(message.chat.id, entry.amount, entry.label, entry.day, entry.note)
        note = f" ({escape(entry.note)})" if entry.note else ""
        day = "" if entry.day == t else f", {fmt_day(entry.day)}"
        spent_month = sum(e.amount for e in db.expenses(message.chat.id, t.replace(day=1), t + timedelta(days=1)))
        await message.answer(
            f"✍️ {fmt_money(entry.amount)} — {escape(entry.label)}{note}{day}\n"
            f"За месяц потрачено: {fmt_money(spent_month)}",
            reply_markup=undo_kb(),
        )

    return router


def _undo_text(result: tuple[str, int, str] | None) -> str:
    if result is None:
        return "Удалять нечего."
    kind, amount, label = result
    what = "расход" if kind == "expense" else "доход"
    return f"🗑 Удалён {what}: {fmt_money(amount)} — {escape(label)}"

