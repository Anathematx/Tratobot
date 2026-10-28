import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _int_set(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str = "data/expenses.db"
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Moscow"))
    # Пустое множество — бот отвечает всем; иначе только перечисленным Telegram ID
    allowed_users: frozenset[int] = frozenset()
    # Еженедельный отчёт: день недели (mon..sun) и час
    report_day: str = "mon"
    report_hour: int = 10
    # Ежедневная проверка плановых платежей
    reminder_hour: int = 10
    # За сколько дней предупреждать о платеже
    remind_days_before: int = 2
    # Дни выплат (например, 5 и 20). Пусто — бот угадывает по истории поступлений
    paydays: tuple[int, ...] = ()


def load_config() -> Config:
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    return Config(
        bot_token=token,
        db_path=os.environ.get("DB_PATH", "data/expenses.db"),
        tz=ZoneInfo(os.environ.get("TZ_NAME", "Europe/Moscow")),
        allowed_users=frozenset(_int_set(os.environ.get("ALLOWED_USERS", ""))),
        report_day=os.environ.get("REPORT_DAY", "mon"),
        report_hour=int(os.environ.get("REPORT_HOUR", "10")),
        reminder_hour=int(os.environ.get("REMINDER_HOUR", "10")),
        remind_days_before=int(os.environ.get("REMIND_DAYS_BEFORE", "2")),
        paydays=tuple(sorted(_int_set(os.environ.get("PAYDAYS", "")))),
    )
