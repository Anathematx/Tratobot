"""Анализ расходов и тексты отчётов (HTML-разметка Telegram)."""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from html import escape

from .db import Database, Expense, Recurring
from .parsing import fmt_money

MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def fmt_day(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month]}"


def by_category(expenses: list[Expense]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for e in expenses:
        totals[e.category] += e.amount
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def _bar(value: int, maximum: int, width: int = 10) -> str:
    filled = round(width * value / maximum) if maximum else 0
    return "▓" * filled + "░" * (width - filled)


def _pct_change(now: int, before: int) -> str:
    if before == 0:
        return ""
    diff = (now - before) * 100 / before
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:.0f}%"


# --- регулярные платежи ---

def due_date(rec: Recurring, year: int, month: int) -> date:
    """Дата платежа в данном месяце; 31-е в коротком месяце переносится на последний день."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(rec.day_of_month, last))


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def next_due(rec: Recurring, today: date) -> date:
    """Ближайшая неоплаченная дата платежа (сегодня или позже, либо просроченная в этом месяце)."""
    y, m = today.year, today.month
    while rec.last_paid is not None and f"{y:04d}-{m:02d}" <= rec.last_paid:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return due_date(rec, y, m)


def upcoming_payments(recs: list[Recurring], today: date, until: date) -> list[tuple[Recurring, date]]:
    items = [(r, next_due(r, today)) for r in recs]
    return sorted([(r, d) for r, d in items if d <= until], key=lambda x: x[1])


# --- период между поступлениями ---

def _next_by_days(days: list[int], today: date) -> date:
    for d in sorted(days):
        last = calendar.monthrange(today.year, today.month)[1]
        if min(d, last) > today.day:
            return date(today.year, today.month, min(d, last))
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(y, m, min(min(days), calendar.monthrange(y, m)[1]))


def expected_next_income(db: Database, chat_id: int, today: date, paydays: tuple[int, ...] = ()) -> date | None:
    """
    Дата следующего поступления: по заданным дням выплат, иначе по дням месяца,
    в которые приходили деньги последние 2 месяца. Если поступление было одно — через 15 дней после него.
    """
    if paydays:
        return _next_by_days(list(paydays), today)
    recent = db.incomes(chat_id, today - timedelta(days=62), today + timedelta(days=1))
    days = sorted({i.day.day for i in recent})
    if not days:
        return None
    if len(days) == 1:
        guess = recent[-1].day + timedelta(days=15)
        return guess if guess > today else _next_by_days(days, today)
    return _next_by_days(days, today)


def budget_block(db: Database, chat_id: int, today: date, paydays: tuple[int, ...] = ()) -> str:
    """Сколько осталось с последнего поступления с учётом предстоящих обязательных платежей."""
    last = db.last_income(chat_id)
    if last is None:
        return "Поступлений пока нет. Добавьте зарплату или аванс командой /income."
    start = last.day
    tomorrow = today + timedelta(days=1)
    income = sum(i.amount for i in db.incomes(chat_id, start, tomorrow))
    spent = sum(e.amount for e in db.expenses(chat_id, start, tomorrow))
    left = income - spent

    lines = [
        f"<b>С {fmt_day(start)}</b> (последнее поступление)",
        f"Пришло: {fmt_money(income)}",
        f"Потрачено: {fmt_money(spent)}",
        f"Осталось: <b>{fmt_money(left)}</b>",
    ]
    nxt = expected_next_income(db, chat_id, today, paydays)
    if nxt is not None and nxt > today:
        upcoming = upcoming_payments(db.recurring(chat_id), today, nxt - timedelta(days=1))
        planned = sum(r.amount for r, _ in upcoming)
        free = left - planned
        days_left = (nxt - today).days
        if planned:
            lines.append(f"Плановые платежи до {fmt_day(nxt)}: {fmt_money(planned)}")
        lines.append(f"Свободно до {fmt_day(nxt)}: <b>{fmt_money(free)}</b>")
        if free > 0:
            lines.append(f"Это примерно {fmt_money(free // days_left)} в день ({days_left} дн.)")
        else:
            lines.append("⚠️ Денег до следующего поступления не хватает с учётом плановых платежей.")
    return "\n".join(lines)


# --- отчёты ---

def period_report(db: Database, chat_id: int, start: date, end: date, title: str) -> str:
    """Отчёт за [start, end): итог, категории, крупнейшие траты."""
    expenses = db.expenses(chat_id, start, end)
    incomes = db.incomes(chat_id, start, end)
    lines = [f"<b>{escape(title)}</b>", f"{fmt_day(start)} — {fmt_day(end - timedelta(days=1))}", ""]
    if incomes:
        lines.append(f"Доходы: {fmt_money(sum(i.amount for i in incomes))}")
    total = sum(e.amount for e in expenses)
    lines.append(f"Расходы: <b>{fmt_money(total)}</b>")
    if not expenses:
        return "\n".join(lines)
    lines.append("")
    cats = by_category(expenses)
    top = max(cats.values())
    for cat, amount in cats.items():
        share = amount * 100 / total
        lines.append(f"{_bar(amount, top)} {escape(cat)}: {fmt_money(amount)} ({share:.0f}%)")
    biggest = sorted(expenses, key=lambda e: -e.amount)[:3]
    lines.append("")
    lines.append("Крупнейшие траты:")
    for e in biggest:
        note = f" — {escape(e.note)}" if e.note else ""
        lines.append(f"• {fmt_day(e.day)}, {escape(e.category)}{note}: {fmt_money(e.amount)}")
    return "\n".join(lines)


def weekly_report(db: Database, chat_id: int, today: date, paydays: tuple[int, ...] = ()) -> str:
    """Отчёт за прошлую полную неделю (пн–вс) с анализом относительно предыдущих недель."""
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    end = this_monday
    parts = [period_report(db, chat_id, start, end, "📊 Отчёт за неделю")]

    week = by_category(db.expenses(chat_id, start, end))
    total = sum(week.values())
    prev_total = sum(e.amount for e in db.expenses(chat_id, start - timedelta(days=7), start))
    history = db.expenses(chat_id, start - timedelta(days=28), start)
    avg_total = sum(e.amount for e in history) // 4
    hist_cats = {k: v // 4 for k, v in by_category(history).items()}

    analysis = []
    if prev_total:
        analysis.append(f"К прошлой неделе: {_pct_change(total, prev_total)} ({fmt_money(prev_total)})")
    if history:
        analysis.append(f"Среднее за 4 недели: {fmt_money(avg_total)} ({_pct_change(total, avg_total)})")
        grown = [
            (cat, amount, hist_cats[cat]) for cat, amount in week.items()
            if hist_cats.get(cat) and amount > hist_cats[cat] * 1.3 and amount - hist_cats[cat] >= 50000
        ]
        for cat, amount, avg in grown[:3]:
            analysis.append(f"↑ {escape(cat)}: {fmt_money(amount)} при среднем {fmt_money(avg)}")
        new = [cat for cat in week if cat not in hist_cats]
        if new:
            analysis.append("Новые категории: " + ", ".join(escape(c) for c in new))
    if analysis:
        parts.append("<b>Анализ</b>\n" + "\n".join(analysis))

    parts.append(budget_block(db, chat_id, today, paydays))

    upcoming = upcoming_payments(db.recurring(chat_id), today, today + timedelta(days=7))
    if upcoming:
        parts.append(
            "<b>Плановые платежи на неделе</b>\n"
            + "\n".join(f"• {fmt_day(d)}: {escape(r.name)} — {fmt_money(r.amount)}" for r, d in upcoming)
        )
    return "\n\n".join(parts)
