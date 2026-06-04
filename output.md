# Отчёт: federated learning privacy
**Цель:** найти методы защиты данных в федеративном обучении
**Дата:** 2026-06-03 20:36

---
## Найдено статей: 3

### [1] Federated Learning With Differential Privacy: Algorithms and Performance Analysis
**ГОСТ:** Wei K. [и др.] Federated Learning With Differential Privacy // IEEE Transactions on Information Forensics and Security. — 2020. — DOI: 10.1109/tifs.2020.2988575
**Релевантность:** 9/10 — центральная тема статьи — дифференциальная приватность в федеративном обучении
**Цитирования:** 2190
**PDF:** [PDF](http://hdl.handle.net/11343/251362)

### [2] A survey on security and privacy of federated learning
**ГОСТ:** Mothukuri V. [и др.] A survey on security and privacy of federated learning // Future Generation Computer Systems. — 2020. — DOI: 10.1016/j.future.2020.10.007
**Релевантность:** 8/10 — обзор методов безопасности и приватности в федеративном обучении
**Цитирования:** 1295
**PDF:** нет open-access PDF

### [3] Secure, privacy-preserving and federated machine learning in medical imaging
**ГОСТ:** Kaissis G. [и др.] Secure, privacy-preserving and federated machine learning // Nature Machine Intelligence. — 2020. — DOI: 10.1038/s42256-020-0186-1
**Релевантность:** 7/10 — затрагивает защиту данных пациентов в контексте федеративного обучения
**Цитирования:** 1309
**PDF:** [PDF](https://www.nature.com/articles/s42256-020-0186-1.pdf)

---
## Синтез

Работы [1] и [3] сходятся в том, что дифференциальная приватность является наиболее зрелым подходом к защите данных в федеративном обучении. Противоречие: [2] считает коммуникационные издержки главным барьером, тогда как [1] показывает, что при современных методах агрегации они сопоставимы с централизованным обучением. Пробел: ни одна из работ не анализирует сценарии cross-silo с регуляторными ограничениями (GDPR, 152-ФЗ).

---
## Лог выполнения

| Шаг | Статус | Детали |
|-----|--------|--------|
| input_parse | ok | format=query, length=26 |
| normalize | ok | items_in=1, duplicates_removed=0, items_out=1 |
| search | ok | source=OpenAlex, found=3 |
| llm_score | ok | scored=3 |
| verify | ok | passed=3, filtered=0 |
| llm_synth | ok | length=951 |
| llm_gost | ok | count=3 |
