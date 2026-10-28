import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .bot import build_router
from .config import load_config
from .db import Database
from .scheduler import setup_scheduler

COMMANDS = [
    BotCommand(command="balance", description="Сколько осталось до зарплаты"),
    BotCommand(command="week", description="Анализ за прошлую неделю"),
    BotCommand(command="month", description="Расходы за месяц"),
    BotCommand(command="lastmonth", description="Расходы за прошлый месяц"),
    BotCommand(command="income", description="Добавить доход"),
    BotCommand(command="regular", description="Добавить регулярный платёж"),
    BotCommand(command="regulars", description="Регулярные платежи"),
    BotCommand(command="categories", description="Категории"),
    BotCommand(command="export", description="Выгрузка в CSV"),
    BotCommand(command="undo", description="Удалить последнюю запись"),
    BotCommand(command="help", description="Справка"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    db = Database(config.db_path)
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(db, config))
    await bot.set_my_commands(COMMANDS)
    scheduler = setup_scheduler(bot, db, config)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
