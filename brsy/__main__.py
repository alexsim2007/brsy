import asyncio
import logging
import os

import httpx
import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .app import create_app
from .bot import build_dispatcher, configure_bot, notification_worker
from .config import Config
from .grade import GradeClient
from .service import Service
from .store import Store
from .schedule import ScheduleClient


async def run():
    os.umask(0o077)
    config = Config.load()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # HTTP libraries can log request URLs, which include the BRS token upstream.
    for name in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    store, bot, tasks = None, None, []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), limits=httpx.Limits(max_connections=4), headers={"Cookie": ""}) as client:
            if config.demo:
                service = None
            else:
                store = Store(config.database_path, config.encryption_key)
                service = Service(store, GradeClient(client))
                bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                await configure_bot(bot, config.mini_app_url)
                dp = build_dispatcher(service, config.mini_app_url)
                tasks.append(asyncio.create_task(dp.start_polling(bot, handle_signals=False, close_bot_session=False), name="telegram"))
                tasks.append(asyncio.create_task(notification_worker(service, bot, config.poll_interval), name="notifications"))
            schedule = ScheduleClient(client, demo=config.demo)
            server = uvicorn.Server(uvicorn.Config(create_app(config, service, schedule), host=config.host, port=config.port, access_log=False, log_level="info"))
            tasks.append(asyncio.create_task(server.serve(), name="web"))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()
        if store:
            store.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
