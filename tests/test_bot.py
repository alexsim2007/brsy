from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, DeleteWebhook, EditMessageReplyMarkup, SendMessage, SetChatMenuButton, SetMyCommands
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User
from cryptography.fernet import Fernet

from brsy.bot import build_dispatcher, configure_bot
from brsy.service import Service
from brsy.store import Store
from test_security import BOT_TOKEN
from test_store_service import FakeGrade


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.deleted = []
        self.methods = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.methods.append(method)
        if isinstance(method, (DeleteWebhook, SetChatMenuButton, SetMyCommands)):
            return True
        if isinstance(method, SendMessage):
            message = Message(message_id=100+len(self.sent), date=datetime.now(timezone.utc),
                              chat=Chat(id=int(method.chat_id), type='private'),
                              from_user=User(id=bot.id, is_bot=True, first_name='Бот'), text=method.text,
                              reply_markup=method.reply_markup)
            self.sent.append(message)
            return message
        if isinstance(method, DeleteMessage):
            self.deleted.append(method.message_id)
            return True
        if isinstance(method, EditMessageReplyMarkup):
            return self.sent[-1]
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(type(method))

    async def stream_content(self, *args, **kwargs):
        yield b''


async def test_migration_to_polling_preserves_pending_updates_and_sets_hosted_app_url():
    session = TelegramSession()
    async with Bot(BOT_TOKEN, session=session) as bot:
        await configure_bot(bot, 'https://example.bothost.tech')
    assert isinstance(session.methods[0], DeleteWebhook)
    assert session.methods[0].drop_pending_updates is False
    menu = next(method for method in session.methods if isinstance(method, SetChatMenuButton))
    assert menu.menu_button.web_app.url == 'https://example.bothost.tech'


async def test_telegram_conversation_start_token_grades_settings_delete(tmp_path):
    store = Store(str(tmp_path/'bot-db'), Fernet.generate_key().decode())
    session=TelegramSession()
    bot=Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    service=Service(store, FakeGrade())
    dp=build_dispatcher(service, '')
    uid=42
    user=User(id=uid, is_bot=False, first_name='Александр')
    update_id=0
    async def send(text, group=False):
        nonlocal update_id
        update_id+=1
        entities=[MessageEntity(type='bot_command',offset=0,length=len(text.split()[0]))] if text.startswith('/') else None
        msg=Message(message_id=update_id,date=datetime.now(timezone.utc),chat=Chat(id=uid if not group else -1,type='private' if not group else 'group'),from_user=user,text=text,entities=entities)
        await dp.feed_update(bot, Update(update_id=update_id,message=msg))
    try:
        await send('/start', group=True)
        assert session.sent == []
        await send('/start')
        assert '40 символов' in session.sent[-1].text
        await send('s'*40)
        assert store.token(uid) == 's'*40
        assert session.deleted == [3]
        assert 'БРС подключена' in session.sent[-1].text
        await send('/grades')
        assert any('Анализ' in m.text and '70' in m.text for m in session.sent)
        await send('/target 88')
        assert store.user(uid)['target']==88
        await send('/settings')
        assert '88' in session.sent[-1].text
        await send('/logout')
        assert store.user(uid) is not None  # requires concrete confirmation
        confirm=session.sent[-1]
        await dp.feed_update(bot, Update(update_id=1000,callback_query=CallbackQuery(id='cb1',from_user=user,chat_instance='x',message=confirm,data='delete')))
        assert store.user(uid) is None
        assert 'удалены' in session.sent[-1].text
    finally:
        await dp.storage.close()
        await bot.session.close()
        store.close()


async def test_failed_notification_is_retried_without_losing_outbox(tmp_path, monkeypatch):
    from aiogram.exceptions import TelegramNetworkError
    from brsy.bot import notification_worker
    from test_store_service import subject

    class StopWorker(BaseException):
        pass

    class FlakyBot:
        def __init__(self):
            self.calls=0
        async def send_message(self, uid, text):
            self.calls+=1
            if self.calls==1:
                raise TelegramNetworkError(method=SendMessage(chat_id=uid,text=text), message='offline')

    store=Store(str(tmp_path/'outbox-db'), Fernet.generate_key().decode())
    service=Service(store,FakeGrade())
    await service.link(42,'s'*40,'Тест')
    store.save(42,'10',[subject(80)],current=True)
    bot=FlakyBot()
    iterations=0
    async def next_iteration(_):
        nonlocal iterations
        iterations+=1
        if iterations==1:
            assert len(store.pending(42))==1
        if iterations>=2:
            raise StopWorker
    monkeypatch.setattr('brsy.bot.asyncio.sleep',next_iteration)
    try:
        with pytest.raises(StopWorker):
            await notification_worker(service,bot,300)
        assert bot.calls==2
        assert store.pending(42)==[]
    finally:
        store.close()
