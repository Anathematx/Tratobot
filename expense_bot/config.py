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
    report_hour: int = 9
    # Утренний свод за вчерашний день (час)
    daily_hour: int = 9
    # Через сколько минут удалять сообщения из чата (0 — не удалять)
    autodelete_minutes: int = 30
    # Ежедневная проверка плановых платежей
    reminder_hour: int = 10
    # За сколько дней предупреждать о платеже
    remind_days_before: int = 2
    # Дни выплат (например, 5 и 20). Пусто — бот угадывает по истории поступлений
    paydays: tuple[int, ...] = ()


def load_env_file(path: str = ".env") -> None:
    """Подхватывает настройки из .env при запуске без Docker; уже заданные переменные не перезаписывает."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> Config:
    load_env_file()
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    return Config(
        bot_token=token,
        db_path=os.environ.get("DB_PATH", "data/expenses.db"),
        tz=ZoneInfo(os.environ.get("TZ_NAME", "Europe/Moscow")),
        allowed_users=frozenset(_int_set(os.environ.get("ALLOWED_USERS", ""))),
        report_day=os.environ.get("REPORT_DAY", "mon"),
        report_hour=int(os.environ.get("REPORT_HOUR", "9")),
        daily_hour=int(os.environ.get("DAILY_HOUR", "9")),
        autodelete_minutes=int(os.environ.get("AUTODELETE_MINUTES", "30")),
        reminder_hour=int(os.environ.get("REMINDER_HOUR", "10")),
        remind_days_before=int(os.environ.get("REMIND_DAYS_BEFORE", "2")),
        paydays=tuple(sorted(_int_set(os.environ.get("PAYDAYS", "")))),
    )
