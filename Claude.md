# Legal Agents v2 — Опис системи для Claude

> Цей файл описує поточний стан проєкту: архітектуру, агентів, потік даних та Web UI.
> Використовується для швидкого введення в контекст при наступних сесіях розробки.

---

## Зміст

1. [Як запустити](#1-як-запустити)
2. [Структура проєкту](#2-структура-проєкту)
3. [Загальна схема пайплайну](#3-загальна-схема-пайплайну)
4. [Оркестратор і PipelineState](#4-оркестратор-і-pipelinestate)
5. [Agent 1 — Архітектор позову](#5-agent-1--архітектор-позову)
6. [Agent 2 — Калькулятор судових зборів](#6-agent-2--калькулятор-судових-зборів)
7. [Agent 3 — Критик / Опонент](#7-agent-3--критик--опонент)
8. [Agent 4 — Генератор + Compliance](#8-agent-4--генератор--compliance)
9. [Agent 5 — Надпотужний Експерт](#9-agent-5--надпотужний-експерт)
10. [Prompt Caching](#10-prompt-caching)
11. [Web UI та файловий ввід](#11-web-ui-та-файловий-ввід)
12. [V1-компоненти (допоміжні)](#12-v1-компоненти-допоміжні)
13. [Поточні обмеження та ідеї для покращення](#13-поточні-обмеження-та-ідеї-для-покращення)

---

## 1. Як запустити

### Web UI (рекомендовано)

```bash
pip install -r requirements.txt
python main.py server           # відкриє http://127.0.0.1:8000
python main.py server --port 8080  # або на іншому порті
```

### CLI

```bash
python main.py smart-pipeline \
  --situation "Орендар не платить 6 місяців. Хочу розірвати договір." \
  --plaintiff "Іваненко І.І." \
  --defendant "ТОВ Агро-Захід" \
  --max-iterations 2 \
  --no-analysis

# З файлом ситуації:
python main.py smart-pipeline --situation-file situation.txt --plaintiff "..."

# Наповнення ChromaDB (потрібно для --analysis):
python main.py collect --category civil --date-from 2023-01-01 --keywords "оренда,борг" --max 100
```

### Налаштування (.env)

```env
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-20250514
MAX_TOKENS=4096
CHROMA_DB_PATH=./data/chroma_db
OUTPUT_PATH=./data/output_documents
```

---

## 2. Структура проєкту

```
legal-agents/
├── main.py                    # CLI: collect, stats, smart-pipeline, server
├── server.py                  # FastAPI-сервер Web UI
├── process.html               # Веб-інтерфейс (один HTML-файл, vanilla JS)
├── requirements.txt
├── .env
│
├── shared/
│   ├── config.py              # Pydantic Settings з .env
│   ├── models.py              # Всі Pydantic-моделі V1 + V2
│   ├── logger.py              # Rich-логер (console = Console())
│   ├── claude_client.py       # ClaudeClient + analyze_cached() + CacheStats
│   ├── legal_texts.py         # Тексти ЦПК/КАС/ГПК/ЗСЗ для Prompt Caching
│   └── file_processor.py      # Витяг тексту з PDF/DOCX/TXT
│
├── agent1_intake/
│   └── intake_agent.py        # Agent 1: Архітектор позову
├── agent2_fees/
│   └── fees_calculator.py     # Agent 2: Калькулятор судових зборів
├── agent3_critic/
│   └── critic_agent.py        # Agent 3: Критик / Опонент
├── agent4_generator/
│   ├── generator_v2.py        # Agent 4: Генератор документа
│   └── compliance.py          # Agent 4: Перевірка відповідності ЦПК/КАС/ГПК
├── agent5_expert/
│   └── expert_reviewer.py     # Agent 5: Надпотужний Експерт
│
├── orchestrator/
│   ├── pipeline_v2.py         # Головний цикл з feedback loops
│   └── state.py               # create_state(), save_state(), load_state()
│
├── agent1_collector/          # V1: Playwright-скрапер reyestr.court.gov.ua
├── agent2_analyst/            # V1: RAG-аналіз ChromaDB → AnalysisReport
├── agent3_writer/
│   └── docx_builder.py        # V1 (використовується Agent 4): збирає .docx
│
└── data/
    ├── chroma_db/             # Векторна БД судових рішень
    ├── output_documents/      # Готові .docx + pipeline_states/
    └── analysis_reports/      # JSON-звіти RAG-аналізу
```

---

## 3. Загальна схема пайплайну

```
Вхід: текст ситуації + опційні файли (PDF/DOCX/TXT)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR (pipeline_v2.py)                   │
│                  Зовнішній цикл: до max_iterations (3)              │
└────────────────────────────┬────────────────────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │                                              │
         ▼                                              │ якщо critical_issues
   Agent 1 (Intake)   ←── supporting_docs[]            │ + questions_for_intake
   IntakeResult                                         │
         │                                              │
         ▼                                              │
   Agent 2 (Fees)     ←── якщо needs_fee_recalculation │
   FeesCalculation                                      │
         │                                              │
         ▼                                              │
   [V1 Collector + Analyzer] ← опційно                 │
   AnalysisReport                                       │
         │                                              │
         ▼                                              │
   Agent 3 (Critic) ──── critical_issues? ─────────────┘
         │ approved / needs_revision (score ≥ 5)
         ▼
   Agent 4 (Generator + Compliance)
   GeneratedDocumentV2
         │
         ▼
   Agent 5 (Expert)
         │
         ├── approved (всі критерії ≥ 7.5, total ≥ 8.0) → .docx ✓
         │
         ├── revise_generator → Agent 4 (quick fix) → Agent 5
         │                                              │
         │                              approved? → .docx ✓
         │                              else → next master_iter
         │
         └── revise_critic → next master_iter
                             (Agent 3 отримує expert_feedback)
```

---

## 4. Оркестратор і PipelineState

**Файли:** [`orchestrator/pipeline_v2.py`](orchestrator/pipeline_v2.py), [`orchestrator/state.py`](orchestrator/state.py)

### PipelineState — центральний об'єкт стану

```python
class PipelineState(BaseModel):
    session_id: str                # унікальний 8-символьний ID
    raw_situation: str             # оригінальний текст ситуації
    case_parties: dict             # plaintiff, plaintiff_details, defendant, court, lawyer
    case_number: str
    doc_type_hint: str             # appeal (за замовчуванням)
    supporting_docs: list[str]     # витягнутий текст завантажених файлів ← NEW

    # Результати агентів (накопичуються)
    intake_result: Optional[IntakeResult]
    fees_calculation: Optional[FeesCalculation]
    analysis_report: Optional[AnalysisReport]
    critic_reviews: list[CriticReview]
    generated_document: Optional[GeneratedDocumentV2]
    expert_reviews: list[ExpertReview]

    # Управління
    current_iteration: int
    max_iterations: int            # 3 за замовчуванням
    status: str                    # pending / running / completed / failed
    final_docx_path: Optional[str]
    error_message: Optional[str]
```

### Константи оркестратора

```python
CRITIC_MIN_SCORE_TO_GENERATE = 5.0   # мінімум для переходу до Agent 4
MAX_CRITIC_INNER_LOOPS = 2            # максимум внутрішніх циклів Agent3→Agent1
```

### Псевдокод зовнішнього циклу

```python
for master_iter in range(state.max_iterations):     # 0, 1, 2
    critic = agent3.review(intake, fees, analysis, expert_feedback)

    # Внутрішній цикл (до 2 разів)
    for _ in range(MAX_CRITIC_INNER_LOOPS):
        if critic.status != "critical_issues" or not critic.questions_for_intake:
            break
        intake = agent1.process(raw_situation, critic_questions=critic.questions_for_intake)
        if critic.needs_fee_recalculation:
            fees = agent2.calculate(intake)
        critic = agent3.review(intake, fees, analysis)

    if critic.overall_score < CRITIC_MIN_SCORE_TO_GENERATE:
        continue   # пропустити генерацію

    generated = agent4.generate(intake, fees, critic, analysis, expert_feedback)
    expert = agent5.review(generated, intake, fees, analysis)

    if expert.decision == "approved":
        break
    elif expert.decision == "revise_generator":
        generated = agent4.generate(..., expert_feedback=expert)
        expert2 = agent5.review(generated, ...)
        if expert2.decision == "approved":
            break
    # revise_critic → наступна зовнішня ітерація з expert_feedback
```

---

## 5. Agent 1 — Архітектор позову

**Файл:** [`agent1_intake/intake_agent.py`](agent1_intake/intake_agent.py)

**Вхід:** `raw_situation: str` + опційно `critic_questions`, `supporting_docs`, `iteration`
**Вихід:** `IntakeResult`

### Методологія (6 кроків)

1. Визначення типу справи → процесуальний кодекс (ЦПК / КАС / ГПК)
2. Ідентифікація сторін (позивач, відповідач, треті особи)
3. Хронологія фактів з датами та доказами
4. Правові підстави — конкретні статті законів
5. Позовні вимоги — що просити у суду
6. Відсутня інформація — `missing_info[]`

### Файловий ввід (NEW)

Якщо `supporting_docs` не порожній, у запит до Claude додається:
```
## ДОДАТКОВІ ДОКУМЕНТИ (завантажені користувачем)
### Документ 1
[contract.pdf]
Договір оренди від 01.01.2023 між...
```
Документи передаються ТІЛЬКИ на ітерації 0. На повторних ітераціях (revision) не передаються.

### Вихідна модель

```python
class IntakeResult(BaseModel):
    raw_situation: str
    case_description: CaseDescription    # для RAG-пошуку (сумісно з V1)
    identified_claims: list[str]
    legal_basis_detected: list[str]
    missing_info: list[str]
    case_type: str                       # позов / апеляція / заперечення
    procedural_code: str                 # ЦПК / КАС / ГПК
    recommended_doc_type: str            # appeal / claim / objection / motion_*
    plaintiff_type: str                  # фізична особа / юридична особа
    confidence: float                    # 0.0–1.0
    intake_iteration: int
```

---

## 6. Agent 2 — Калькулятор судових зборів

**Файл:** [`agent2_fees/fees_calculator.py`](agent2_fees/fees_calculator.py)

**Вхід:** `IntakeResult`
**Вихід:** `FeesCalculation`

### Що розраховує

| Тип вимоги | Позивач | Ставка |
|------------|---------|--------|
| Майнова | Фіз. особа | 1% (мін. ~1 211 грн) |
| Майнова | Юр. особа | 1,5% (мін. ~3 028 грн) |
| Немайнова | Фіз. особа | ~1 211 грн |
| Немайнова | Юр. особа | ~6 056 грн |
| Апеляція | — | 110% від первісного |
| Касація | — | 200% від первісного |

Перевіряє пільги ст.5 ЗСЗ, визначає підсудність (районний / окружний адмін / господарський суд).

### Кешований системний промт

Повний текст ЗСЗ (~2 000 токенів) + ключові статті ЦПК + стандарти якості.

---

## 7. Agent 3 — Критик / Опонент

**Файл:** [`agent3_critic/critic_agent.py`](agent3_critic/critic_agent.py)

**Вхід:** `IntakeResult` + `FeesCalculation` + `AnalysisReport` (опційно) + `ExpertReview` (при revise_critic)
**Вихід:** `CriticReview`

### П'ять напрямків критики

1. Перевірка правової позиції (норми, підсудність, строки)
2. Прогнозування заперечень відповідача
3. Оцінка доказової бази
4. Перевірка розрахунку збитків
5. Загальна оцінка (score 0–10) та маршрутизація

### Рішення

- `score ≥ 8.0` → `"approved"` — передати до Agent 4
- `5.0 ≤ score < 8.0` → `"needs_revision"` — передати з зауваженнями
- `score < 5.0` → `"critical_issues"` — повернути до Agent 1

### Комунікація з Agent 1 та Agent 2

```python
class CriticReview(BaseModel):
    questions_for_intake: list[str]   # → Agent 1 revision
    needs_fee_recalculation: bool     # → Agent 2 revision
    objections: list[str]             # → Agent 4 (спростувати)
    missing_evidence: list[str]       # → Agent 4 (клопотання)
    suggestions: list[str]
    overall_score: float
    status: str                       # approved / needs_revision / critical_issues
    critic_iteration: int
```

---

## 8. Agent 4 — Генератор + Compliance

**Файли:** [`agent4_generator/generator_v2.py`](agent4_generator/generator_v2.py), [`agent4_generator/compliance.py`](agent4_generator/compliance.py)

**Вхід:** весь контекст справи + `ExpertReview` (при revise_generator)
**Вихід:** `GeneratedDocumentV2` → `.docx`

### Структура документа

```
1. ШАПКА (суд, позивач+адреса+РНОКПП, відповідач+адреса)
2. НАЗВА документа (по центру, жирний)
3. ВСТУПНА ЧАСТИНА (суть спору)
4. ОБСТАВИНИ СПРАВИ (хронологія + докази)
5. ПРАВОВЕ ОБҐРУНТУВАННЯ (норми → порушення → аргумент;
   спростування заперечень з CriticReview;
   посилання на практику ВС з AnalysisReport)
6. ПРОХАЛЬНА ЧАСТИНА (ПРОШУ СУД:)
7. ПЕРЕЛІК ДОДАТКІВ
8. ПІДПИС ТА ДАТА
```

Місця без даних → `[___]` для заповнення вручну.

### Compliance перевірка (окремий запит)

Перевіряє наявність обов'язкових елементів за ЦПК ст.175 / КАС ст.160 / ГПК ст.162.

### Збірка .docx

Використовує `agent3_writer/docx_builder.py`:
Times New Roman 12pt, поля 3/1.5/2/2 см, інтервал 1.5, відступ 1.25 см, нумерація сторінок.

---

## 9. Agent 5 — Надпотужний Експерт

**Файл:** [`agent5_expert/expert_reviewer.py`](agent5_expert/expert_reviewer.py)

**Вхід:** `GeneratedDocumentV2` + весь контекст + `list[ExpertReview]` (попередні раунди)
**Вихід:** `ExpertReview`

### Чотири критерії (поріг ≥ 7.5 кожен, total ≥ 8.0)

| Критерій | Що вимірює |
|----------|-----------|
| `argumentation_score` | Переконливість, правильність норм, посилання на практику |
| `compliance_score` | Формальні вимоги кодексу, суд, збір |
| `evidence_score` | Кожна обставина ↔ доказ |
| `persuasiveness_score` | Логіка, структура, офіційний стиль |

### Рішення

- `approved` → всі ≥ 7.5 і total ≥ 8.0 → `.docx` готовий
- `revise_generator` → слабкі формулювання → Agent 4 (mandatory_fixes у dynamic_system)
- `revise_critic` → фундаментальні проблеми → Agent 3 у наступній ітерації

**Автокорекція:** якщо Claude повертає `revise_generator`, але всі бали ≥ 7.5 → код примусово змінює на `approved`.

---

## 10. Prompt Caching

**Метод:** `ClaudeClient.analyze_cached(cached_system, dynamic_system, user_message)`

```python
system = [
    {"type": "text", "text": cached_system, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": dynamic_system}   # без cache_control
]
```

### Що кешується

| Агент | Вміст кешованого блоку | ~Токенів |
|-------|------------------------|----------|
| Agent 1 | Роль + методологія структурування | 2 000 |
| Agent 2 | Роль + повний текст ЗСЗ + підсудність | 4 000 |
| Agent 3 | Роль критика + ЦПК + стандарти якості | 6 000 |
| Agent 4 (gen) | Роль + структура документів + ЦПК/ЗСЗ | 6 500 |
| Agent 4 (compliance) | Роль редактора + ЦПК + КАС + ГПК | 7 000 |
| Agent 5 | Роль Партнера + стандарти якості | 4 500 |

**Мінімум для кешування:** 1 024 токени. Кеш живе 5 хвилин (ephemeral).
**Економія при 3 ітераціях:** ~80–90% вхідних токенів.
**Тексти кодексів:** [`shared/legal_texts.py`](shared/legal_texts.py)

---

## 11. Web UI та файловий ввід

### Нові файли

| Файл | Призначення |
|------|------------|
| [`server.py`](server.py) | FastAPI-сервер (маршрути, сесії в пам'яті, SSE-логи) |
| [`process.html`](process.html) | Веб-інтерфейс (один файл, vanilla JS, без збірки) |
| [`shared/file_processor.py`](shared/file_processor.py) | Витяг тексту з PDF / DOCX / TXT, обрізка до 8 000 символів |

### API маршрути

| Метод | Шлях | Опис |
|-------|------|------|
| `GET` | `/` | Повертає `process.html` |
| `POST` | `/api/run` | Запускає пайплайн (multipart/form-data + файли) |
| `GET` | `/api/logs/{id}` | SSE-потік логів (`text/event-stream`) |
| `GET` | `/api/status/{id}` | Стан сесії: `running / completed / failed` |
| `GET` | `/api/download/{id}` | Завантажити `.docx` |

### Потік файлового вводу

```
process.html → POST /api/run (multipart + files[])
                     │
                     ▼
          shared/file_processor.py
          extract_text_from_bytes(filename, bytes)
          ├── .pdf  → pypdf.PdfReader
          ├── .docx → python-docx Document
          └── .txt  → decode UTF-8 / CP1251
          Обрізка: 8 000 символів / файл
          Формат:  "[назва_файлу]\n<текст>"
                     │
                     ▼
          create_state(..., supporting_docs=[...])
                     │
                     ▼
          pipeline_v2.py → agent1.process(
              raw_situation=...,
              supporting_docs=state.supporting_docs   ← тільки ітерація 0
          )
                     │
                     ▼
          Agent 1: _build_user_message() → секція
          ## ДОДАТКОВІ ДОКУМЕНТИ
          ### Документ 1
          [назва_файлу] ...текст...
                     │
                     ▼
          Збагачений CaseDescription → Agent 2 RAG ChromaDB
```

### Захоплення логів → браузер (SSE)

`server.py` додає `_QueueLogHandler` до `logging.getLogger()` перед запуском пайплайну.
Всі `logger.info(...)` з усіх агентів → черга → SSE → `EventSource` у браузері.
Термінал отримує Rich-вивід паралельно (він не відключається).

### Сесії

Зберігаються в пам'яті (`dict[str, SessionInfo]`). При перезапуску сервера — скидаються.

---

## 12. V1-компоненти (допоміжні)

Старі агенти збережені, бо їх використовує V2-пайплайн:

| Папка | Роль у V2 | Команда |
|-------|-----------|---------|
| `agent1_collector/` | Playwright-скрапер → ChromaDB | `python main.py collect` |
| `agent2_analyst/` | RAG-аналіз ChromaDB → `AnalysisReport` | Викликається з `pipeline_v2.py` |
| `agent3_writer/docx_builder.py` | Збирає `.docx` з тексту | Використовується Agent 4 |

`agent3_writer/generator.py` і `agent3_writer/templates/` — **видалені**.

### Покращення парсера (agent1_collector/parser.py)

Функція `extract_structured_positions(text, claude_client)` витягує з кожного рішення:
- `legal_positions` — 3-5 ключових правових позицій суду
- `cited_laws` — конкретні статті ("ст.22 ЦК України", "ст.156 ЗК України")
- `damage_amount` — сума збитків у грн (якщо є)
- `evidence_types` — типи доказів що згадуються у рішенні

Ці поля зберігаються у `CourtDecision` і покращують якість RAG-пошуку та AnalysisReport.

### Покращення аналізатора (agent2_analyst/analyzer.py)

`PracticeAnalyzer.analyze()` тепер повертає в `AnalysisReport`:
- `cited_laws` — зведений перелік застосованих норм права
- `damage_calculation_method` — методологія розрахунку збитків
- `required_evidence` — перелік необхідних доказів

Ці поля передаються в `agent4_generator/generator_v2.py` де вже використовуються (рядки 283–300).

---

## 13. Поточні обмеження та ідеї для покращення

### Обмеження

| Агент | Проблема |
|-------|---------|
| Agent 1 | Не задає питань реальному користувачу — лише записує у `missing_info` |
| Agent 1 | Без пам'яті між сесіями |
| Agent 2 | Ставки ЗСЗ захардкоджені у `shared/legal_texts.py` — потрібно оновлювати вручну |
| Agent 2 | Реквізити ДКС — лише загальна форма IBAN UA |
| Agent 3 | Не порівнює з попередніми справами |
| Agent 4 | Compliance — окремий запит (зайві токени і час) |
| Agent 4 | Не зберігає проміжні версії документів |
| Agent 5 | Оцінки суб'єктивні, немає тест-сету для калібрування |
| Загально | Немає unit/integration тестів |
| Загально | Одна модель для всіх агентів (`claude-sonnet-4`) |
| Web UI | Сесії зберігаються тільки в пам'яті |

### Ідеї покращення (пріоритет)

**Висока пріоритетність:**
1. Різні моделі: Agent 1/2 → Haiku, Agent 5 → Opus
2. Тест-сет з 10–20 реальних справ

**Середня пріоритетність:**
3. Динамічне підтягування ставок ЗСЗ з zakon.rada.gov.ua
4. Об'єднати generation + compliance в один запит
5. Зберігати проміжні версії документів для порівняння

**Нижча пріоритетність:**
6. Async пайплайн (Agent 1 і Agent 2 незалежні — можна паралельно)
7. Зустрічний позов, заява про зміну предмету позову, мирова угода
8. Сесії Web UI з відновленням між перезапусками сервера
9. Інтеграція з реєстром (перевірка статусу справи)

---

*Актуально: 2026-03-04 — enhanced parser (extract_structured_positions) + analyzer (cited_laws / damage_calc / evidence)*
