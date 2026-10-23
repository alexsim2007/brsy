# Запуск БРС рядом и загрузка на GitHub

Гайд для твоего Mac и проекта `/Users/al/Documents/ChatGPT/brsy`.

Порядок: посмотреть демо → создать своего Telegram-бота → подключить БРС → открыть Mini App в Telegram → загрузить код на GitHub → при необходимости перенести на Raspberry Pi.

**Что ещё не проверено:** доступ к БРС с действующим студенческим токеном и работа на физической Raspberry Pi. Демо работает на вымышленных данных. Авторизованное подключение к БРС нужно проверить на твоём аккаунте.

## 1. Посмотреть интерфейс на Mac

Открой приложение «Терминал» и выполни:

```sh
cd /Users/al/Documents/ChatGPT/brsy
python3 --version
```

Нужен Python 3.11 или новее. На этом Mac подходящий Python уже использовался для разработки. Если на другом Mac его нет — установи через `brew install python`.

Подготовь окружение и зависимости:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Запусти демо:

```sh
BOT_TOKEN= DEMO_MODE=true .venv/bin/python -m brsy
```

`BOT_TOKEN=` здесь намеренно пустой: это позволяет открыть демо даже после настройки настоящего токена в `.env`.

В браузере открой [http://127.0.0.1:8000](http://127.0.0.1:8000).

Попробуй:

- переключить тёмную и светлую темы кнопкой в шапке;
- открыть предмет и вкладку посещаемости;
- найти предмет по названию;
- изменить порог сравнения баллов в настройках;
- скачать CSV.

Баллы в демо вымышленные. Порог и уведомления в демо не сохраняются после перезагрузки страницы; оформление сохраняется на устройстве.

Для остановки нажми **Control + C** в терминале с сервером. Это именно Control, не Command. Пока приложение запущено вручную, этот терминал должен оставаться открытым.

## 2. Создать своего Telegram-бота

В Telegram открой [@BotFather](https://t.me/BotFather):

1. Отправь `/newbot`.
2. Введи отображаемое имя, например `БРС рядом`.
3. Выбери свободный username, заканчивающийся на `bot`.
4. Сохрани выданный токен. Он обычно выглядит как число, двоеточие и длинная строка.

Это **токен Telegram-бота**. Позже понадобится другой токен — **студенческий токен БРС**. Не перепутай их.

## 3. Настроить `.env`

Вернись в папку проекта:

```sh
cd /Users/al/Documents/ChatGPT/brsy
```

Если `.env` ещё не создавался:

```sh
cp -n .env.example .env
chmod 600 .env
```

`cp -n` не перезапишет существующий файл. Если `.env` уже настроен, редактируй его, не заменяя шаблоном.

Сгенерируй ключ шифрования:

```sh
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Это делается один раз при первой настройке.** Сохрани результат в менеджере паролей: без исходного ключа приложение не прочитает уже сохранённые токены после перезапуска или переноса базы.

Открой файл в TextEdit:

```sh
open -e /Users/al/Documents/ChatGPT/brsy/.env
```

Заполни значения, сохранив остальные строки шаблона:

```dotenv
BOT_TOKEN=ТОКЕН_ОТ_BOTFATHER
ENCRYPTION_KEY=СГЕНЕРИРОВАННЫЙ_КЛЮЧ
DATABASE_PATH=data/brsy.sqlite3
HOST=127.0.0.1
PORT=8000
MINI_APP_URL=
POLL_INTERVAL=300
DEMO_MODE=false
```

Заглушки после `=` замени настоящими значениями. `MINI_APP_URL` пока оставь пустым. Сохрани **Command + S**.

**Студенческий токен БРС в `.env` не вставляй.** Его вводят в личном чате с твоим ботом или в Mini App.

## 4. Запустить бота и подключить свой аккаунт БРС

Если демо ещё работает, сначала останови его через Control + C.

```sh
cd /Users/al/Documents/ChatGPT/brsy
DEMO_MODE=false .venv/bin/python -m brsy
```

Открой **своего нового бота** в Telegram и нажми «Запустить» или отправь `/start`.

Получение студенческого токена:

1. В браузере войди как студент на [grade.sfedu.ru](https://grade.sfedu.ru).
2. В том же браузере открой [grade.sfedu.ru/student/authtokenget](https://grade.sfedu.ru/student/authtokenget).
3. Скопируй сам токен из 40 символов.
4. Отправь его в личный чат со своим ботом.

Бот попробует удалить сообщение с токеном, проверит доступ к БРС и сохранит первые баллы.

Проверь команды:

```text
/grades
/semesters
/history
/settings
/export
```

В `/grades` нажми на предмет, чтобы открыть работы и сведения о посещаемости. `/target 80` задаёт порог сравнения баллов. Он не определяет зачёт или допуск.

История сначала пустая: начальная загрузка — точка отсчёта. Уведомления появляются при последующих изменениях итоговых баллов. Проверка по умолчанию раз в 5 минут.

Без HTTPS-адреса бот уже должен работать в переписке. Mini App подключается следующим шагом.

## 5. Открыть Mini App внутри Telegram

### Установить туннель

На этом Mac Homebrew уже есть. Открой **вторую вкладку терминала** — Command + T — и установи:

```sh
brew install cloudflared
```

Бот из предыдущего шага должен продолжать работать в первой вкладке.

Во второй запусти:

```sh
cloudflared tunnel --url http://127.0.0.1:8000
```

Cloudflare напечатает адрес вида:

```text
https://случайное-имя.trycloudflare.com
```

Скопируй **реальный выданный адрес**. Не адрес из примера. Quick Tunnel создаёт временный HTTPS-адрес без собственного домена; он предназначен для тестирования и работает, пока запущен процесс `cloudflared`. [Документация Cloudflare](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

### Подключить адрес к боту

Во второй вкладке оставь туннель работающим. В **третьей вкладке** открой `.env`:

```sh
open -e /Users/al/Documents/ChatGPT/brsy/.env
```

Измени только строку:

```dotenv
MINI_APP_URL=https://ТВОЙ_РЕАЛЬНЫЙ_АДРЕС.trycloudflare.com
```

Сохрани файл. В **первой вкладке** останови бота Control + C и запусти снова:

```sh
cd /Users/al/Documents/ChatGPT/brsy
DEMO_MODE=false .venv/bin/python -m brsy
```

В Telegram отправь `/start`. Появится кнопка **«Открыть БРС рядом»**, а в меню чата — **«Моя БРС»**. Открой приложение через одну из этих кнопок.

Если открыть HTTPS-адрес напрямую в обычном браузере, личные баллы не отобразятся: приложению нужны подписанные данные Telegram. Это ожидаемое поведение.

Для этого способа не обязательно отдельно настраивать Mini App у BotFather: кнопки задаёт сам бот. Main Mini App в BotFather можно добавить позже.

**При новом запуске Quick Tunnel адрес может измениться.** Тогда обнови `MINI_APP_URL` и перезапусти бота. Если Telegram не открывает старую кнопку, снова отправь `/start`.

Для постоянной работы нужен постоянный HTTPS-адрес: например, именованный Cloudflare Tunnel на собственном домене. GitHub хранит код; сам по себе push не запускает Python-бота.

## 6. Загрузить проект на GitHub

Сейчас локальный Git уже настроен на ветку `main`; remote пока отсутствует. Повторно выполнять `git init` не нужно.

### Войти в GitHub из терминала

```sh
brew install gh
gh auth login
```

Выбери:

- GitHub.com;
- HTTPS;
- вход через браузер;
- согласись использовать GitHub credentials для Git, если появится такой вопрос.

Пройди вход в браузере. Затем:

```sh
gh auth status
```

Официальная инструкция: [вход через GitHub CLI](https://docs.github.com/en/github-cli/github-cli/authenticating-with-github-cli).

### Сделать первый коммит

```sh
cd /Users/al/Documents/ChatGPT/brsy
git status --short
git check-ignore .env .venv/ data/brsy.sqlite3
git add .
git diff --cached --name-only
```

В списке подготовленных файлов должны быть исходники, инструкции, `.env.example`, Dockerfile и другие файлы проекта. **Там не должно быть `.env`, `.venv/` или базы данных.** Они уже указаны в `.gitignore`; `.env.example` — безопасный шаблон без значений.

Сделай коммит:

```sh
git commit -m "Initial BRS bot and Mini App"
```

Если Git напишет `Author identity unknown`, один раз задай имя и email **для этого проекта**:

```sh
git config user.name "ТВОЁ ИМЯ"
git config user.email "EMAIL_ИЗ_НАСТРОЕК_GITHUB"
git commit -m "Initial BRS bot and Mini App"
```

Вместо email можно использовать свой GitHub noreply-адрес из настроек аккаунта. Заглушки в командах нужно заменить.

### Создать репозиторий и отправить код

```sh
gh repo create brsy --private --source=. --remote=origin --push
gh repo view --web
```

Команда создаст **приватный** репозиторий `brsy`, привяжет его как `origin` и отправит коммит. Если репозиторий с таким именем у тебя уже существует, выбери другое имя вместо `brsy`; не повторяй создание для уже созданного репозитория. [Документация `gh repo create`](https://cli.github.com/manual/gh_repo_create).

На GitHub проверь, что видны исходники и README, а `.env` отсутствует.

### Отправлять следующие изменения

```sh
cd /Users/al/Documents/ChatGPT/brsy
git status --short
git add .
git diff --cached --name-only
git commit -m "Update bot and Mini App"
git push
```

Если Git пишет `nothing to commit`, новых изменений нет: этот коммит можно пропустить.

## 7. Перенести на Raspberry Pi

Сначала проверь работу на Mac. На Pi используй Raspberry Pi OS **64-bit**. В терминале Pi:

```sh
uname -m
```

Для 64-разрядной ARM-системы ожидается `aarch64`.

### Получить код

Если репозиторий приватный, удобнее клонировать его через GitHub CLI после входа на самой Pi:

```sh
sudo apt update
sudo apt install -y git gh
gh auth login
gh repo clone ТВОЙ_GITHUB_ЛОГИН/brsy
cd brsy
```

Если ты выбрал другое имя репозитория, замени `brsy` в двух последних командах.

### Запуск через Docker Compose

Для этого варианта на Pi должны быть установлены Docker Engine и Compose plugin. Установка зависит от версии Raspberry Pi OS; используй [официальную инструкцию Docker для Debian](https://docs.docker.com/engine/install/debian/). Перед запуском проверь:

```sh
sudo docker version
sudo docker compose version
```

Создай конфигурацию:

```sh
cp -n .env.example .env
chmod 600 .env
nano .env
```

Вставь токен своего бота, ключ шифрования и `DEMO_MODE=false`. В nano: **Control + O**, Enter — сохранить; **Control + X** — выйти.

Если переносишь **существующую базу**, обязательно используй её исходный `ENCRYPTION_KEY`. При чистой установке с новой базой можно создать новый ключ и заново подключить студенческий токен. Для генерации на Pi:

```sh
sudo docker run --rm python:3.12-slim sh -c 'pip -q install cryptography && python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
```

**Останови бота на Mac перед запуском того же BOT_TOKEN на Pi.** Одновременный long polling одного бота в двух процессах вызывает конфликт.

На Pi:

```sh
sudo docker compose up -d --build
sudo docker compose logs -f --tail=50
```

Control + C закроет просмотр логов; сам контейнер продолжит работать.

Проверка:

```sh
curl http://127.0.0.1:8000/healthz
```

Ожидается:

```json
{"status":"ok","mode":"live"}
```

После этого проверь `/start` и `/grades` в Telegram. При новой базе подключи студенческий токен ещё раз.

Для Mini App туннель теперь должен вести на **Pi**, а не на Mac. В `.env` на Pi укажи соответствующий HTTPS-адрес. После изменения `.env`:

```sh
sudo docker compose up -d --force-recreate
```

Пересоздание контейнера сохраняет базу в Docker volume. Для автозапуска контейнера после перезагрузки Docker daemon должен быть включён.

Обновление кода на Pi:

```sh
cd ~/brsy
git pull --ff-only
sudo docker compose up -d --build
```

Остановка:

```sh
sudo docker compose down
```

**Не добавляй `-v` к `down`, если хочешь сохранить базу.** Этот флаг удаляет Docker volume с данными.

## Если что-то не запускается

| Что видишь | Что сделать |
|---|---|
| `address already in use`, порт 8000 занят | Останови предыдущий сервер в его терминале. Не запускай демо и рабочего бота одновременно на одном порту. |
| `DEMO_MODE несовместим с BOT_TOKEN` | Для демо используй `BOT_TOKEN= DEMO_MODE=true .venv/bin/python -m brsy`. |
| Просят `BOT_TOKEN` и `ENCRYPTION_KEY` | Проверь `.env` и запускай из корня проекта. |
| Telegram сообщает, что токен бота неверный | Проверь токен от BotFather, без лишних пробелов. Студенческий токен сюда не подходит. |
| `Token is broken` или БРС отклонила токен | Получи новый студенческий токен через сайт БРС и отправь `/connect`. |
| Сообщение об изменении формата ответа БРС | Может потребоваться адаптация клиента под фактический API. Сохрани текст ошибки без токенов. |
| `ENCRYPTION_KEY не подходит к существующей базе` | Верни исходный ключ. Новый ключ не расшифрует старую базу. |
| Mini App просит открыть её через Telegram | Используй кнопку бота, не обычную ссылку в браузере. При истёкшей сессии закрой Mini App и открой снова. |
| Mini App не открывается | Проверь, что сервер и туннель запущены, а `MINI_APP_URL` содержит текущий HTTPS-адрес. |
| Telegram пишет `Conflict` / `terminated by other getUpdates request` | Останови второй экземпляр бота с тем же BOT_TOKEN. |
| `gh: command not found` | На Mac выполни `brew install gh`, затем открой новую вкладку терминала. |
| `remote origin already exists` | Выполни `git remote -v`: репозиторий уже привязан. Используй `git push`, если это нужный remote. |
| `nothing to commit` | Новых изменений для коммита нет. |

## Необязательная проверка тестами

```sh
cd /Users/al/Documents/ChatGPT/brsy
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Тесты используют имитации API; они не проверяют доступность твоего аккаунта БРС или действительность токена BotFather.
