# brsy

Неофициальный Telegram-бот и Mini App для БРС ЮФУ. Позволяет смотреть баллы, учебные работы и посещаемость, получать уведомления об изменениях и выгружать оценки в CSV.

Независимый проект, не связан с администрацией ЮФУ. [Бот в Telegram](https://t.me/brs_app_bot).

## Возможности

- Предметы и баллы по семестрам.
- Карточки предметов с работами, преподавателями и посещаемостью.
- Уведомления об изменении итоговых баллов и история с момента подключения.
- Экспорт баллов в CSV.
- Mini App с чёрной и белой темами и системным шрифтом.
- Подключение по токену БРС, замена токена и удаление своих данных.

Пароль от университетской почты не запрашивается. Токен БРС хранится зашифрованным с помощью Fernet. Также сохраняются Telegram ID, имя, снимки предметов и баллов, история изменений, настройки и сведения о синхронизации. Удалить данные из рабочей базы можно командой `/logout`.

Бот и Mini App работают в одном Python-процессе. Стек: Python, aiogram, FastAPI, SQLite и обычные HTML, CSS и JavaScript.

## Запуск на ПК

Нужны **Python 3.11+**, Git и токен своего Telegram-бота от [@BotFather](https://t.me/BotFather). Чтобы создать бота, отправить BotFather команду `/newbot` и сохранить выданный токен.

### 1. Скачать проект

```sh
git clone https://github.com/alexsim2007/brsy.git
cd brsy
```

### 2. Установить зависимости

**macOS / Linux:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
chmod 600 .env
```

**Windows, PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### 3. Настроить `.env`

При первой настройке сгенерировать ключ шифрования.

**macOS / Linux:**

```sh
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Windows:**

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Открыть `.env` в текстовом редакторе и заполнить:

```dotenv
BOT_TOKEN=токен_от_BotFather
ENCRYPTION_KEY=сгенерированный_ключ
DEMO_MODE=false
```

`ENCRYPTION_KEY` создаётся **один раз**. При следующих запусках использовать тот же ключ. `.env` и папку `data` не добавлять в Git.

### 4. Запустить бота

**macOS / Linux:**

```sh
.venv/bin/python -m brsy
```

**Windows:**

```powershell
.\.venv\Scripts\python.exe -m brsy
```

Открыть **своего бота** в Telegram и отправить `/start`. После входа в личный кабинет БРС перейти на [страницу выдачи токена](https://grade.sfedu.ru/student/authtokenget), скопировать студенческий токен и отправить его боту. Это отдельный токен, он отличается от выданного BotFather.

Основные команды: `/grades` — баллы, `/refresh` — обновить, `/semesters` — выбрать семестр, `/history` — история, `/settings` — настройки, `/export` — CSV.

Бот работает, пока запущен процесс. Для остановки нажать **Ctrl+C**. С одним токеном Telegram должен работать один экземпляр бота.

### Просмотр Mini App на ПК

Для просмотра интерфейса с вымышленными данными в `.env` временно оставить `BOT_TOKEN` пустым и установить `DEMO_MODE=true`, затем запустить приложение той же командой. Открыть [http://127.0.0.1:8000](http://127.0.0.1:8000) в браузере.

Для возвращения к рабочему боту восстановить `BOT_TOKEN`, установить `DEMO_MODE=false` и перезапустить приложение.
