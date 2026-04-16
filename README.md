# HomeStock Universal

Локальное веб-приложение для домашних запасов: AI-камера, карточки товаров, список покупок, аналитика, категории, импорт/экспорт и PWA-режим.

## Что уже подготовлено

- `FastAPI` backend: `app.py`
- Один frontend-файл: `index.html`
- Railway deploy config: `railway.json`
- Procfile для совместимости: `Procfile`
- Python runtime: `runtime.txt`
- Список зависимостей: `requirements.txt`
- Пример переменных окружения: `.env.example`
- Защита секретов и локальных данных: `.gitignore`

## Локальный запуск на ПК

1. Установи зависимости:

```powershell
py -m pip install -r requirements.txt
```

2. Положи ключ Gemini локально одним из способов:

```powershell
$env:GEMINI_API_KEY="your_gemini_api_key"
```

или создай файл `gemini_api_key.txt` рядом с `app.py`.

3. Запусти сервер:

```powershell
.\start_server.bat
```

4. Открой приложение:

```text
http://127.0.0.1:8000
```

Для телефона в одной Wi-Fi сети открывай адрес ПК, например:

```text
http://192.168.0.147:8000
```

## Деплой на Railway

1. Создай GitHub-репозиторий и загрузи туда содержимое папки `homestock-universal`.
2. В Railway выбери `New Project` -> `Deploy from GitHub repo`.
3. В Railway добавь переменные окружения:

```text
GEMINI_API_KEY=your_gemini_api_key
SECRET_KEY=change-me-before-production
IMAGE_PROVIDER=pollinations
ALLOW_APPROX_TEXT_IMAGE=1
POLLINATIONS_IMAGE_MODEL=flux
POLLINATIONS_IMAGE_MODELS=flux,seedream,kontext,nanobanana
IMAGE_WIDTH=720
IMAGE_HEIGHT=520
POLLINATIONS_TIMEOUT=75
```

Если будет платный/рабочий ключ Pollinations, можно добавить:

```text
POLLINATIONS_API_KEY=your_pollinations_key
```

Railway сам использует команду из `railway.json`:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Проверка сервера:

```text
/health
```

## Важно про секреты

Не загружай в GitHub:

- `gemini_api_key.txt`
- `pollinations_api_key.txt`
- `.env`
- папку `data/`

Эти файлы уже добавлены в `.gitignore`. Если ключ Gemini уже где-то показывался публично, лучше перевыпустить его в Google AI Studio.

## Важно про данные

Сейчас данные хранятся локально в папке `data/`. Для полноценной версии с регистрацией, общим доступом и стабильным Railway-деплоем лучше следующим этапом подключить базу данных, например Railway Postgres.

## Следующий этап

Для версии с аккаунтами нужно добавить:

- регистрацию и вход
- пользователей и домашние группы
- PostgreSQL вместо локальных JSON-файлов
- синхронизацию телефона и ПК через один сервер
- роли доступа: владелец, член семьи, только просмотр
