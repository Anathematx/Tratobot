"""Разбор пользовательского ввода и форматирование сумм."""
import re
from dataclasses import dataclass
from datetime import date, timedelta

_AMOUNT_RE = re.compile(r"^(\d+(?:[.,]\d{1,2})?)(к|k|тыс)?$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$")


class ParseError(ValueError):
    pass


def parse_amount(token: str) -> int | None:
    """'1500' -> 150000 коп., '99,90' -> 9990, '2.5к' -> 250000. None, если это не сумма."""
    m = _AMOUNT_RE.match(token.strip().replace("₽", "").replace("р", "") if token else "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    if m.group(2):
        value *= 1000
    kopecks = round(value * 100)
    return kopecks if kopecks > 0 else None


def parse_day(token: str, today: date) -> date | None:
    t = token.lower()
    if t == "сегодня":
        return today
    if t == "вчера":
        return today - timedelta(days=1)
    if t == "позавчера":
        return today - timedelta(days=2)
    m = _DATE_RE.match(t)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    year = today.year if y is None else (int(y) + 2000 if len(y) == 2 else int(y))
    try:
        result = date(year, mo, d)
    except ValueError:
        return None
    # «31.12», введённое в январе, — это прошлый год
    if y is None and result > today:
        result = result.replace(year=year - 1)
    return result


def _merge_number_groups(words: list[str]) -> list[str]:
    """Склеивает «1 500» в «1500», чтобы суммы с пробелами-разделителями тоже понимались."""
    out: list[str] = []
    for w in words:
        if out and re.fullmatch(r"\d{3}([.,]\d{1,2})?", w) and re.fullmatch(r"\d{1,3}", out[-1]):
            out[-1] += w
        else:
            out.append(w)
    return out


@dataclass
class ParsedEntry:
    amount: int
    label: str  # категория расхода или вид дохода
    note: str
    day: date


def parse_entry(text: str, today: date, default_label: str | None = None) -> ParsedEntry:
    """
    Понимает «500 продукты», «продукты 500 молоко и хлеб», «1 200 такси вчера», «300 кафе 25.10».
    Первое слово, не являющееся суммой или датой, — категория, остальное — комментарий.
    """
    words = _merge_number_groups(text.split())
    amount = None
    day = today
    rest: list[str] = []
    for w in words:
        if amount is None and (a := parse_amount(w)) is not None:
            amount = a
            continue
        if (d := parse_day(w, today)) is not None:
            day = d
            continue
        rest.append(w)
    if amount is None:
        raise ParseError("Не нашёл сумму. Пример: «500 продукты».")
    if not rest and default_label is None:
        raise ParseError("Укажите категорию. Пример: «500 продукты».")
    label = rest[0].lower() if rest else default_label
    note = " ".join(rest[1:]) if rest else ""
    return ParsedEntry(amount=amount, label=label, note=note, day=day)


@dataclass
class ParsedRecurring:
    day_of_month: int
    amount: int
    category: str
    name: str


def parse_recurring(text: str) -> ParsedRecurring:
    """«15 1200 связь Мобильный телефон» -> день, сумма, категория, название."""
    words = text.split()
    if len(words) < 3:
        raise ParseError("Формат: /regular <день месяца> <сумма> <категория> [название]")
    if not words[0].isdigit() or not 1 <= int(words[0]) <= 31:
        raise ParseError("День месяца должен быть числом от 1 до 31.")
    words = [words[0]] + _merge_number_groups(words[1:])
    amount = parse_amount(words[1])
    if amount is None:
        raise ParseError("Не понял сумму платежа.")
    if len(words) < 3:
        raise ParseError("Укажите категорию платежа.")
    category = words[2].lower()
    name = " ".join(words[3:]) or category.capitalize()
    return ParsedRecurring(int(words[0]), amount, category, name)


def fmt_money(kopecks: int) -> str:
    rub, kop = divmod(abs(kopecks), 100)
    s = f"{rub:,}".replace(",", " ")
    if kop:
        s += f",{kop:02d}"
    return ("−" if kopecks < 0 else "") + s + " ₽"
