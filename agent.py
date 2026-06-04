"""
Research Agent — pipeline for scientific article search.
Usage (CLI):    python agent.py --query "federated learning" --goal "найти методы"
Usage (server): python agent.py --serve
Usage (test):   python agent.py --test
"""

import os, sys, json, hashlib, argparse, textwrap, time, xml.etree.ElementTree as ET
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL     = "https://api.groq.com/openai/v1/chat/completions"
DEEPSEEK_MODEL   = "llama-3.3-70b-versatile"
MIN_SCORE        = 2
MAX_QUERY_LEN    = 200
MAX_ITEM_LEN     = 500

# ── TRACE ────────────────────────────────────────────────────────────────────

_trace = []

def trace_reset():
    global _trace
    _trace = []

def log(step, status, **kwargs):
    entry = {"step": step, "status": status, **kwargs}
    _trace.append(entry)
    details = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"  [{status.upper():5}] {step}: {details}", file=sys.stderr)

def get_trace():
    return list(_trace)

# ── STEP 1: INPUT PARSER ─────────────────────────────────────────────────────

def parse_input(query=None, filepath=None):
    if not query and not filepath:
        log("input_parse", "error", reason="no input provided")
        return {"type": "empty", "items": []}

    if query:
        query = query.strip()
        if not query:
            log("input_parse", "error", reason="empty query string")
            return {"type": "empty", "items": []}
        if len(query) > MAX_QUERY_LEN:
            log("input_parse", "warn", reason="query truncated to 200 chars")
            query = query[:MAX_QUERY_LEN]
        log("input_parse", "ok", format="query", length=len(query))
        return {"type": "query", "items": [query]}

    if not os.path.exists(filepath):
        log("input_parse", "error", reason=f"file not found: {filepath}")
        return {"type": "empty", "items": []}

    content = open(filepath, encoding="utf-8").read().strip()
    if not content:
        log("input_parse", "error", reason="empty file")
        return {"type": "empty", "items": []}

    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else "txt"

    if ext == "csv":
        lines = [l.split(",")[0].strip().strip('"') for l in content.splitlines()[1:] if l.strip()]
        log("input_parse", "ok", format="csv", count=len(lines))
        return {"type": "urls", "items": lines}

    if ext in ("md", "txt"):
        items = [l.strip() for l in content.splitlines() if l.strip()]
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

# ── STEP 2: NORMALIZER ───────────────────────────────────────────────────────

def normalize(parsed):
    seen, result, duplicates = set(), [], 0
    for item in parsed.get("items", []):
        item = item.strip()
        if not item:
            continue
        key = hashlib.md5(item.lower().encode()).hexdigest()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(item[:MAX_ITEM_LEN])
    log("normalize", "ok", items_in=len(parsed.get("items", [])),
        duplicates_removed=duplicates, items_out=len(result))
    return result

# ── STEP 3: SEARCH ───────────────────────────────────────────────────────────

def search_openalex(query, limit=3):
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={
                "search":   query,
                "per_page": limit,
                "select":   "title,authorships,publication_year,doi,open_access,abstract_inverted_index,cited_by_count,primary_location",
                "mailto":   "agent@research.local",
            },
            timeout=20
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        articles = []
        for w in results:
            inv = w.get("abstract_inverted_index") or {}
            abstract = ""
            if inv:
                word_pos = [(pos, wd) for wd, positions in inv.items() for pos in positions]
                abstract = " ".join(wd for _, wd in sorted(word_pos))[:400]
            oa      = w.get("open_access") or {}
            pdf_url = oa.get("oa_url") or ""
            authors = [{"name": a.get("author", {}).get("display_name", "")}
                       for a in (w.get("authorships") or [])[:3]]
            loc    = w.get("primary_location") or {}
            source = loc.get("source") or {}
            venue  = source.get("display_name", "")
            doi    = (w.get("doi") or "").replace("https://doi.org/", "")
            articles.append({
                "title":            (w.get("title") or "").strip(),
                "authors":          authors,
                "year":             str(w.get("publication_year") or ""),
                "externalIds":      {"DOI": doi},
                "openAccessPdf":    {"url": pdf_url} if pdf_url else {},
                "abstract":         abstract,
                "citationCount":    w.get("cited_by_count"),
                "publicationVenue": {"name": venue},
            })
        return articles
    except Exception as e:
        log("search_openalex", "warn", error=str(e))
        return []

def search_articles(query, limit=3):
    articles = search_openalex(query, limit)
    if articles:
        log("search", "ok", source="OpenAlex", found=len(articles))
    else:
        log("search", "warn", source="OpenAlex", found=0)
    return articles

# ── STEP 4: DEEPSEEK / GROQ ──────────────────────────────────────────────────

def call_deepseek(system_prompt, user_content, expect_json=True, retries=4):
    if not DEEPSEEK_API_KEY:
        log("llm", "error", reason="DEEPSEEK_API_KEY not set")
        return None

    for attempt in range(retries):
        try:
            r = requests.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={"model": DEEPSEEK_MODEL, "max_tokens": 2000,
                      "messages": [{"role": "system", "content": system_prompt},
                                   {"role": "user",   "content": user_content}]},
                timeout=45,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            if expect_json:
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
            return text
        except (json.JSONDecodeError, KeyError, requests.RequestException) as e:
            log("llm", "warn", attempt=attempt+1, error=str(e)[:120])
            if attempt < retries - 1:
                wait = 30 if "429" in str(e) else 3
                time.sleep(wait)

    log("llm", "error", reason="all retries exhausted")
    return None

def analyze_relevance(articles, goal):
    if not articles:
        return []
    abstracts = "\n\n".join(
        f"[{i+1}] {a.get('title','No title')} ({a.get('year','?')})\n{(a.get('abstract') or 'No abstract')[:400]}"
        for i, a in enumerate(articles)
    )
    system = textwrap.dedent("""
        Ты — научный ассистент. Оцени каждую статью по тематической релевантности цели.
        Оценивай только соответствие ТЕМЫ статьи и цели — не год публикации, не язык.
        Верни ТОЛЬКО валидный JSON-массив без markdown:
        [{"index": 1, "score": 8, "reason": "одно предложение на русском"}]
        score: целое число 0–10. reason: одно предложение на русском.
    """).strip()
    result = call_deepseek(system, f"Цель: {goal}\n\nСтатьи:\n{abstracts}")
    if not result:
        log("llm_score", "error", reason="no response")
        return []
    log("llm_score", "ok", scored=len(result))
    return result

def synthesize(articles, goal):
    if not articles:
        return "Недостаточно статей для синтеза."
    titles = "\n".join(f"[{i+1}] {a.get('title','')} ({a.get('year','?')})" for i, a in enumerate(articles))
    system = textwrap.dedent("""
        Ты — научный ассистент. Напиши синтез на русском (3–5 предложений).
        Структура: общие тезисы → противоречия → пробелы.
        Ссылайся на статьи как [1], [2] и т.д.
        Верни только текст синтеза, без заголовков.
    """).strip()
    result = call_deepseek(system, f"Цель: {goal}\n\nСтатьи:\n{titles}", expect_json=False)
    if not result:
        log("llm_synth", "error", reason="no response")
        return "Синтез недоступен."
    log("llm_synth", "ok", length=len(result))
    return result

def format_gost_fallback(article):
    authors = [a.get("name", "") for a in article.get("authors", [])[:3]]
    author_str = authors[0].split()[-1] if authors else "Unknown"
    doi   = (article.get("externalIds") or {}).get("DOI", "")
    year  = article.get("year", "")
    title = article.get("title", "")
    venue = (article.get("publicationVenue") or {}).get("name", "")
    ref   = f"DOI: {doi}" if doi else ""
    parts = [p for p in [author_str, title, venue] if p]
    base  = " // ".join(parts)
    return f"{base}. — {year}. — {ref}".strip(" —.")

def format_gost_batch(articles):
    metas = []
    for i, a in enumerate(articles):
        metas.append({
            "index":   i+1,
            "title":   a.get("title", ""),
            "authors": [x.get("name","") for x in a.get("authors",[])[:3]],
            "year":    a.get("year", ""),
            "venue":   (a.get("publicationVenue") or {}).get("name",""),
            "doi":     (a.get("externalIds") or {}).get("DOI",""),
        })
    system = textwrap.dedent("""
        Сформируй библиографические записи по ГОСТ 7.0.5-2008 для каждой статьи.
        Формат: Фамилия И.О. [и др.] Название // Журнал. — Год. — DOI.
        ВАЖНО: имена авторов оставляй в оригинальном написании (латиница).
        Используй только переданные поля. Если поля нет — пропусти.
        Верни ТОЛЬКО валидный JSON-массив без markdown:
        [{"index": 1, "gost": "строка записи"}, ...]
    """).strip()
    result = call_deepseek(system, json.dumps(metas, ensure_ascii=False))
    if not result:
        log("llm_gost", "warn", reason="fallback")
        return {i: format_gost_fallback(a) for i, a in enumerate(articles)}
    log("llm_gost", "ok", count=len(result))
    return {item["index"]-1: item.get("gost", format_gost_fallback(articles[item["index"]-1]))
            for item in result if "index" in item}

# ── STEP 5: VERIFIER ─────────────────────────────────────────────────────────

def verify(articles, scores):
    score_map = {s["index"]-1: s for s in (scores or [])}
    passed, filtered = [], []
    for i, article in enumerate(articles):
        if not article.get("title"):
            filtered.append((i+1, "нет названия"))
            continue
        sc     = score_map.get(i, {})
        score  = sc.get("score", 5)
        reason = sc.get("reason", "нет оценки")
        if score < MIN_SCORE:
            filtered.append((i+1, f"score={score}: {reason}"))
            continue
        article["_score"]  = score
        article["_reason"] = reason
        passed.append(article)
    log("verify", "ok" if passed else "warn", passed=len(passed), filtered=len(filtered))
    for idx, reason in filtered:
        log("verify_detail", "info", article=idx, reason=reason)
    return passed

# ── STEP 6: ARTIFACT BUILDER ─────────────────────────────────────────────────

def build_report(articles, synthesis, goal, query):
    """Telegram HTML-formatted message."""
    gost_map = format_gost_batch(articles)
    lines = [f"🔍 <b>Результаты по запросу:</b> {query}", ""]

    for i, a in enumerate(articles, 1):
        gost    = gost_map.get(i-1, format_gost_fallback(a))
        pdf_obj = a.get("openAccessPdf") or {}
        pdf_url = pdf_obj.get("url", "") if isinstance(pdf_obj, dict) else ""
        pdf_str = f'<a href="{pdf_url}">Открыть PDF</a>' if pdf_url else "нет open-access PDF"
        cite    = a.get("citationCount")
        cite_str = f"{cite} цит." if cite is not None else ""
        lines += [
            f"<b>[{i}] {a.get('title','Без названия')}</b>",
            f"📎 {gost}",
            f"⭐ Релевантность: {a.get('_score','?')}/10",
            f"💬 {a.get('_reason','')}",
            f"📊 Цитирований: {cite_str}" if cite_str else "",
            f"📄 {pdf_str}",
            "",
        ]

    if synthesis and synthesis != "Синтез недоступен.":
        lines += ["─" * 20, "<b>📝 Синтез</b>", "", synthesis]

    _save_full_report(articles, gost_map, synthesis, goal, query)
    return "\n".join(l for l in lines if l is not None)

def _save_full_report(articles, gost_map, synthesis, goal, query):
    """Save full Markdown report with trace to output.md."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Отчёт: {query}", f"**Цель:** {goal}", f"**Дата:** {now}",
             "", "---", f"## Найдено статей: {len(articles)}", ""]
    for i, a in enumerate(articles, 1):
        gost    = gost_map.get(i-1, format_gost_fallback(a))
        pdf_obj = a.get("openAccessPdf") or {}
        pdf_url = pdf_obj.get("url","") if isinstance(pdf_obj, dict) else ""
        lines += [
            f"### [{i}] {a.get('title','Без названия')}",
            f"**ГОСТ:** {gost}",
            f"**Релевантность:** {a.get('_score','?')}/10 — {a.get('_reason','')}",
            f"**Цитирования:** {a.get('citationCount') or 'нет данных'}",
            f"**PDF:** {'[PDF]('+pdf_url+')' if pdf_url else 'нет open-access PDF'}",
            "",
        ]
    lines += ["---", "## Синтез", "", synthesis or "—", "",
              "---", "## Лог выполнения", "",
              "| Шаг | Статус | Детали |", "|-----|--------|--------|"]
    for t in get_trace():
        details = ", ".join(f"{k}={v}" for k, v in t.items() if k not in ("step","status"))
        lines.append(f"| {t.get('step')} | {t.get('status')} | {details} |")
    try:
        open("output.md", "w", encoding="utf-8").write("\n".join(lines))
    except Exception:
        pass

# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def run_pipeline(query=None, filepath=None, goal="найти релевантные статьи по теме"):
    trace_reset()

    if query and len(query.split()) < 2:
        log("pipeline", "warn", reason="query may be too short")

    parsed = parse_input(query=query, filepath=filepath)
    if parsed["type"] in ("empty", "unknown"):
        msg = {"empty": "Пустой запрос. Напиши тему для поиска.",
               "unknown": "Формат не поддерживается. Используй txt, csv, md или json."
               }.get(parsed["type"], "Неизвестная ошибка.")
        return {"ok": False, "error": msg, "trace": get_trace()}

    items = normalize(parsed)
    if not items:
        return {"ok": False, "error": "После очистки не осталось данных.", "trace": get_trace()}

    articles = search_articles(items[0])
    if not articles:
        return {"ok": False, "error": "Статьи не найдены. Попробуй изменить запрос.", "trace": get_trace()}

    scores   = analyze_relevance(articles, goal)
    articles = verify(articles, scores)
    if not articles:
        return {"ok": False,
                "error": "Все найденные статьи нерелевантны цели. Попробуй уточнить запрос.",
                "trace": get_trace()}

    synthesis = synthesize(articles, goal)
    report    = build_report(articles, synthesis, goal, items[0])
    return {"ok": True, "report": report, "trace": get_trace()}

# ── TEST ──────────────────────────────────────────────────────────────────────

def test_deepseek():
    print("\nПроверка LLM API...")
    if not DEEPSEEK_API_KEY:
        print("  [FAIL] DEEPSEEK_API_KEY не задан в .env")
        return
    key_preview = DEEPSEEK_API_KEY[:8] + "..." + DEEPSEEK_API_KEY[-4:]
    print(f"  [INFO] Ключ: {key_preview}")
    print(f"  [INFO] URL: {DEEPSEEK_URL}")
    print(f"  [INFO] Модель: {DEEPSEEK_MODEL}")
    result = call_deepseek("Отвечай кратко.", "Скажи только: OK", expect_json=False)
    if result:
        print(f"  [OK] Ответ: {result.strip()}")
        print("\nAPI работает корректно.")
    else:
        print("  [FAIL] Нет ответа — проверь ключ.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def cli_main():
    parser = argparse.ArgumentParser(description="Research Agent CLI")
    parser.add_argument("--query",  help="Search query")
    parser.add_argument("--input",  help="Input file path")
    parser.add_argument("--goal",   default="найти релевантные статьи по теме")
    parser.add_argument("--out",    default="output.md")
    parser.add_argument("--serve",  action="store_true")
    parser.add_argument("--test",   action="store_true")
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
        print(f"\nГотово. Отчёт сохранён: output.md")
    else:
        print(f"\nОшибка: {result['error']}")
        sys.exit(1)

# ── SERVER ────────────────────────────────────────────────────────────────────

def run_server():
    try:
        from flask import Flask, request as freq, jsonify
    except ImportError:
        print("Flask не установлен: pip install flask")
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/run", methods=["POST"])
    def run_endpoint():
        data  = freq.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        goal  = (data.get("goal")  or "найти релевантные статьи по теме").strip()
        if not query:
            return jsonify({"ok": False, "error": "Поле query не может быть пустым."}), 400
        return jsonify(run_pipeline(query=query, goal=goal))

    port = int(os.getenv("PORT", 5000))
    print(f"Сервер запущен на http://0.0.0.0:{port}")
    print(f"  GET  /health")
    print(f"  POST /run")
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    cli_main()
