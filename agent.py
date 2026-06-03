"""
Research Agent — pipeline for scientific article search.
Usage (CLI):    python agent.py --query "federated learning" --goal "найти методы после 2020"
Usage (server): python agent.py --serve
"""

import os
import sys
import json
import hashlib
import argparse
import textwrap
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL     = "https://models.inference.ai.azure.com/chat/completions"
DEEPSEEK_MODEL   = "DeepSeek-V3-0324"

# ─────────────────────────────────────────────
# TRACE LOG
# ─────────────────────────────────────────────

_trace = []

def trace_reset():
    global _trace
    _trace = []

def log(step: str, status: str, **kwargs):
    entry = {"step": step, "status": status, **kwargs}
    _trace.append(entry)
    details = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"  [{status.upper():5}] {step}: {details}", file=sys.stderr)

def get_trace():
    return list(_trace)


# ─────────────────────────────────────────────
# STEP 1 — INPUT PARSER
# ─────────────────────────────────────────────

def parse_input(query = None, filepath = None) -> dict:
    """
    Returns: {"type": "query"|"urls"|"csv"|"text"|"empty"|"unknown", "items": [...]}
    """
    # Edge case: nothing provided
    if not query and not filepath:
        log("input_parse", "error", reason="no input provided")
        return {"type": "empty", "items": []}

    # Text query from Telegram
    if query:
        query = query.strip()
        if not query:
            log("input_parse", "error", reason="empty query string")
            return {"type": "empty", "items": []}
        log("input_parse", "ok", format="query", length=len(query))
        return {"type": "query", "items": [query]}

    # File input
    if not os.path.exists(filepath):
        log("input_parse", "error", reason=f"file not found: {filepath}")
        return {"type": "empty", "items": []}

    content = open(filepath, encoding="utf-8").read().strip()
    if not content:
        log("input_parse", "error", reason="empty file")
        return {"type": "empty", "items": []}

    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else "txt"

    if ext == "csv":
        lines = []
        for line in content.splitlines()[1:]:   # skip header
            cell = line.split(",")[0].strip().strip('"')
            if cell:
                lines.append(cell)
        log("input_parse", "ok", format="csv", count=len(lines))
        return {"type": "urls", "items": lines}

    if ext in ("md", "txt"):
        items = [l.strip() for l in content.splitlines() if l.strip()]
        if not items:
            log("input_parse", "error", reason="file has no non-empty lines")
            return {"type": "empty", "items": []}
        detected = "urls" if items[0].startswith("http") else "text"
        log("input_parse", "ok", format=ext, detected=detected, count=len(items))
        return {"type": detected, "items": items}

    if ext == "json":
        try:
            data = json.loads(content)
            items = data if isinstance(data, list) else [str(data)]
            log("input_parse", "ok", format="json", count=len(items))
            return {"type": "text", "items": [str(i) for i in items]}
        except json.JSONDecodeError:
            log("input_parse", "error", reason="invalid JSON")
            return {"type": "unknown", "items": []}

    log("input_parse", "error", reason=f"unsupported extension: {ext}")
    return {"type": "unknown", "items": []}


# ─────────────────────────────────────────────
# STEP 2 — NORMALIZER
# ─────────────────────────────────────────────

MAX_ITEM_LENGTH = 500

def normalize(parsed):
    """
    Deduplication by MD5 hash + trim long items.
    Returns clean list of strings.
    """
    seen = set()
    result = []
    duplicates = 0

    for item in parsed.get("items", []):
        item = item.strip()
        if not item:
            continue
        key = hashlib.md5(item.lower().encode()).hexdigest()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(item[:MAX_ITEM_LENGTH])

    log("normalize", "ok", items_in=len(parsed.get("items", [])),
        duplicates_removed=duplicates, items_out=len(result))
    return result


# ─────────────────────────────────────────────
# STEP 3 — ARTICLE SEARCH
# ─────────────────────────────────────────────

def search_arxiv(query, limit=8, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}", "max_results": limit},
                timeout=20
            )
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                log("search_arxiv", "warn", reason=f"rate limit, waiting {wait}s", attempt=attempt+1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            ns = "{http://www.w3.org/2005/Atom}"
            root = ET.fromstring(r.text)
            entries = []
            for e in root.findall(f"{ns}entry"):
                arxiv_id = e.findtext(f"{ns}id", "").split("/")[-1]
                pdf_url  = f"https://arxiv.org/pdf/{arxiv_id}"
                entries.append({
                    "title":           e.findtext(f"{ns}title", "").strip().replace("\n", " "),
                    "authors":         [{"name": a.findtext(f"{ns}name", "")}
                                        for a in e.findall(f"{ns}author")],
                    "year":            e.findtext(f"{ns}published", "")[:4],
                    "externalIds":     {"ArXiv": arxiv_id},
                    "openAccessPdf":   {"url": pdf_url},
                    "abstract":        e.findtext(f"{ns}summary", "").strip().replace("\n", " "),
                    "citationCount":   None,
                    "publicationVenue": {"name": "arXiv"},
                })
            return entries
        except Exception as e:
            log("search_arxiv", "warn", error=str(e), attempt=attempt+1)
            if attempt < retries - 1:
                time.sleep(3)
    return []


def search_articles(query, limit=8):
    """Search via arXiv API."""
    articles = search_arxiv(query, limit)
    if articles:
        log("search", "ok", source="arXiv", found=len(articles))
    else:
        log("search", "warn", source="arXiv", found=0)
    return articles


# ─────────────────────────────────────────────
# STEP 4 — DEEPSEEK ANALYSIS
# ─────────────────────────────────────────────

def call_deepseek(system_prompt: str, user_content: str,
                  expect_json: bool = True, retries: int = 2):
    """Wrapper around DeepSeek API with retry logic."""
    if not DEEPSEEK_API_KEY:
        log("llm", "error", reason="DEEPSEEK_API_KEY not set")
        return None

    for attempt in range(retries):
        try:
            r = requests.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      DEEPSEEK_MODEL,
                    "max_tokens": 2000,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_content},
                    ],
                },
                timeout=45,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()

            if expect_json:
                # Strip markdown code fences if present
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
            return text

        except (json.JSONDecodeError, KeyError, requests.RequestException) as e:
            log("llm", "warn", attempt=attempt + 1, error=str(e)[:120])
            if attempt < retries - 1:
                wait = 15 if "429" in str(e) else 3
                time.sleep(wait)

    log("llm", "error", reason="all retries exhausted")
    return None


def analyze_relevance(articles, goal):
    """Score each article 0-10 for relevance to goal."""
    if not articles:
        return []

    abstracts = "\n\n".join(
        f"[{i+1}] {a.get('title', 'No title')} ({a.get('year', '?')})\n"
        f"{(a.get('abstract') or 'No abstract')[:400]}"
        for i, a in enumerate(articles)
    )

    system = textwrap.dedent("""
        Ты — научный ассистент. Оцени каждую статью по релевантности цели исследования.
        Верни ТОЛЬКО валидный JSON-массив без markdown-блоков и пояснений:
        [{"index": 1, "score": 8, "reason": "одно предложение на русском"}]
        score: целое число от 0 до 10.
        reason: одно предложение на русском, объясняющее оценку.
    """).strip()

    result = call_deepseek(system, f"Цель: {goal}\n\nСтатьи:\n{abstracts}")
    if not result:
        log("llm_score", "error", reason="no response from model")
        return []

    log("llm_score", "ok", scored=len(result))
    return result


def synthesize(articles, goal):
    """Generate a synthesis paragraph across all passing articles."""
    if not articles:
        return "Недостаточно статей для синтеза."

    titles = "\n".join(
        f"[{i+1}] {a.get('title', '')} ({a.get('year', '?')})"
        for i, a in enumerate(articles)
    )

    system = textwrap.dedent("""
        Ты — научный ассистент. На основе списка статей напиши синтез на русском языке (3–5 предложений).
        Структура: общие тезисы → противоречия между авторами → пробелы в теме.
        Ссылайся на статьи как [1], [2] и т.д.
        Верни только текст синтеза, без заголовков и markdown.
    """).strip()

    result = call_deepseek(system, f"Цель: {goal}\n\nСтатьи:\n{titles}",
                           expect_json=False)
    if not result:
        log("llm_synth", "error", reason="no response")
        return "Синтез недоступен."

    log("llm_synth", "ok", length=len(result))
    return result


def format_gost(article: dict) -> str:
    """Format one article as GOST 7.0.5-2008 bibliographic entry via DeepSeek."""
    authors = [a.get("name", "") for a in article.get("authors", [])[:3]]
    venue   = (article.get("publicationVenue") or {}).get("name", "")
    meta = {
        "title":   article.get("title", ""),
        "authors": authors,
        "year":    article.get("year", ""),
        "venue":   venue,
        "doi":     (article.get("externalIds") or {}).get("DOI", ""),
        "arxiv":   (article.get("externalIds") or {}).get("ArXiv", ""),
    }

    system = textwrap.dedent("""
        Сформируй библиографическую запись по ГОСТ 7.0.5-2008.
        Формат: Фамилия И.О. [и др.] Название статьи // Журнал. — Год. — DOI или arXiv-ссылка.
        Используй только переданные поля. Если поля нет — пропусти его, ничего не придумывай.
        Верни только строку записи, без пояснений.
    """).strip()

    result = call_deepseek(system, json.dumps(meta, ensure_ascii=False),
                           expect_json=False)
    if not result:
        # Minimal fallback without LLM
        author_str = authors[0].split()[-1] if authors else "Unknown"
        return f"{author_str} {meta['title']} — {meta['year']}."

    return result.strip()


# ─────────────────────────────────────────────
# STEP 5 — VERIFIER
# ─────────────────────────────────────────────

MIN_SCORE = 3

def verify(articles, scores):
    """Filter articles by relevance score and attach score metadata."""
    score_map = {s["index"] - 1: s for s in (scores or [])}
    passed = []
    filtered = []

    for i, article in enumerate(articles):
        if not article.get("title"):
            filtered.append((i + 1, "нет названия"))
            continue

        sc     = score_map.get(i, {})
        score  = sc.get("score", 5)    # default 5 if scoring failed
        reason = sc.get("reason", "нет оценки")

        if score < MIN_SCORE:
            filtered.append((i + 1, f"score={score}: {reason}"))
            continue

        article["_score"]  = score
        article["_reason"] = reason
        passed.append(article)

    status = "ok" if passed else "warn"
    log("verify", status, passed=len(passed), filtered=len(filtered))
    if filtered:
        for idx, reason in filtered:
            log("verify_detail", "info", article=idx, reason=reason)

    return passed


# ─────────────────────────────────────────────
# STEP 6 — ARTIFACT BUILDER
# ─────────────────────────────────────────────

def build_report(articles, synthesis,
                 goal, query):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Отчёт агента: {query}",
        f"**Цель:** {goal}  ",
        f"**Дата:** {now}",
        "",
        "---",
        f"## Найдено статей: {len(articles)}",
        "",
    ]

    gost_map = format_gost_batch(articles)
    for i, a in enumerate(articles, 1):
        gost    = gost_map.get(i - 1, format_gost_fallback(a))
        pdf_obj = a.get("openAccessPdf") or {}
        pdf_url = pdf_obj.get("url", "") if isinstance(pdf_obj, dict) else ""
        pdf_str = f"[Открыть PDF]({pdf_url})" if pdf_url else "нет open-access PDF"
        cite    = a.get("citationCount")
        cite_str = f"{cite} цитирований" if cite is not None else "данных о цитированиях нет"

        lines += [
            f"### [{i}] {a.get('title', 'Без названия')}",
            f"**ГОСТ:** {gost}",
            f"**Релевантность:** {a.get('_score', '?')}/10 — {a.get('_reason', '')}",
            f"**Цитирования:** {cite_str}",
            f"**PDF:** {pdf_str}",
            "",
        ]

    lines += [
        "---",
        "## Синтез",
        "",
        synthesis,
        "",
        "---",
        "## Лог выполнения",
        "",
        "| Шаг | Статус | Детали |",
        "|-----|--------|--------|",
    ]

    for t in get_trace():
        step    = t.get("step", "")
        status  = t.get("status", "")
        details = ", ".join(f"{k}={v}" for k, v in t.items()
                            if k not in ("step", "status"))
        lines.append(f"| {step} | {status} | {details} |")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline(query=None,
                 filepath = None,
                 goal = "найти релевантные статьи по теме") -> dict:
    """
    Run the full pipeline. Returns:
      {"ok": True,  "report": "...", "trace": [...]}
      {"ok": False, "error": "...",  "trace": [...]}
    """
    trace_reset()

    # Step 1
    parsed = parse_input(query=query, filepath=filepath)
    if parsed["type"] in ("empty", "unknown"):
        msg = {
            "empty":   "Пустой запрос. Напиши тему для поиска.",
            "unknown": "Формат не поддерживается. Используй txt, csv, md или json.",
        }[parsed["type"]]
        return {"ok": False, "error": msg, "trace": get_trace()}

    # Step 2
    items = normalize(parsed)
    if not items:
        return {"ok": False,
                "error": "После очистки не осталось данных.",
                "trace": get_trace()}

    # Step 3
    search_query = items[0]
    articles = search_articles(search_query, limit=5)
    if not articles:
        return {"ok": False,
                "error": "Статьи не найдены. Попробуй изменить запрос.",
                "trace": get_trace()}

    # Step 4
    scores   = analyze_relevance(articles, goal)
    articles = verify(articles, scores)
    if not articles:
        return {"ok": False,
                "error": "Все найденные статьи нерелевантны цели. "
                         "Попробуй уточнить запрос или изменить цель.",
                "trace": get_trace()}

    synthesis = synthesize(articles, goal)

    # Steps 5-6
    report = build_report(articles, synthesis, goal, search_query)

    return {"ok": True, "report": report, "trace": get_trace()}


# ─────────────────────────────────────────────
# CLI MODE
# ─────────────────────────────────────────────

def test_deepseek():
    """Quick check: DeepSeek API key and connection."""
    print("\nПроверка DeepSeek API...")

    if not DEEPSEEK_API_KEY:
        print("  [FAIL] DEEPSEEK_API_KEY не задан в .env")
        return

    key_preview = DEEPSEEK_API_KEY[:8] + "..." + DEEPSEEK_API_KEY[-4:]
    print(f"  [INFO] Ключ найден: {key_preview}")
    print(f"  [INFO] URL: {DEEPSEEK_URL}")
    print(f"  [INFO] Модель: {DEEPSEEK_MODEL}")

    result = call_deepseek(
        system_prompt="Отвечай кратко.",
        user_content="Скажи только: OK",
        expect_json=False
    )

    if result:
        print(f"  [OK] Ответ модели: {result.strip()}")
        print("\nDeepSeek работает корректно.")
    else:
        print("  [FAIL] Модель не ответила — проверь ключ и баланс.")


def cli_main():
    parser = argparse.ArgumentParser(description="Research Agent CLI")
    parser.add_argument("--query",  help="Search query text")
    parser.add_argument("--input",  help="Path to input file (txt/csv/md/json)")
    parser.add_argument("--goal",   default="найти релевантные статьи по теме")
    parser.add_argument("--out",    default="output.md", help="Output file path")
    parser.add_argument("--serve",  action="store_true", help="Run as HTTP server")
    parser.add_argument("--test",   action="store_true", help="Test DeepSeek API connection")
    args = parser.parse_args()

    if args.serve:
        run_server()
        return

    if args.test:
        test_deepseek()
        return

    if not args.query and not args.input:
        print("Укажи --query 'тема' или --input файл.txt")
        sys.exit(1)

    print("Запускаю pipeline...", file=sys.stderr)
    result = run_pipeline(query=args.query, filepath=args.input, goal=args.goal)

    if result["ok"]:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result["report"])
        print(f"\nГотово. Отчёт сохранён: {args.out}")
    else:
        print(f"\nОшибка: {result['error']}")
        sys.exit(1)


# ─────────────────────────────────────────────
# SERVER MODE (for n8n)
# ─────────────────────────────────────────────

def run_server():
    try:
        from flask import Flask, request as freq, jsonify
    except ImportError:
        print("Flask не установлен. Запусти: pip install flask")
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/run", methods=["POST"])
    def run_endpoint():
        data    = freq.get_json(force=True, silent=True) or {}
        query   = (data.get("query") or "").strip()
        goal    = (data.get("goal")  or "найти релевантные статьи по теме").strip()

        if not query:
            return jsonify({"ok": False,
                            "error": "Поле query не может быть пустым."}), 400

        result = run_pipeline(query=query, goal=goal)
        return jsonify(result)

    port = int(os.getenv("PORT", 5000))
    print(f"Сервер запущен на http://0.0.0.0:{port}")
    print(f"  GET  /health — проверка работы")
    print(f"  POST /run    — запуск pipeline")
    app.run(host="0.0.0.0", port=port, debug=False)


# ─────────────────────────────────────────────

if __name__ == "__main__":
    cli_main()
