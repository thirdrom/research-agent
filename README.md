# Research Agent

Telegram-бот для поиска научных статей с оформлением по ГОСТ 7.0.5-2008.

Агент по запросу пользователя ищет релевантные статьи через OpenAlex API, оценивает их через LLM, строит синтез источников и возвращает отчёт прямо в Telegram. Работает только с open-access материалами.

## Стек

| Компонент | Технология |
|---|---|
| Интерфейс | Telegram Bot |
| Оркестрация | n8n Cloud |
| HTTP-сервер | Python + Flask |
| Туннель | ngrok |
| Поиск статей | OpenAlex API (250M+ работ, без ключа) |
| LLM-анализ | Groq API — Llama 3.3 70B |
| Язык | Python 3.9+ |

## Структура проекта

```
research-agent/
├── agent.py              # Весь pipeline + Flask-сервер
├── requirements.txt      # Зависимости
├── .env.example          # Шаблон переменных окружения
├── examples/
│   ├── input.txt         # Пример текстового запроса
│   ├── input_links.csv   # Пример CSV со ссылками
│   └── output.md         # Пример готового отчёта
└── README.md
```

## Быстрый старт

```bash
# 1. Установить зависимости
pip3 install -r requirements.txt

# 2. Создать .env
cp .env.example .env
# Вставить GROQ API ключ: console.groq.com

# 3. Проверить API
python3 agent.py --test

# 4. Запустить CLI
python3 agent.py --query "federated learning privacy" --goal "найти методы защиты данных"

# 5. Запустить сервер для n8n
python3 agent.py --serve
```

## Пример входных данных

```
Query: "federated learning privacy"
Goal:  "найти методы защиты данных в федеративном обучении"
```

Или файл `examples/input.txt`, CSV `examples/input_links.csv`.

## Пример результата

См. `examples/output.md` — Markdown-отчёт с ГОСТ-списком, скорингом и синтезом.

## Pipeline

```
Пользователь → Telegram
    ↓
n8n Trigger → HTTP POST /run → Flask (agent.py)
    ↓
Шаг 1: Парсинг входа       — формат: query / txt / csv / md / json
Шаг 2: Нормализация        — дедупликация по MD5, обрезка до 200 символов
Шаг 3: Поиск статей        — OpenAlex API, лимит 3 статьи
Шаг 4: LLM-анализ (Groq)   — скоринг 0-10, синтез, ГОСТ батчем
Шаг 5: Верификация         — фильтр score < 2, fallback ГОСТ без LLM
Шаг 6: Сборка отчёта       — Telegram HTML + output.md с trace-логом
    ↓
n8n → Telegram Send Message
```

## Реализация шагов

| Шаг | Тип |
|---|---|
| Парсинг, дедупликация, обрезка | Детерминированный код |
| Поиск статей | OpenAlex API |
| Скоринг релевантности с обоснованием | LLM (Groq) |
| Синтез противоречий и пробелов | LLM (Groq) |
| ГОСТ-форматирование из метаданных | LLM (Groq) |
| Фильтрация, retry, fallback, отчёт | Детерминированный код |

## Обработка ошибок

| Ситуация | Реакция |
|---|---|
| Пустой запрос | Сообщение пользователю |
| Неизвестный формат файла | Сообщение с поддерживаемыми форматами |
| Дубликаты | Удаляются, отмечаются в trace |
| Запрос > 200 символов | Обрезается с предупреждением |
| Статьи не найдены | Предложение изменить запрос |
| Все статьи нерелевантны (score < 2) | Предложение уточнить цель |
| Ошибка LLM / 429 | Retry ×4 с backoff, затем ГОСТ-fallback |

## n8n Workflow

4 ноды: Telegram Trigger → HTTP Request → IF → Telegram Send Message

HTTP Request:
```json
{
  "query": "={{ $json.message.text }}",
  "goal":  "найти релевантные статьи по теме"
}
```

Telegram Send Message:
- Parse Mode: HTML
- Text: `{{ $json.report ? $json.report.substring(0, 4000) : $json.error }}`

## Ограничения

- Только open-access статьи — закрытые PDF не скачиваются
- OpenAlex лучше работает с английскими запросами
- Groq бесплатный тариф — 30 req/min
- ngrok меняет URL при перезапуске (бесплатный тариф)
- 3 статьи за запрос — ограничение rate limit LLM
- Сессионная память не реализована 
