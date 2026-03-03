# Мультиагентна система аналізу судової практики України

Автоматизована Python-система з трьох послідовних AI-агентів для роботи з судовими рішеннями.

## Архітектура

```
Агент 1 (Збирач)     →    Агент 2 (Аналітик)    →    Агент 3 (Процесуаліст)
reyestr.court.gov.ua      ChromaDB RAG + Claude        Claude + python-docx
       ↓                         ↓                            ↓
  CourtDecision             AnalysisReport             .docx документ
```

| Агент | Роль | Технології |
|-------|------|------------|
| Agent 1 | Збір рішень з реєстру судових рішень | Playwright, BeautifulSoup, ChromaDB |
| Agent 2 | RAG-пошук + аналіз практики | ChromaDB, Claude API, Pydantic |
| Agent 3 | Генерація процесуальних документів | Claude API, python-docx |

## Встановлення

### 1. Передумови

- Python 3.11+
- Ключ API Anthropic ([отримати тут](https://console.anthropic.com/))

### 2. Клонувати репозиторій

```bash
git clone https://github.com/YOUR_USERNAME/legal-agents.git
cd legal-agents
```

### 3. Створити віртуальне середовище

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 4. Встановити залежності

```bash
pip install -r requirements.txt
playwright install chromium
```

### 5. Налаштувати змінні середовища

```bash
cp .env.example .env
```

Відредагувати `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
CHROMA_DB_PATH=./data/chroma_db
RAW_DATA_PATH=./data/raw_decisions
OUTPUT_PATH=./data/output_documents
REPORTS_PATH=./data/analysis_reports
SCRAPE_DELAY_SECONDS=3
MAX_DECISIONS_PER_RUN=100
HEADLESS_BROWSER=true
CLAUDE_MODEL=claude-sonnet-4-20250514
MAX_TOKENS=4096
```

### 6. Перевірити встановлення

```bash
python main.py stats
```

---

## Використання

### Агент 1 — Збір рішень

```bash
python main.py collect \
  --category civil \
  --date-from 2023-01-01 \
  --date-to 2024-12-31 \
  --keywords "оренда,земля,розірвання" \
  --court-level appeal \
  --max 100
```

**Параметри:**
- `--category`: `civil` | `admin` | `commercial` | `criminal` | `labor`
- `--date-from` / `--date-to`: формат `YYYY-MM-DD`
- `--keywords`: слова через кому
- `--court-level`: `first` | `appeal` | `cassation`
- `--region`: назва регіону (наприклад, `Київська`)
- `--no-claude`: не використовувати Claude для витягу позицій (швидше)

---

### Агент 2 — Аналіз практики

Підготувати файл `case.json`:

```json
{
  "category": "civil",
  "subject": "Розірвання договору оренди земельної ділянки",
  "key_facts": "Орендодавець вимагає дострокового розірвання договору через несплату орендної плати протягом 3 місяців",
  "desired_outcome": "Відмова у задоволенні позову про розірвання договору",
  "court_level": "appeal",
  "opposing_arguments": "Несплата орендних платежів протягом 3 місяців є підставою для розірвання"
}
```

```bash
python main.py analyze --case-file case.json --top-k 20
```

**Вихід:** Markdown-звіт та JSON у `data/analysis_reports/`

---

### Агент 3 — Генерація документа

```bash
python main.py generate \
  --analysis-file data/analysis_reports/report_20240101_120000_Розірвання.json \
  --doc-type appeal \
  --case-number "757/12345/24-ц" \
  --plaintiff "Іваненко Іван Іванович" \
  --defendant "ТОВ 'Агро-Захід'" \
  --court "Київський апеляційний суд" \
  --lawyer "Адвокат Петренко П.П."
```

**Типи документів:**
- `appeal` — апеляційна скарга
- `cassation` — касаційна скарга
- `objection` — відзив на позов/скаргу
- `motion_security` — клопотання про забезпечення позову
- `motion_restore_deadline` — клопотання про поновлення строку
- `motion_evidence` — клопотання про витребування доказів
- `motion_expert` — клопотання про призначення експертизи
- `motion_adjournment` — клопотання про відкладення

**Вихід:** `.docx` файл у `data/output_documents/`

---

### Повний пайплайн

```bash
python main.py pipeline \
  --case-file case.json \
  --doc-type appeal \
  --case-number "757/12345/24-ц" \
  --plaintiff "Іваненко І.І." \
  --defendant "ТОВ 'Агро-Захід'" \
  --court "Київський апеляційний суд"
```

Прапор `--collect` додатково запускає збір нових рішень перед аналізом.

---

### Статистика бази даних

```bash
python main.py stats
```

---

## Структура проєкту

```
legal-agents/
├── main.py                     # CLI-інтерфейс
├── requirements.txt
├── .env.example
│
├── shared/                     # Спільні компоненти
│   ├── config.py               # Налаштування (env змінні)
│   ├── models.py               # Pydantic моделі даних
│   ├── logger.py               # Rich логування
│   └── claude_client.py        # Обгортка Anthropic API
│
├── agent1_collector/           # Агент 1: Збір рішень
│   ├── scraper.py              # Playwright скрапер
│   ├── parser.py               # Парсинг HTML
│   ├── storage.py              # ChromaDB + JSON
│   └── filters.py              # Фільтри пошуку
│
├── agent2_analyst/             # Агент 2: Аналітик
│   ├── retriever.py            # RAG-пошук
│   ├── ranker.py               # Ранжування
│   ├── analyzer.py             # Claude аналіз
│   └── report.py               # Форматування звіту
│
├── agent3_writer/              # Агент 3: Процесуаліст
│   ├── generator.py            # Claude генерація тексту
│   ├── docx_builder.py         # python-docx збірка
│   └── templates/              # Промпти по типах документів
│       ├── appeal.py
│       ├── cassation.py
│       ├── objection.py
│       └── motion.py
│
└── data/                       # Дані (не в git)
    ├── chroma_db/              # Векторна БД
    ├── raw_decisions/          # JSON рішень
    ├── analysis_reports/       # Звіти Агента 2
    └── output_documents/       # Готові .docx
```

---

## Формати даних

### Вхідний файл справи (case.json)

| Поле | Тип | Опис |
|------|-----|------|
| `category` | `civil\|admin\|commercial\|criminal\|labor` | Категорія справи |
| `subject` | string | Предмет спору |
| `key_facts` | string | Ключові факти |
| `desired_outcome` | string | Бажаний результат |
| `court_level` | `first\|appeal\|cassation` | Рівень суду |
| `opposing_arguments` | string (опційно) | Аргументи іншої сторони |

### CourtDecision (внутрішня модель)

| Поле | Тип | Опис |
|------|-----|------|
| `id` | string | Унікальний ID |
| `registry_number` | string | Номер справи |
| `court_name` | string | Назва суду |
| `decision_date` | date | Дата рішення |
| `category` | string | Категорія |
| `result` | string | Результат рішення |
| `legal_positions` | list[string] | Витягнуті правові позиції |
| `url` | string | Джерело |

---

## Обмеження та важливі примітки

### Rate limiting (reyestr.court.gov.ua)
- Мінімальна затримка між запитами: **3 секунди**
- При отриманні 429/503 — exponential backoff
- Рекомендовано не більше 100 рішень за сесію

### Якість генерації документів
- Система посилається **тільки** на рішення з бази даних
- Номери справ у документах беруться виключно з реальних зібраних даних
- Чим більше рішень у базі — тим кращий аналіз

### Вартість API
- Один аналіз (Агент 2): ~5–15 тис. вхідних токенів + ~2 тис. вихідних
- Генерація документа (Агент 3): ~8–20 тис. вхідних токенів + ~4 тис. вихідних

---

## Попередження

> **ВАЖЛИВО:** Усі згенеровані документи є попередніми чернетками та **обов'язково потребують перевірки та редагування кваліфікованим юристом** перед поданням до суду.
>
> Система не надає юридичних консультацій. Результати аналізу мають інформаційний характер.

---

## Стек технологій

| Компонент | Технологія | Версія |
|-----------|-----------|--------|
| AI | Anthropic Claude | claude-sonnet-4 |
| Векторна БД | ChromaDB | ≥0.5 |
| Веб-скрапінг | Playwright | ≥1.44 |
| Документи | python-docx | ≥1.1 |
| Валідація | Pydantic v2 | ≥2.0 |
| CLI | Click | ≥8.1 |
| Логування | Rich | ≥13.0 |

---

## Ліцензія

MIT License — для некомерційного та дослідницького використання.
