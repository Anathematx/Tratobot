from datetime import date

import pytest

from expense_bot.db import Database
from expense_bot.parsing import ParseError, fmt_money, parse_amount, parse_day, parse_entry, parse_recurring
from expense_bot.reports import budget_block, expected_next_income, next_due, upcoming_payments, weekly_report
from expense_bot.scheduler import reminder_due

TODAY = date(2026, 10, 28)  # среда
CHAT = 1


@pytest.fixture
def db():
    return Database(":memory:")


@pytest.mark.parametrize("token,expected", [
    ("500", 50000), ("99,90", 9990), ("12.5", 1250), ("2к", 200000), ("1.5k", 150000),
    ("100р", 10000), ("продукты", None), ("0", None), ("-5", None),
])
def test_parse_amount(token, expected):
    assert parse_amount(token) == expected


def test_parse_day():
    assert parse_day("вчера", TODAY) == date(2026, 10, 27)
    assert parse_day("25.10", TODAY) == date(2026, 10, 25)
    assert parse_day("31.12", TODAY) == date(2025, 12, 31)  # будущая дата без года — прошлый год
    assert parse_day("1.1.27", TODAY) == date(2027, 1, 1)
    assert parse_day("32.10", TODAY) is None


def test_parse_entry_variants():
    e = parse_entry("500 продукты молоко и хлеб", TODAY)
    assert (e.amount, e.label, e.note, e.day) == (50000, "продукты", "молоко и хлеб", TODAY)
    e = parse_entry("Такси 1 200 вчера", TODAY)
    assert (e.amount, e.label, e.day) == (120000, "такси", date(2026, 10, 27))
    e = parse_entry("300 кафе 25.10", TODAY)
    assert (e.amount, e.day) == (30000, date(2026, 10, 25))
    assert parse_entry("60000", TODAY, default_label="доход").label == "доход"
    with pytest.raises(ParseError):
        parse_entry("продукты", TODAY)
    with pytest.raises(ParseError):
        parse_entry("500", TODAY)


def test_parse_recurring():
    r = parse_recurring("15 1 200 связь Мобильный телефон")
    assert (r.day_of_month, r.amount, r.category, r.name) == (15, 120000, "связь", "Мобильный телефон")
    assert parse_recurring("1 30000 жильё").name == "Жильё"
    with pytest.raises(ParseError):
        parse_recurring("40 100 x")


def test_fmt_money():
    assert fmt_money(123456789) == "1 234 567,89 ₽"
    assert fmt_money(-50000) == "−500 ₽"


def test_next_due_handles_paid_and_short_months(db):
    rid = db.add_recurring(CHAT, "Аренда", 3000000, "жильё", 31)
    rec = db.get_recurring(CHAT, rid)
    assert next_due(rec, date(2026, 11, 5)) == date(2026, 11, 30)
    db.mark_recurring_paid(rid, "2026-11")
    rec = db.get_recurring(CHAT, rid)
    assert next_due(rec, date(2026, 11, 5)) == date(2026, 12, 31)
    db.mark_recurring_paid(rid, "2026-12")
    rec = db.get_recurring(CHAT, rid)
    assert next_due(rec, date(2026, 12, 31)) == date(2027, 1, 31)


def test_reminders(db):
    rid = db.add_recurring(CHAT, "Интернет", 60000, "связь", 30)
    rec = db.get_recurring(CHAT, rid)
    assert reminder_due(rec, date(2026, 10, 28), 2)[0] is True   # за 2 дня
    assert reminder_due(rec, date(2026, 10, 29), 2)[0] is False  # за 1 день молчим
    assert reminder_due(rec, date(2026, 10, 30), 2)[0] is True   # в день платежа
    assert reminder_due(rec, date(2026, 10, 31), 2)[0] is True   # просрочен
    db.mark_recurring_reminded(rid, date(2026, 10, 31))
    assert reminder_due(db.get_recurring(CHAT, rid), date(2026, 10, 31), 2)[0] is False
    db.mark_recurring_paid(rid, "2026-10")
    assert reminder_due(db.get_recurring(CHAT, rid), date(2026, 11, 1), 2)[0] is False


def test_budget_and_expected_income(db):
    db.add_income(CHAT, 3000000, "аванс", date(2026, 9, 20))
    db.add_income(CHAT, 6000000, "зарплата", date(2026, 10, 5))
    db.add_income(CHAT, 3000000, "аванс", date(2026, 10, 20))
    db.add_expense(CHAT, 500000, "продукты", date(2026, 10, 22))
    db.add_expense(CHAT, 100000, "кафе", date(2026, 10, 19))  # до последнего поступления — не учитывается
    db.add_recurring(CHAT, "Интернет", 60000, "связь", 30)
    assert expected_next_income(db, CHAT, TODAY) == date(2026, 11, 5)
    text = budget_block(db, CHAT, TODAY)
    assert "Осталось: <b>25 000 ₽</b>" in text
    assert "Плановые платежи до 5 ноября: 600 ₽" in text
    assert "Свободно до 5 ноября: <b>24 400 ₽</b>" in text
    assert len(upcoming_payments(db.recurring(CHAT), TODAY, date(2026, 11, 4))) == 1


def test_weekly_report_analysis(db):
    # 4 недели истории по 1 000 ₽ на продукты, на прошлой неделе — 3 000 ₽ и новая категория
    for week_start in (date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)):
        db.add_expense(CHAT, 100000, "продукты", week_start)
    db.add_expense(CHAT, 300000, "продукты", date(2026, 10, 20))
    db.add_expense(CHAT, 50000, "кино", date(2026, 10, 21))
    db.add_income(CHAT, 3000000, "аванс", date(2026, 10, 20))
    text = weekly_report(db, CHAT, TODAY)
    assert "19 октября — 25 октября" in text
    assert "Расходы: <b>3 500 ₽</b>" in text
    assert "↑ продукты" in text
    assert "Новые категории: кино" in text


def test_undo_removes_latest_entry(db):
    db.add_expense(CHAT, 100, "a", TODAY)
    db.add_income(CHAT, 200, "зарплата", TODAY)
    assert db.delete_last_entry(CHAT) == ("income", 200, "зарплата")
    assert db.delete_last_entry(CHAT) == ("expense", 100, "a")
    assert db.delete_last_entry(CHAT) is None


def test_expected_income_with_paydays_and_single_income(db):
    assert expected_next_income(db, CHAT, TODAY, (5, 20)) == date(2026, 11, 5)
    assert expected_next_income(db, CHAT, date(2026, 11, 10), (5, 20)) == date(2026, 11, 20)
    assert expected_next_income(db, CHAT, TODAY) is None
    db.add_income(CHAT, 100, "зарплата", date(2026, 10, 20))
    assert expected_next_income(db, CHAT, TODAY) == date(2026, 11, 4)
