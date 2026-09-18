# HH-Killer

Telegram-бот для AI-поиска вакансий на польских и CIS job-порталах. Пользователь описывает должность или загружает резюме, задаёт регионы и фильтры — бот собирает вакансии, отсекает неподходящие и присылает карточки со сводкой от модели.

Доступ закрыт whitelist’ом Telegram user ID. На VPS бот работает в Docker через **long polling**: исходящий HTTPS к Telegram, входящие HTTP-порты не нужны.

## Что умеет бот

1. **Запрос.** Текст («Python developer, remote, Warszawa») или резюме PDF / DOCX / TXT / RTF — модель достаёт должность, навыки, опыт и город.
2. **Настройка.** Один экран: до пяти регионов, формат работы, занятость, зарплата, опыт, свежесть, сайты.
3. **Поиск.** Прогресс в чате, можно остановить и получить уже найденное. Параллельные поиски ограничены (`SEARCH_MAX_CONCURRENT`, по умолчанию 1).
4. **Результат.** До 10 карточек: должность, компания, локация, занятость, зарплата, дата, описание, ссылка, AI-сводка. Опционально — отброшенные вакансии с причиной.

Пресеты сайтов: Pracuj.pl, Praca.pl, OLX Praca, LinkedIn, Indeed, HH.ru / HH PL и другие. Можно добавить свой URL.

Команды: `/start`, `/help`, `/whoami` (свой Telegram ID).

## Как это устроено

```
Telegram-клиент
      │
      ▼
 Telegram API (HTTPS)
      │  long polling, исходящий TLS
      ▼
 run_bot.py  (aiogram 3, FSM в памяти)
      │
      ├─ Playwright stealth (общий Chromium на процесс)
      ├─ Apify (опционально: LinkedIn / Indeed / fallback)
      └─ OpenRouter (разбор резюме и оценка вакансий)
```

Точка входа бота: `python run_bot.py`.  
`python main.py` — CLI-демо скрейпера, не публичный сервис.

## Ограничения

- FSM и поисковые сессии **в памяти**: рестарт контейнера сбрасывает незавершённый диалог.
- Один браузер Playwright на процесс; параллельные поиски упираются в RAM.
- Сайты могут отдавать CAPTCHA / блокировку — нужны прокси, Apify или cookies.
- `PLAYWRIGHT_STORAGE_STATE` общий на всех пользователей процесса.

## Требования

- Python 3.11+ (локально) или Docker Engine + Compose на VPS
- Токены: Telegram (BotFather), OpenRouter; Apify желателен для LinkedIn/Indeed
- RAM: от **2 GB** (Chromium + модель + бот)
- Исходящий HTTPS (443) к `api.telegram.org`, `openrouter.ai`, `api.apify.com` и job-сайтам

## Переменные окружения

Скопируйте [`.env.example`](.env.example) в `.env` и заполните секреты. Файл не попадает в git и в Docker-образ.

| Переменная | Обязательна | Описание |
|---|---|---|
| `OPENROUTER_API_KEY` | да | Ключ [openrouter.ai/keys](https://openrouter.ai/keys) |
| `OPENROUTER_MODEL` | нет | По умолчанию `google/gemini-2.5-flash-lite` |
| `TELEGRAM_BOT_TOKEN` | да | Токен от [@BotFather](https://t.me/BotFather) |
| `ALLOWED_USER_IDS` | да | Telegram user ID через запятую |
| `SEARCH_MAX_CONCURRENT` | нет | Одновременные поиски, по умолчанию `1` |
| `LOG_FILE` | нет | Путь к лог-файлу; пусто — только stdout. Локально по умолчанию `bot.log` |
| `APIFY_API_TOKEN` | нет | Без токена LinkedIn/Indeed идут через Playwright (часто блок) |
| `APIFY_MAX_RESULTS` | нет | Максимум вакансий за один Apify run (`10`) |
| `APIFY_MAX_RUNS_PER_SESSION` | нет | Лимит запусков Apify **на жизнь процесса** (`5`) |
| `APIFY_MAX_RUNS_PER_DAY` | нет | Суточный лимит UTC; `0` — выключен |
| `APIFY_FALLBACK_PLAYWRIGHT` | нет | Fallback, если квота Apify кончилась (`true`) |
| `PLAYWRIGHT_USE_CHROME` | нет | `true` — системный Chrome; в Docker всегда `false` |
| `PLAYWRIGHT_HEADLESS` | нет | `true` на сервере |
| `PLAYWRIGHT_LOCALE` / `PLAYWRIGHT_TIMEZONE` | нет | По умолчанию `pl-PL` / `Europe/Warsaw` |
| `PLAYWRIGHT_PROXY` / `PLAYWRIGHT_PROXIES` | нет | Один прокси или список через запятую |
| `PLAYWRIGHT_STORAGE_STATE` | нет | JSON cookies (Playwright storage state) |
| `PLAYWRIGHT_BLOCK_ASSETS` | нет | Не грузить картинки/шрифты (`true`) |
| `SCRAPER_DOMAIN_INTERVAL` | нет | Пауза между запросами к одному домену, сек (`2.5`) |

### Где взять токены

1. **Telegram.** [@BotFather](https://t.me/BotFather) → `/newbot` → скопировать токен. Username бота можно не скрывать: без ID из whitelist бот не работает.
2. **Свой Telegram ID.** Напишите боту любое сообщение — в ответе «Нет доступа» будет ID. Передайте его администратору. После добавления в `ALLOWED_USER_IDS` и перезапуска контейнера работает `/whoami`.
3. **OpenRouter.** [openrouter.ai/keys](https://openrouter.ai/keys). Без валидного ключа процесс не стартует.
4. **Apify.** [console.apify.com](https://console.apify.com) → API token. Бесплатный план ограничен; лимиты задаются переменными выше.

## Локальный запуск

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# опционально, если PLAYWRIGHT_USE_CHROME=true:
# playwright install chrome

cp .env.example .env
# заполните OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS

python run_bot.py
```

Cookies после ручного логина (headed-браузер, не для Docker):

```bash
python scripts/save_storage_state.py https://www.pracuj.pl storage/pracuj_state.json
# затем PLAYWRIGHT_STORAGE_STATE=storage/pracuj_state.json
```

## Docker

Образ: `mcr.microsoft.com/playwright/python:v1.62.0-noble` + зависимости из `requirements.txt` (`playwright==1.62.0`). Порты не публикуются.

```bash
cp .env.example .env
# заполните секреты; PLAYWRIGHT_USE_CHROME в контейнере принудительно false

mkdir -p logs storage
docker compose up -d --build
docker compose logs -f bot
```

Права на `logs/`: процесс в контейнере идёт от `pwuser` (обычно uid 1000). Если файл лога не создаётся:

```bash
sudo chown -R 1000:1000 logs
```

Остановка только бота: `docker compose down`. Это не трогает чужие контейнеры (например AmneziaWG). Не используйте `--remove-orphans` и не перезапускайте `docker.service` ради бота — из‑за iptables может отвалиться VPN.

## Деплой на VPS

Ниже — чеклист для Ubuntu/Debian. Домен, nginx и Let’s Encrypt **не нужны**.

### 1. Сервер

- 2 GB RAM или больше, диск ~10 GB
- Исходящий 443 открыт
- Входящие: только SSH

### 2. SSH

- Вход по ключу, парольный логин выключен (`PasswordAuthentication no`)
- По желанию: нестандартный порт SSH, fail2ban

### 3. Firewall

Пример ufw (подставьте свой SSH-порт):

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw enable
```

Порты 80/443 снаружи **не открывать**. Проверка после запуска бота: `ss -tlnp` — у контейнера нет слушающих 80/443.

### 4. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# перелогиньтесь
```

### 5. Код и секреты

```bash
git clone <ваш-репозиторий> HH-Killer
cd HH-Killer
cp .env.example .env
chmod 600 .env
mkdir -p logs storage
```

В `.env` обязательно:

- `TELEGRAM_BOT_TOKEN`
- `OPENROUTER_API_KEY`
- `ALLOWED_USER_IDS` — свой ID и ID людей, которым даёте доступ

Не копируйте `.env` в образ и не коммитьте его.

### 6. Запуск

```bash
docker compose up -d --build
docker compose logs -f bot
```

В логах не должно быть токенов целиком. OpenRouter-ключ пишется в маскированном виде.

### 7. Обновление

```bash
git pull
docker compose up -d --build
```

Рестарт сбрасывает незавершённые диалоги (память процесса).

### 8. Проверка доступа

- Пользователь из whitelist: `/start` открывает меню
- Чужой аккаунт: «Нет доступа» и ID в ответе
- `docker compose logs` содержит `Отклонён пользователь …`

## Безопасное подключение

Бот **сам** устанавливает TLS-сессию к `https://api.telegram.org` (long polling). Отдельный сертификат на VPS не используется.

Что это значит на практике:

- Не публиковать порты контейнера и не ставить бот за открытым HTTP
- Firewall: входящие только SSH
- Секреты только в `.env` с правами `600`, не в Dockerfile и не в git
- Docker без `--privileged` и без проброса `/var/run/docker.sock`
- `security_opt: no-new-privileges` уже в `docker-compose.yml`
- Whitelist — основной контроль: знание username бота недостаточно
- Прокси-URL с логином/паролем — тоже секрет

**Резюме.** Файл не пишется на диск, текст держится в RAM (FSM) и уходит в OpenRouter для разбора. Это персональные данные: не добавляйте в whitelist посторонних и не включайте полный текст резюме в логи (сейчас пишутся только роль, опыт, число навыков, город).

**Cookies.** `PLAYWRIGHT_STORAGE_STATE` — общая сессия браузера на весь процесс. Не сохраняйте туда личный аккаунт, если ботом пользуются несколько людей.

## Антибот

Если сайты режут выдачу:

1. `APIFY_API_TOKEN` — LinkedIn/Indeed и fallback при CAPTCHA
2. Резидентный прокси: `PLAYWRIGHT_PROXY` или ротация `PLAYWRIGHT_PROXIES`
3. Ручной логин и `scripts/save_storage_state.py` (см. выше)
4. Увеличить `SCRAPER_DOMAIN_INTERVAL` / `PLAYWRIGHT_SLOW_MO`

## Troubleshooting

| Симптом | Что проверить |
|---|---|
| Бот не стартует, ошибка `ALLOWED_USER_IDS` | Переменная задана, ID — числа через запятую |
| Бот молчит / «Нет доступа» | `/whoami` у уже добавленного пользователя или ID из ответа; перезапуск после правки `.env` |
| `OPENROUTER_API_KEY` 401 | Новый ключ на openrouter.ai, без кавычек и пробелов |
| Chrome channel недоступен | В Docker это нормально: compose ставит `PLAYWRIGHT_USE_CHROME=false`. Локально установите Chrome или тоже выставьте `false` |
| `exec …/tini` или `…/docker-init: operation not permitted` | `no-new-privileges` + setuid/caps у init. В актуальном compose `init: false`, в образе нет tini. Пересоберите: `docker compose up -d --build` |
| Chromium падает, `page crashed` | Мало `/dev/shm`: в compose уже `shm_size: 1gb`. Не хватает RAM — держите `SEARCH_MAX_CONCURRENT=1` |
| Permission denied на `bot.log` | `chown 1000:1000 logs` |
| Пустая выдача / блокировки | Прокси, Apify, cookies; смотрите `docker compose logs` |
| Apify «лимит исчерпан» | Счётчик на **жизнь процесса**, не на один поиск. Рестарт контейнера сбрасывает. Либо поднимите `APIFY_MAX_RUNS_PER_SESSION` / `APIFY_MAX_RUNS_PER_DAY` |
| После рестарта пропал экран настройки | Ожидаемо: MemoryStorage |
| В логах или чате торчат внутренние traceback | Не должно: пользователю уходит общее сообщение, traceback только в лог |

## Лицензия и ответственность

Скрейпинг может нарушать правила конкретных сайтов. Используйте бота для личного/ограниченного круга, соблюдайте ToS площадок и законы о персональных данных.
