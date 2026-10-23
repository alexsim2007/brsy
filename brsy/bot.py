import asyncio
import csv
import html
import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo)

from .grade import GradeError
from .service import Service

log = logging.getLogger(__name__)
INSTRUCTION = (
    "<b>Подключим твою БРС</b>\n\n"
    '1. Открой <a href="https://grade.sfedu.ru">сайт БРС ЮФУ</a> в браузере и войди как студент.\n'
    '2. В этом же браузере перейди на <a href="https://grade.sfedu.ru/student/authtokenget">страницу получения токена</a>.\n'
    "3. Скопируй токен из 40 символов и отправь сюда.\n\n"
    "Если вместо токена открывается вход — сначала войди в БРС, затем открой ссылку снова. "
    "На телефоне используй один и тот же внешний браузер для обоих шагов.\n\n"
    "Пароль от университетской почты не нужен. Токен хранится зашифрованным. "
    "Сообщение с токеном бот постарается удалить.\n/cancel — отменить"
)


class Connect(StatesGroup):
    token = State()


def fmt(value):
    return "—" if value is None else f"{value:g}"


def csv_bytes(subjects):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Предмет", "Баллы", "Текущий максимум", "Вид контроля", "Преподаватель"])
    for s in subjects:
        row = [s["name"], s["score"], s["available"], s["type"], s["teacher"]]
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in row])
    return stream.getvalue().encode("utf-8-sig")


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text[:64], callback_data=data) for text, data in row] for row in rows])


def menu(url):
    rows = [[InlineKeyboardButton(text="📚 Мои баллы", callback_data="grades"),
             InlineKeyboardButton(text="↻ Обновить", callback_data="refresh")],
            [InlineKeyboardButton(text="🗓 Семестры", callback_data="semesters"),
             InlineKeyboardButton(text="◷ История", callback_data="history")],
            [InlineKeyboardButton(text="⚙ Настройки", callback_data="settings")]]
    if url:
        rows.insert(0, [InlineKeyboardButton(text="Открыть БРС рядом ↗", web_app=WebAppInfo(url=url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dispatcher(service: Service, url: str) -> Dispatcher:
    router = Router()
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")

    @router.message.outer_middleware()
    async def errors(handler, event, data):
        try:
            return await handler(event, data)
        except (GradeError, ValueError) as exc:
            await event.answer(html.escape(str(exc)))
        except Exception:
            # Never log Telegram Update objects or exception strings (may contain tokens).
            log.error("Bot handler failed")
            await event.answer("Не удалось выполнить действие. Попробуй ещё раз позже.")

    @router.callback_query.outer_middleware()
    async def callback_errors(handler, event, data):
        try:
            return await handler(event, data)
        except (GradeError, ValueError) as exc:
            await event.message.answer(html.escape(str(exc)))
        except Exception:
            log.error("Bot callback failed")
            await event.message.answer("Не удалось выполнить действие. Попробуй позже.")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        if service.store.user(message.from_user.id):
            await message.answer("<b>БРС рядом</b>\nБаллы, предметы и изменения — в одном месте.", reply_markup=menu(url))
        else:
            await state.set_state(Connect.token)
            await message.answer("Привет! Это <b>БРС рядом</b> — независимый помощник для студентов ЮФУ.\n\n" + INSTRUCTION)

    @router.message(Command("connect", "instruction"))
    async def connect(message: Message, state: FSMContext):
        await state.set_state(Connect.token)
        await message.answer(INSTRUCTION)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Ввод отменён. /connect — подключить БРС.")

    async def grades(message, uid, semester=None, force=False):
        data = await service.dashboard(uid, semester, force=force)
        title = next((s["title"] for s in data["semesters"] if s["id"] == data["semester"]), "Текущий семестр")
        lines = [f"<b>{html.escape(title)}</b>\n"]
        for s in data["subjects"]:
            lines.append(f"{html.escape(s['name'])}: <b>{fmt(s['score'])}</b> / 100 · сейчас доступно {fmt(s['available'])}")
        if not data["subjects"]:
            lines.append("В этом семестре пока нет предметов.")
        lines.append(f"\nСредний балл: <b>{fmt(data['stats']['average'])}</b> / 100\nПорог сравнения: {data['settings']['target']} / 100")
        lines.append("Обновлено: " + datetime.fromtimestamp(data["updated"], ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M МСК"))
        if data["error"]:
            lines.append("⚠ " + html.escape(data["error"]))
        # Telegram messages are limited to 4096 chars; one subject button per row, paged separately if needed.
        for offset in range(0, len(data["subjects"]), 20):
            chunk = data["subjects"][offset:offset + 20]
            text = "\n".join(lines) if offset == 0 and len("\n".join(lines)) < 3800 else "<b>Предметы</b>\n" + "\n".join(f"{html.escape(s['name'][:100])}: {fmt(s['score'])}/100" for s in chunk)
            await message.answer(text, reply_markup=keyboard([[(s["name"][:55], f"subject:{s['id']}:{data['semester']}")] for s in chunk]))
        if not data["subjects"]:
            await message.answer("\n".join(lines))
        await message.answer("Что посмотрим?", reply_markup=menu(url))

    @router.message(Command("grades", "refresh"))
    async def show_grades(message: Message):
        await grades(message, message.from_user.id, force=message.text.split()[0].split("@")[0] == "/refresh")

    async def semesters(message, uid):
        items = await service.semesters(uid)
        await message.answer("<b>Выбери семестр</b>", reply_markup=keyboard([[(s["title"], "semester:" + s["id"])] for s in items]))

    @router.message(Command("semesters"))
    async def show_semesters(message: Message):
        await semesters(message, message.from_user.id)

    async def history(message, uid):
        service.require(uid)
        events = service.store.history(uid, 15)
        lines = ["<b>Последние изменения</b>\n"]
        for e in events:
            lines.append(f"{html.escape(e['subject'][:120])}: {fmt(e['old'])} → <b>{fmt(e['new'])}</b>")
        if not events:
            lines.append("Изменений пока нет. Начну записывать их после подключения.")
        await message.answer("\n".join(lines))

    @router.message(Command("history"))
    async def show_history(message: Message):
        await history(message, message.from_user.id)

    async def settings(message, uid):
        user = service.require(uid)
        enabled = user["notifications"]
        await message.answer(f"<b>Твои настройки</b>\n\nУведомления: {'включены' if enabled else 'выключены'}\nПорог сравнения: {user['target']} / 100\n\n/target 80 — изменить порог\n/export — выгрузить баллы\n/connect — заменить токен\n/logout — удалить мои данные", reply_markup=keyboard([[('Выключить уведомления' if enabled else 'Включить уведомления', "notify")]]))

    @router.message(Command("settings"))
    async def show_settings(message: Message):
        await settings(message, message.from_user.id)

    @router.message(Command("target"))
    async def target(message: Message):
        service.require(message.from_user.id)
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 100:
            await message.answer("Например: /target 80. Порог — число от 1 до 100 для сравнения баллов. Не определяет зачёт или допуск.")
            return
        service.store.settings(message.from_user.id, target=int(parts[1]))
        await message.answer(f"Порог сохранён: {parts[1]} / 100.")

    @router.message(Command("export"))
    async def export(message: Message):
        data = await service.dashboard(message.from_user.id)
        await message.answer_document(BufferedInputFile(csv_bytes(data["subjects"]), filename="brsy-grades.csv"))

    @router.message(Command("logout"))
    async def logout(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Удалить токен, сохранённые баллы и историю из этого бота?", reply_markup=keyboard([[('Да, удалить', 'delete'), ('Оставить', 'keep')]]))

    @router.message(Command("help", "privacy"))
    async def help_message(message: Message):
        await message.answer("<b>БРС рядом · независимый проект</b>\n\n/grades — баллы и предметы\n/refresh — проверить изменения\n/semesters — другие семестры\n/history — история баллов\n/settings — уведомления и порог баллов\n/export — CSV\n/connect — подключить или заменить токен\n/instruction — как получить токен\n/logout — удалить мои данные\n\nБот хранит Telegram ID, имя, зашифрованный токен, баллы, историю и настройки в базе у владельца сервера. Пароль ЮФУ не запрашивается. Данные запрашиваются только у grade.sfedu.ru. История — с момента подключения, уведомления — об изменении итоговых баллов предмета. /logout удаляет данные из рабочей базы; резервные копии управляются владельцем сервера.")

    @router.callback_query()
    async def callback(query: CallbackQuery):
        await query.answer()
        action, uid, message = query.data or "", query.from_user.id, query.message
        if action in ("grades", "refresh"):
            await grades(message, uid, force=action == "refresh")
        elif action.startswith("semester:"):
            await grades(message, uid, semester=action.split(":", 1)[1])
        elif action == "semesters":
            await semesters(message, uid)
        elif action == "history":
            await history(message, uid)
        elif action == "settings":
            await settings(message, uid)
        elif action == "notify":
            user = service.require(uid)
            service.store.settings(uid, notifications=not user["notifications"])
            await settings(message, uid)
        elif action == "delete":
            await service.delete(uid)
            await message.edit_reply_markup(reply_markup=None)
            await message.answer("Токен, баллы и история удалены. /connect — подключиться снова.")
        elif action == "keep":
            await message.edit_reply_markup(reply_markup=None)
            await message.answer("Данные сохранены.")
        elif action.startswith("subject:"):
            _, sid, semester = action.split(":", 2)
            detail = await service.detail(uid, sid, semester)
            lines = [f"<b>{html.escape(detail['name'])}</b>\n{html.escape(detail['teacher'])}\nБаллы: {fmt(detail['score'])} / 100\n"]
            for module in detail["modules"]:
                lines.append(f"<b>{html.escape(module['title'])}</b>")
                for w in module["works"]:
                    lines.append(f"{html.escape(w['title'][:150])}: {fmt(w['score'])} / {fmt(w['max'])}")
            if detail["journal"]:
                lessons = detail["journal"]["lessons"]
                attended = sum(1 for lesson in lessons if lesson.get("Attended") in (True, 1, "1"))
                lines.append(f"\nПосещаемость: {attended} из {len(lessons)} отмеченных занятий")
            elif detail["journal_error"]:
                lines.append("\nЖурнал временно недоступен.")
            # Send per-line chunks without splitting HTML tags.
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) > 3800:
                    await message.answer(chunk)
                    chunk = ""
                chunk += line + "\n"
            if chunk:
                await message.answer(chunk)

    @router.message(Connect.token, F.text)
    @router.message(F.text.regexp(r"^[\s\"'`]*[A-Za-z0-9_-]{40}[\s\"'`]*$"))
    async def token_message(message: Message, state: FSMContext):
        token = message.text
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await service.link(message.from_user.id, token, message.from_user.first_name)
        await state.clear()
        await message.answer("<b>БРС подключена ✓</b>\nПервые баллы сохранены. Изменения будут приходить сюда.", reply_markup=menu(url))

    @router.message()
    async def fallback(message: Message):
        await message.answer("/connect — подключить БРС\n/grades — посмотреть баллы\n/help — все команды")

    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def configure_bot(bot: Bot, url: str):
    # This application uses polling, including when a host also provides a web domain.
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in [
        ("grades", "Мои баллы"), ("refresh", "Обновить"), ("semesters", "Семестры"),
        ("history", "История изменений"), ("settings", "Настройки"), ("export", "Экспорт CSV"),
        ("instruction", "Как получить токен"), ("connect", "Подключить БРС"),
        ("logout", "Удалить мои данные"), ("help", "Помощь и приватность")]])
    if url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Моя БРС", web_app=WebAppInfo(url=url)))


async def notification_worker(service: Service, bot: Bot, interval: int):
    async def check(uid):
        try:
            await service.sync(uid, force=True)
        except GradeError:
            pass
        except Exception:
            log.error("Background sync failed")

    while True:
        try:
            due = service.store.due(interval)
            for start in range(0, len(due), 4):
                await asyncio.gather(*(check(uid) for uid in due[start:start + 4]))
            for uid in service.store.notification_users():
                events = service.store.pending(uid)
                if not events:
                    continue
                lines = ["<b>В БРС изменились баллы</b>\n"]
                for e in events:
                    lines.append(f"{html.escape(e['subject'][:120])}: {fmt(e['old'])} → <b>{fmt(e['new'])}</b>")
                try:
                    await bot.send_message(uid, "\n".join(lines))
                except TelegramForbiddenError:
                    service.store.settings(uid, notifications=False)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(min(exc.retry_after, 60))
                except TelegramAPIError:
                    pass  # Outbox remains pending; retry on the next iteration.
                else:
                    service.store.delivered(uid, [e["id"] for e in events])
        except Exception:
            log.error("Notification worker iteration failed")
        await asyncio.sleep(15)
