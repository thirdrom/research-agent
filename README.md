# Research Agent

Telegram-бот для поиска научных статей с оформлением по ГОСТ 7.0.5-2008.

## Быстрый старт

```bash
# 1. Установить зависимости
pip3 install -r requirements.txt

# 2. Создать .env и вставить ключ
echo "DEEPSEEK_API_KEY=your_github_token" > .env

# 3. Проверить DeepSeek
python3 agent.py --test

# 4. Запустить pipeline (CLI)
python3 agent.py --query "federated learning" --goal "найти методы после 2020"

# 5. Запустить сервер для n8n
python3 agent.py --serve
```

## Пример входных данных

```
Query:  "federated learning privacy"
Goal:   "найти методы защиты данных после 2020"
```

Или файл `examples/input.txt`, CSV `examples/input_links.csv`.

## Пример результата

См. `examples/output.md` — Markdown-отчёт с ГОСТ-списком, скорингом и синтезом.

## Описание

Агент решает проблему ручного поиска и оформления научных источников: исследователь тратит часы на просмотр баз данных, проверку PDF и форматирование по ГОСТ — агент делает это за секунды через Telegram-бот.

Pipeline состоит из пяти шагов: парсинг входа (запрос / файл / CSV / Markdown), нормализация (дедупликация по хэшу, обрезка длинных текстов), поиск через arXiv API, анализ через DeepSeek и сборка Markdown-отчёта с trace-логом.

LLM используется содержательно в трёх местах: оценка релевантности каждой статьи по шкале 0–10 с обоснованием, синтез противоречий и пробелов между источниками, форматирование ГОСТ-записей из разнородных метаданных. Всё остальное — детерминированный код: дедупликация, retry, fallback, фильтрация, сборка отчёта.

Основные ограничения: агент работает только с open-access статьями; arXiv покрывает преимущественно точные науки и CS; GitHub Models имеет rate limit ~10 req/min на бесплатном тарифе; ГОСТ-запись зависит от полноты метаданных arXiv.

Сложнее всего оказалось совместить rate limit arXiv и GitHub Models — оба давали 429 при частых запросах, что потребовало перехода на батч-вызовы LLM (один запрос на все статьи вместо N запросов).

Следующий шаг — заменить arXiv на OpenAlex API (250M+ статей, без rate limit, чистый JSON) и добавить RAG-слой для работы с загруженными пользователем PDF.

## Стек

- Python 3.9+ · Flask · python-dotenv · requests
- arXiv API (поиск статей, open access)
- DeepSeek V3 via GitHub Models (анализ, ГОСТ, синтез)
- n8n Cloud (Telegram триггер + HTTP транспорт)
- ngrok (публичный URL для локального сервера)

## n8n Workflow (4 ноды)

```
Telegram Trigger
    → HTTP Request POST /run (ngrok URL)
    → IF ok == true
        → Telegram Send report
        → Telegram Send error
```

HTTP Request body:
```json
{
  "query": "={{ $json.message.text }}",
  "goal":  "найти релевантные статьи по теме"
}
```

## Структура проекта

```
research-agent/
├── agent.py              # Весь pipeline + Flask сервер
├── requirements.txt
├── .env.example
├── examples/
│   ├── input.txt         # Пример запроса
│   ├── input_links.csv   # Пример CSV
│   └── output.md         # Пример отчёта
└── README.md
```
