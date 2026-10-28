"""Хранилище на SQLite. Суммы хранятся в копейках (int), даты — ISO-строками."""
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS incomes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    amount   INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    day      TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    amount        INTEGER NOT NULL,
    category      TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    day           TEXT NOT NULL,
    recurring_id  INTEGER,
    created_ns    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS recurring (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        INTEGER NOT NULL,
    name           TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    category       TEXT NOT NULL,
    day_of_month   INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    active         INTEGER NOT NULL DEFAULT 1,
    last_paid      TEXT,      -- 'YYYY-MM' месяца, за который платёж отмечен оплаченным
    last_reminded  TEXT       -- дата последнего напоминания
);
CREATE INDEX IF NOT EXISTS idx_expenses_chat_day ON expenses(chat_id, day);
CREATE INDEX IF NOT EXISTS idx_incomes_chat_day ON incomes(chat_id, day);
"""


@dataclass
class Expense:
    id: int
    amount: int
    category: str
    note: str
    day: date


@dataclass
class Income:
    id: int
    amount: int
    kind: str
    note: str
    day: date


@dataclass
class Recurring:
    id: int
    chat_id: int
    name: str
    amount: int
    category: str
    day_of_month: int
    last_paid: str | None
    last_reminded: str | None


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- пользователи ---
    def add_user(self, chat_id: int) -> None:
        self.conn.execute("INSERT OR IGNORE INTO users(chat_id) VALUES (?)", (chat_id,))
        self.conn.commit()

    def users(self) -> list[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM users")]

    # --- доходы ---
    def add_income(self, chat_id: int, amount: int, kind: str, day: date, note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO incomes(chat_id, amount, kind, note, day, created_ns) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, amount, kind, note, day.isoformat(), time.time_ns()),
        )
        self.conn.commit()
        return cur.lastrowid

    def incomes(self, chat_id: int, start: date, end: date) -> list[Income]:
        rows = self.conn.execute(
            "SELECT * FROM incomes WHERE chat_id=? AND day>=? AND day<? ORDER BY day",
            (chat_id, start.isoformat(), end.isoformat()),
        )
        return [Income(r["id"], r["amount"], r["kind"], r["note"], date.fromisoformat(r["day"])) for r in rows]

    def last_income(self, chat_id: int) -> Income | None:
        r = self.conn.execute(
            "SELECT * FROM incomes WHERE chat_id=? ORDER BY day DESC, id DESC LIMIT 1", (chat_id,)
        ).fetchone()
        return Income(r["id"], r["amount"], r["kind"], r["note"], date.fromisoformat(r["day"])) if r else None

    # --- расходы ---
    def add_expense(
        self, chat_id: int, amount: int, category: str, day: date, note: str = "", recurring_id: int | None = None
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO expenses(chat_id, amount, category, note, day, recurring_id, created_ns)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, amount, category, note, day.isoformat(), recurring_id, time.time_ns()),
        )
        self.conn.commit()
        return cur.lastrowid

    def expenses(self, chat_id: int, start: date, end: date) -> list[Expense]:
        rows = self.conn.execute(
            "SELECT * FROM expenses WHERE chat_id=? AND day>=? AND day<? ORDER BY day, id",
            (chat_id, start.isoformat(), end.isoformat()),
        )
        return [Expense(r["id"], r["amount"], r["category"], r["note"], date.fromisoformat(r["day"])) for r in rows]

    def categories(self, chat_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) c FROM expenses WHERE chat_id=? GROUP BY category ORDER BY c DESC",
            (chat_id,),
        )
        return [r["category"] for r in rows]

    def delete_last_entry(self, chat_id: int) -> tuple[str, int, str] | None:
        """Удаляет самую свежую запись (расход или доход). Возвращает (тип, сумма, описание)."""
        r = self.conn.execute(
            """
            SELECT * FROM (
                SELECT 'expense' AS t, id, amount, category AS label, created_ns FROM expenses WHERE chat_id=?
                UNION ALL
                SELECT 'income', id, amount, kind, created_ns FROM incomes WHERE chat_id=?
            ) ORDER BY created_ns DESC, id DESC LIMIT 1
            """,
            (chat_id, chat_id),
        ).fetchone()
        if r is None:
            return None
        table = "expenses" if r["t"] == "expense" else "incomes"
        self.conn.execute(f"DELETE FROM {table} WHERE id=?", (r["id"],))
        self.conn.commit()
        return (r["t"], r["amount"], r["label"])

    # --- регулярные платежи ---
    def add_recurring(self, chat_id: int, name: str, amount: int, category: str, day_of_month: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO recurring(chat_id, name, amount, category, day_of_month) VALUES (?, ?, ?, ?, ?)",
            (chat_id, name, amount, category, day_of_month),
        )
        self.conn.commit()
        return cur.lastrowid

    def recurring(self, chat_id: int | None = None) -> list[Recurring]:
        sql = "SELECT * FROM recurring WHERE active=1"
        args: tuple = ()
        if chat_id is not None:
            sql += " AND chat_id=?"
            args = (chat_id,)
        rows = self.conn.execute(sql + " ORDER BY day_of_month, id", args)
        return [
            Recurring(
                r["id"], r["chat_id"], r["name"], r["amount"], r["category"],
                r["day_of_month"], r["last_paid"], r["last_reminded"],
            )
            for r in rows
        ]

    def get_recurring(self, chat_id: int, rec_id: int) -> Recurring | None:
        return next((r for r in self.recurring(chat_id) if r.id == rec_id), None)

    def delete_recurring(self, chat_id: int, rec_id: int) -> bool:
        cur = self.conn.execute("UPDATE recurring SET active=0 WHERE id=? AND chat_id=? AND active=1", (rec_id, chat_id))
        self.conn.commit()
        return cur.rowcount > 0

    def mark_recurring_paid(self, rec_id: int, month: str) -> None:
        self.conn.execute("UPDATE recurring SET last_paid=? WHERE id=?", (month, rec_id))
        self.conn.commit()

    def mark_recurring_reminded(self, rec_id: int, day: date) -> None:
        self.conn.execute("UPDATE recurring SET last_reminded=? WHERE id=?", (day.isoformat(), rec_id))
        self.conn.commit()
