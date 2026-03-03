# Legal Agents v2 — 5-агентна AI-система для юридичного ринку України

> **[Лендінг](https://andriihubanov.github.io/legal-agents)** · **[Архітектура](ARCHITECTURE.md)**

Система п'яти спеціалізованих AI-агентів із зворотним зв'язком та Prompt Caching. Агенти спілкуються між собою: критик змушує архітектора переосмислити позицію, експерт повертає документ на доопрацювання — доки якість не відповідає найвищому стандарту.

---

## Архітектура (коротко)

```
Ситуація (вільний текст)
    │
    ▼
Agent 1 (Архітектор)  ──→  IntakeResult
    │
    ▼
Agent 2 (Калькулятор) ──→  FeesCalculation
    │
    ▼
[Collector + Analyzer] ──→  AnalysisReport (судова практика, опційно)
    │
    ▼  ◀──────────────────────────────────────────────┐
Agent 3 (Критик)       ──→  CriticReview              │ якщо revise_critic
    │ якщо critical_issues                             │
    ▼                                                  │
  Agent 1 (revision) → Agent 2 → Agent 3 (again)      │
    │ якщо approved / needs_revision                   │
    ▼                                                  │
Agent 4 (Генератор + Compliance) ──→ GeneratedDocumentV2
    │
    ▼
Agent 5 (Експерт) ──→ approved → .docx ✓
                  ──→ revise_generator → Agent 4 ──→ Agent 5
                  ──→ revise_critic    ────────────────────────┘
```

Детальний опис кожного агента, моделей даних та Prompt Caching → [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## Встановлення

### Передумови

- Python 3.11+
- Ключ API Anthropic ([console.anthropic.com](https://console.anthropic.com/))

### Кроки

```bash
git clone https://github.com/AndriiHubanov/legal-agents.git
cd legal-agents

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Відредагувати .env: вставити ANTHROPIC_API_KEY
```

`.env`:
```env
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-20250514
MAX_TOKENS=4096
CHROMA_DB_PATH=./data/chroma_db
RAW_DATA_PATH=./data/raw_decisions
OUTPUT_PATH=./data/output_documents
REPORTS_PATH=./data/analysis_reports
SCRAPE_DELAY_SECONDS=3
MAX_DECISIONS_PER_RUN=100
HEADLESS_BROWSER=true
```

Перевірити:
```bash
python main.py --help
```

---

## Використання

### Головна команда — 5-агентний пайплайн

```bash
python main.py smart-pipeline \
  --situation "Орендар не платить оренду 6 місяців. Хочу розірвати договір і стягнути борг 150 000 грн." \
  --plaintiff "Іваненко Іван Іванович" \
  --plaintiff-details "м. Київ, вул. Хрещатик 1, кв.1, РНОКПП 1234567890, тел. 0671234567" \
  --defendant "ТОВ Агро-Захід" \
  --defendant-details "м. Київ, вул. Польова 5, ЄДРПОУ 12345678" \
  --court "Господарський суд міста Києва" \
  --case-number "910/1234/24" \
  --max-iterations 3
```

**Параметри:**

| Параметр | Опис |
|----------|------|
| `--situation` `-s` | Текст ситуації (вільний текст) |
| `--situation-file` `-f` | Шлях до текстового файлу з ситуацією |
| `--plaintiff` | ПІБ або назва позивача |
| `--plaintiff-details` | Адреса, РНОКПП/ЄДРПОУ, пошта, тел. |
| `--defendant` | ПІБ або назва відповідача |
| `--defendant-details` | Адреса, РНОКПП/ЄДРПОУ |
| `--court` | Назва суду (якщо відома) |
| `--lawyer` | ПІБ адвоката/представника |
| `--case-number` `-n` | Номер справи |
| `--max-iterations` | Максимум ітерацій (за замовчуванням: 3) |
| `--analysis-file` `-a` | Існуючий JSON-звіт аналізу (пропустити збір) |
| `--no-analysis` | Не запускати аналіз практики |
| `--save-state` | Зберегти стан пайплайну у JSON |

**Вихід:** `.docx` у `data/output_documents/`

---

### Попереднє наповнення бази судових рішень

Перш ніж запускати `smart-pipeline --analysis-file`, потрібно наповнити ChromaDB:

```bash
python main.py collect \
  --category civil \
  --date-from 2022-01-01 \
  --keywords "оренда,земля,розірвання,борг" \
  --court-level appeal \
  --max 200
```

**Параметри collect:**

| Параметр | Значення |
|----------|----------|
| `--category` | `civil` / `admin` / `commercial` / `criminal` / `labor` |
| `--date-from` / `--date-to` | `YYYY-MM-DD` |
| `--keywords` | Слова через кому |
| `--court-level` | `first` / `appeal` / `cassation` |
| `--region` | Назва регіону (наприклад, `Київська`) |
| `--max` | Максимальна кількість рішень |
| `--no-claude` | Не використовувати Claude для парсингу (швидше) |

---

### Статистика бази

```bash
python main.py stats
```

---

## Структура проєкту

```
legal-agents/
├── main.py                       # CLI (команди: collect, stats, smart-pipeline)
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md               # Детальна технічна документація
│
├── shared/                       # Спільні компоненти
│   ├── config.py                 # Налаштування (.env)
│   ├── models.py                 # Pydantic моделі (V1 + V2)
│   ├── logger.py                 # Rich логування
│   ├── claude_client.py          # Claude API + analyze_cached()
│   └── legal_texts.py            # Тексти ЦПК/КАС/ГПК/ЗСЗ для Prompt Caching
│
├── agent1_intake/                # Agent 1: Архітектор позову
│   └── intake_agent.py
│
├── agent2_fees/                  # Agent 2: Калькулятор судових зборів
│   └── fees_calculator.py
│
├── agent3_critic/                # Agent 3: Критик / Опонент
│   └── critic_agent.py
│
├── agent4_generator/             # Agent 4: Генератор + Compliance
│   ├── generator_v2.py
│   └── compliance.py
│
├── agent5_expert/                # Agent 5: Надпотужний Експерт
│   └── expert_reviewer.py
│
├── orchestrator/                 # Оркестратор пайплайну
│   ├── pipeline_v2.py            # Головний цикл з feedback loops
│   └── state.py                  # PipelineState (збереження/відновлення)
│
├── agent1_collector/             # (V1) Скрапер реєстру судових рішень
│   ├── scraper.py
│   ├── parser.py
│   ├── storage.py                # ChromaDB + JSON
│   └── filters.py
│
├── agent2_analyst/               # (V1) RAG-аналіз практики
│   ├── retriever.py
│   ├── ranker.py
│   └── analyzer.py
│
├── agent3_writer/                # (V1) Збірка .docx
│   └── docx_builder.py
│
├── data/                         # Дані (не в git)
│   ├── chroma_db/                # Векторна БД рішень
│   ├── raw_decisions/            # JSON рішень
│   ├── analysis_reports/         # Звіти RAG-аналізу
│   └── output_documents/         # Готові .docx + pipeline states
│
└── docs/                         # GitHub Pages лендінг
    └── index.html
```

---

## Типові сценарії

### Сценарій 1: Перший запуск (без бази)

```bash
# Наповнити базу за категорією справи
python main.py collect --category civil --date-from 2023-01-01 --keywords "борг,стягнення" --max 100

# Запустити пайплайн без аналізу (якщо бажаєте тільки структурований документ)
python main.py smart-pipeline \
  --situation "Клієнт позичив 50 000 грн у фізичної особи під розписку, не повертає вже 2 роки." \
  --plaintiff "Петренко П.П." --defendant "Сидоренко С.С." \
  --no-analysis
```

### Сценарій 2: З аналізом практики (рекомендовано)

```bash
python main.py smart-pipeline \
  --situation-file situation.txt \
  --plaintiff "ТОВ Юридичний Консалтинг" \
  --defendant "Міністерство юстиції України" \
  --max-iterations 2 \
  --save-state
```

### Сценарій 3: Використати існуючий аналіз

```bash
python main.py smart-pipeline \
  --situation "..." \
  --analysis-file data/analysis_reports/report_20240601.json
```

---

## Prompt Caching — економія токенів

Кожен агент розбиває system prompt на два блоки:

```
cached_system  (кешується, ~3 000–8 000 токенів)
  └─ Роль агента + тексти кодексів (ЦПК, КАС, ГПК, ЗСЗ) + стандарти якості

dynamic_system (не кешується, ~200–400 токенів)
  └─ Контекст поточної ітерації + зауваження попередніх агентів
```

При 3 ітераціях пайплайну:
- **Ітерація 1:** `cache_write` — повна вартість
- **Ітерація 2–3:** `cache_read` — ~10% вартості вхідних токенів

Загальна економія на ітеративних перевірках: **~80–90%** вхідних токенів.

---

## Вартість API (орієнтовно)

| Операція | Вхідні токени | Вихідні токени |
|----------|---------------|----------------|
| Agent 1 (перша ітерація) | ~6 000 | ~800 |
| Agent 2 | ~9 000 | ~500 |
| Agent 3 (critic) | ~12 000 | ~1 000 |
| Agent 4 (generation) | ~15 000 | ~4 000 |
| Agent 4 (compliance) | ~10 000 | ~600 |
| Agent 5 (expert) | ~20 000 | ~1 500 |
| **Разом (1 ітерація)** | **~72 000** | **~8 400** |
| **Разом (3 ітерації, з кешем)** | **~90 000** | **~25 000** |

*Без Prompt Caching 3 ітерації коштували б ~216 000 вхідних токенів.*

---

## Обмеження

### reyestr.court.gov.ua
- Мінімальна затримка між запитами: **3 секунди**
- Рекомендовано не більше **100 рішень** за сесію
- При 429/503 — автоматичний exponential backoff

### Якість результату
- Система посилається **тільки** на рішення з ChromaDB
- Чим більше рішень у базі — тим точніший аналіз
- Прохідний бал для схвалення документа: **7.5/10** за кожним критерієм

### Важливо

> Усі згенеровані документи є попередніми чернетками та **обов'язково потребують перевірки кваліфікованим юристом** перед поданням до суду. Система не надає юридичних консультацій.

---

## Стек технологій

| Компонент | Технологія |
|-----------|-----------|
| AI | Anthropic Claude claude-sonnet-4 |
| Prompt Caching | Anthropic API `cache_control: ephemeral` |
| Векторна БД | ChromaDB |
| Веб-скрапінг | Playwright + BeautifulSoup |
| Документи | python-docx |
| Валідація | Pydantic v2 |
| CLI | Click + Rich |

---

## Ліцензія

MIT — для некомерційного та дослідницького використання.
