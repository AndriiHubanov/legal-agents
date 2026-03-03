# Архітектура Legal Agents v2

> Детальний опис системи для розуміння поточного стану та планування покращень.

---

## Зміст

1. [Загальна схема пайплайну](#1-загальна-схема-пайплайну)
2. [Оркестратор](#2-оркестратор)
3. [Agent 1 — Архітектор позову](#3-agent-1--архітектор-позову)
4. [Agent 2 — Калькулятор судових зборів](#4-agent-2--калькулятор-судових-зборів)
5. [Agent 3 — Критик / Опонент](#5-agent-3--критик--опонент)
6. [Agent 4 — Генератор + Compliance](#6-agent-4--генератор--compliance)
7. [Agent 5 — Надпотужний Експерт](#7-agent-5--надпотужний-експерт)
8. [Prompt Caching — деталі реалізації](#8-prompt-caching--деталі-реалізації)
9. [Моделі даних](#9-моделі-даних)
10. [Потік даних між агентами](#10-потік-даних-між-агентами)
11. [Поточні обмеження та ідеї для покращення](#11-поточні-обмеження-та-ідеї-для-покращення)

---

## 1. Загальна схема пайплайну

```
Вхід: вільний текст ситуації / позову
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR                               │
│                    orchestrator/pipeline_v2.py                      │
│                    Управляє PipelineState                           │
│                    Максимум 3 зовнішні ітерації                     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────┐
         │                                          │
         ▼                                          │
   Agent 1 (Intake)                                 │
   IntakeResult                                     │
         │                                          │
         ▼                                          │
   Agent 2 (Fees)                                   │
   FeesCalculation                                  │ якщо critical_issues
         │                                          │ + questions_for_intake
         ▼                                          │
   [Collector + Analyzer]  ◄── опційно              │
   AnalysisReport                                   │
         │                                          │
         ▼                                          │
   Agent 3 (Critic)  ──── needs_revision? ──────────┘
         │ approved / needs_revision (score ≥ 5)
         ▼
   Agent 4 (Generator)
   GeneratedDocumentV2
         │
         ▼
   Agent 5 (Expert)
         │
         ├─── approved (score ≥ 7.5) ──► FINAL .docx ✓
         │
         ├─── revise_generator ──► Agent 4 (quick fix) ──► Agent 5
         │                                                    │
         │                                              approved? ──► .docx ✓
         │                                              else ──► next master_iter
         │
         └─── revise_critic ──────────────────────────► next master_iter
                                                        (Agent 3 отримує expert_feedback)
```

---

## 2. Оркестратор

**Файл:** [`orchestrator/pipeline_v2.py`](orchestrator/pipeline_v2.py)
**Стан:** [`orchestrator/state.py`](orchestrator/state.py)

### Що робить

Оркестратор — єдина точка входу, яка запускає всі агенти по черзі та управляє двома рівнями циклів.

**Зовнішній цикл** (до `max_iterations`, за замовчуванням 3):
- Запускає Agent 3, Agent 4, Agent 5
- Отримує рішення від Agent 5 і маршрутизує feedback

**Внутрішній цикл** (до `MAX_CRITIC_INNER_LOOPS = 2`):
- Запускається коли Agent 3 повертає `status = "critical_issues"` та є `questions_for_intake`
- Agent 1 переосмислює позицію з урахуванням питань
- Agent 2 перераховує збір якщо `needs_fee_recalculation = true`
- Agent 3 перевіряє знову

### PipelineState

```python
class PipelineState(BaseModel):
    session_id: str                           # унікальний ID сесії (8 символів)
    raw_situation: str                        # оригінальний текст ситуації
    case_parties: dict                        # plaintiff, defendant, court, lawyer
    case_number: str
    doc_type_hint: str

    # Результати агентів (накопичуються)
    intake_result: Optional[IntakeResult]
    fees_calculation: Optional[FeesCalculation]
    analysis_report: Optional[AnalysisReport]
    critic_reviews: list[CriticReview]        # всі ітерації
    generated_document: Optional[GeneratedDocumentV2]
    expert_reviews: list[ExpertReview]        # всі ітерації

    # Управління
    current_iteration: int
    max_iterations: int                       # за замовчуванням 3
    status: str                               # pending/running/completed/failed
    final_docx_path: Optional[str]
```

Стан можна зберегти у JSON (`--save-state`) та відновити для дебагінгу.

---

## 3. Agent 1 — Архітектор позову

**Файл:** [`agent1_intake/intake_agent.py`](agent1_intake/intake_agent.py)
**Вхід:** текст ситуації + опційні `questions_for_intake` від Agent 3
**Вихід:** `IntakeResult`

### Що робить

Перетворює неструктурований текст ситуації на формалізовану правову позицію. Виконує шість кроків аналізу:

1. **Тип справи** — визначає процесуальний кодекс (ЦПК / КАС / ГПК)
2. **Ідентифікація сторін** — позивач / відповідач / треті особи
3. **Хронологія фактів** — ключові події з датами
4. **Правові підстави** — конкретні статті законів
5. **Позовні вимоги** — що просити у суду
6. **Відсутня інформація** — що потрібно уточнити

### Кешований системний промт

Містить повну методологію структурування (>1024 токенів). Кешується при першому запиті.

### Feedback від Agent 3

При повторних ітераціях у `dynamic_system` додається:
```
Агент 3 виявив такі проблеми/питання:
  — Чи є підписаний договір з датою та підписами обох сторін?
  — Яка точна дата, коли відповідач мав виконати зобов'язання?
```

Agent 1 відповідає на ці питання, якщо відповідь є у тексті ситуації, або додає їх у `missing_info`.

### Вихідна модель

```python
class IntakeResult(BaseModel):
    raw_situation: str
    case_description: CaseDescription    # сумісно з V1 Analyzer
    identified_claims: list[str]          # "Стягнути 50 000 грн основного боргу"
    legal_basis_detected: list[str]       # "ст.1046 ЦК України — договір позики"
    missing_info: list[str]               # "Копія розписки або договору"
    case_type: str                        # позов / апеляція / заперечення
    procedural_code: str                  # ЦПК / КАС / ГПК
    recommended_doc_type: str             # appeal / claim / objection / ...
    plaintiff_type: str                   # фізична особа / юридична особа
    confidence: float                     # 0.0 – 1.0
    intake_iteration: int
```

---

## 4. Agent 2 — Калькулятор судових зборів

**Файл:** [`agent2_fees/fees_calculator.py`](agent2_fees/fees_calculator.py)
**Вхід:** `IntakeResult`
**Вихід:** `FeesCalculation`

### Що робить

Розраховує судовий збір відповідно до Закону України "Про судовий збір" та визначає правильну підсудність.

**Алгоритм:**
1. Визначає тип вимог: майнова / немайнова / змішана
2. Визначає тип позивача: фізична / юридична особа
3. Застосовує ст.4 ЗСЗ:
   - Майнова + фіз. особа → **1%** від ціни позову (мін. ~1211 грн, макс. ~15 140 грн у 2024 р.)
   - Майнова + юр. особа → **1,5%** (мін. ~3028 грн, макс. ~1 059 800 грн)
   - Немайнова + фіз. → ~**1211 грн**
   - Немайнова + юр. → ~**6056 грн**
   - Апеляція → **110%** від первісного збору
   - Касація → **200%** від первісного збору
4. Перевіряє пільги (ст.5 ЗСЗ): ветерани, інваліди I-II гр., трудові спори тощо
5. Визначає підсудність: районний/міський суд, окружний адмінсуд, господарський суд

### Кешований системний промт

Повний текст ЗСЗ + ключові статті ЦПК щодо підсудності (~5 000 токенів). Кешується.

### Вихідна модель

```python
class FeesCalculation(BaseModel):
    claim_type: str                   # майнова / немайнова / змішана
    claim_amount: Optional[float]     # null для немайнових
    plaintiff_type: str
    fee_rate_description: str         # "1% від ціни позову (ст.4 ч.1 п.1а ЗСЗ)"
    fee_amount: float                 # розмір збору у грн
    fee_basis: str                    # "ст.4 ч.1 п.1а ЗСЗ"
    exemptions_applicable: list[str]  # можливі пільги
    court_jurisdiction: str           # "Районний суд за місцем проживання відповідача"
    payment_requisites: str           # реквізити ДКС
    notes: list[str]                  # застереження
```

---

## 5. Agent 3 — Критик / Опонент

**Файл:** [`agent3_critic/critic_agent.py`](agent3_critic/critic_agent.py)
**Вхід:** `IntakeResult` + `FeesCalculation` + `AnalysisReport` (опційно) + `ExpertReview` (при повторах)
**Вихід:** `CriticReview`

### Що робить

Виступає "адвокатом диявола" — аналізує позицію з найгіршої точки зору. П'ять напрямків критики:

1. **Аналіз правової позиції**
   - Чи конкретні посилання на закони (стаття + частина + пункт)?
   - Чи правильна категорія справи та підсудність?
   - Чи дотримано строки позовної давності (загальний — 3 роки)?

2. **Прогнозування заперечень відповідача**
   - Процесуальні: непідсудність, пропуск строків, невиконання досудового порядку
   - Матеріально-правові: відсутність вини, форс-мажор, зустрічна вимога

3. **Перевірка доказової бази**
   - Кожна обставина підкріплена документом?
   - Чи потрібна експертиза?

4. **Перевірка розрахунку збитків**
   - Чи є формула? Чи враховано всі складові?

5. **Загальна оцінка та маршрутизація**
   - Score ≥ 8.0 → `"approved"` (передати до Agent 4)
   - Score 5.0–7.9 → `"needs_revision"` (передати з зауваженнями)
   - Score < 5.0 → `"critical_issues"` (повернути до Agent 1)

### Комунікація з Agent 1

```python
class CriticReview(BaseModel):
    status: str                           # approved / needs_revision / critical_issues
    overall_score: float                  # 0–10
    objections: list[str]                 # заперечення відповідача
    legal_risks: list[str]                # правові ризики
    missing_evidence: list[str]           # відсутні докази
    suggestions: list[str]                # пропозиції
    questions_for_intake: list[str]       # ← ЦЕ ПЕРЕДАЄТЬСЯ ДО AGENT 1
    needs_fee_recalculation: bool         # ← ЦЕ ПЕРЕДАЄТЬСЯ ДО AGENT 2
    critic_iteration: int
```

### Кешований системний промт

Роль критика + ключові статті ЦПК + стандарти якості юридичного документа (~8 000 токенів). Кешується.

---

## 6. Agent 4 — Генератор + Compliance

**Файли:** [`agent4_generator/generator_v2.py`](agent4_generator/generator_v2.py) + [`agent4_generator/compliance.py`](agent4_generator/compliance.py)
**Вхід:** `IntakeResult` + `FeesCalculation` + `CriticReview` + `AnalysisReport` + `ExpertReview` (при повторах)
**Вихід:** `GeneratedDocumentV2` (текст + compliance + шлях до .docx)

### GeneratorV2 — що генерує

Повний текст процесуального документа за структурою:

```
1. ШАПКА (реквізити)
   До [суд]
   Позивач: [ПІБ, адреса, РНОКПП/ЄДРПОУ]
   Відповідач: [ПІБ, адреса]

2. НАЗВА (по центру, жирний)
   ПОЗОВНА ЗАЯВА / АПЕЛЯЦІЙНА СКАРГА / ...

3. ВСТУПНА ЧАСТИНА
   Суть спору + підстава звернення

4. ОБСТАВИНИ СПРАВИ (хронологія)
   Кожна подія + посилання на доказ

5. ПРАВОВЕ ОБҐРУНТУВАННЯ
   Норма права → порушення → аргумент
   Спростування заперечень (з CriticReview)
   Посилання на практику ВС (з AnalysisReport)
   Формула збитків (якщо є)

6. ПРОХАЛЬНА ЧАСТИНА
   ПРОШУ СУД:
   1. [вимога 1]
   2. [вимога 2]
   3. Збір покласти на відповідача.

7. ПЕРЕЛІК ДОДАТКІВ

8. ПІДПИС ТА ДАТА
```

Місця без даних позначаються `[___]` для заповнення вручну.

### ComplianceChecker — що перевіряє

Після генерації тексту Claude окремим запитом перевіряє наявність обов'язкових елементів:

| Кодекс | Перевіряє |
|--------|-----------|
| **ЦПК ст.175** | найменування суду, ПІБ/РНОКПП позивача, адреса, ПІБ відповідача, зміст вимог, ціна позову, обставини, докази, підпис, перелік додатків |
| **КАС ст.160** | найменування суду, ім'я позивача, відповідача, вимоги, обставини, порушені норми права, підпис, додатки |
| **ГПК ст.162** | найменування суду, ЄДРПОУ позивача, відповідача, вимоги, обставини, правове обґрунтування, ціна позову, підпис, додатки |

**Вихід перевірки:**

```python
class ComplianceResult(BaseModel):
    procedural_code: str
    required_elements: dict[str, bool]   # кожен елемент: присутній/відсутній
    violations: list[str]                # "Відсутнє місце проживання позивача (ст.175 ЦПК)"
    warnings: list[str]                  # "Бажано додати номер телефону"
    is_compliant: bool
    compliance_score: float              # 0–10
```

### Збірка .docx

Використовує `agent3_writer/docx_builder.py` — форматування по стандарту судочинства України:
- Times New Roman 12pt
- Поля: ліво 3см, право 1.5см, верх/низ 2см
- Міжрядковий інтервал 1.5
- Першорядний відступ 1.25см
- Нумерація сторінок знизу по центру

---

## 7. Agent 5 — Надпотужний Експерт

**Файл:** [`agent5_expert/expert_reviewer.py`](agent5_expert/expert_reviewer.py)
**Вхід:** `GeneratedDocumentV2` + весь контекст справи + `list[ExpertReview]` (попередні раунди)
**Вихід:** `ExpertReview` з рішенням `approved` / `revise_generator` / `revise_critic`

### Чотири критерії оцінки

| Критерій | Що вимірює | Прохідний бал |
|----------|------------|---------------|
| `argumentation_score` | Переконливість правової позиції, правильність норм, посилання на практику, спростування заперечень | ≥ 7.5 |
| `compliance_score` | Формальні вимоги кодексу, правильний суд, правильний збір | ≥ 7.5 |
| `evidence_score` | Кожна обставина підтверджена доказом, клопотання про відсутні | ≥ 7.5 |
| `persuasiveness_score` | Логіка, структура, офіційний стиль, відсутність помилок | ≥ 7.5 |
| **total_score** | Середнє арифметичне | **≥ 8.0** |

### Рішення

```
approved         → всі критерії ≥ 7.5 і загальний ≥ 8.0
                   Документ готовий до збереження у .docx

revise_generator → слабкі формулювання або структура, але позиція правильна
                   Mandatory fixes передаються до Agent 4 як dynamic_system
                   Agent 4 генерує заново, Agent 5 перевіряє ще раз

revise_critic    → фундаментальні проблеми у правовій позиції:
                   неправильна норма права, неправильна підсудність,
                   пропуск строків, відсутність ключових доказів
                   Feedback передається до Agent 3 у наступній зовнішній ітерації
```

### Автокорекція рішення

Якщо Claude повертає `"decision": "revise_generator"`, але всі бали ≥ 7.5 і total ≥ 8.0 — код автоматично змінює рішення на `"approved"`:

```python
if all(s >= APPROVAL_THRESHOLD for s in [arg, comp, evid, pers]) and total >= 8.0:
    decision = "approved"
```

### Кешований системний промт

Роль Партнера з 25-річним досвідом + стандарти якості юридичного документа (~6 000 токенів). Кешується.

---

## 8. Prompt Caching — деталі реалізації

**Файл:** [`shared/claude_client.py`](shared/claude_client.py)

### Метод `analyze_cached()`

```python
def analyze_cached(
    self,
    cached_system: str,   # статичний блок — кешується
    dynamic_system: str,  # динамічний блок — НЕ кешується
    user_message: str,
    label: str = "",
) -> tuple[str, CacheStats]:
```

**API-запит:**
```python
system=[
    {
        "type": "text",
        "text": cached_system,
        "cache_control": {"type": "ephemeral"}   # ← маркер кешу
    },
    {
        "type": "text",
        "text": dynamic_system                    # ← без cache_control
    }
]
```

**CacheStats:**
```python
@dataclass
class CacheStats:
    cache_creation_tokens: int   # токени записані у кеш (перший запит)
    cache_read_tokens: int       # токени прочитані з кешу (наступні запити)
    input_tokens: int            # звичайні вхідні токени
    output_tokens: int           # вихідні токени
```

### Що кешується в кожному агенті

| Агент | Кешований блок | Розмір (~) |
|-------|----------------|------------|
| Agent 1 | Методологія структурування позиції | ~2 000 токенів |
| Agent 2 | Роль + повний текст ЗСЗ + ЦПК підсудність | ~4 000 токенів |
| Agent 3 | Роль критика + ЦПК вимоги + стандарти якості | ~6 000 токенів |
| Agent 4 (gen) | Роль генератора + структура документів + ЦПК/ЗСЗ | ~6 500 токенів |
| Agent 4 (compliance) | Роль редактора + ЦПК + КАС + ГПК тексти | ~7 000 токенів |
| Agent 5 | Роль Партнера + стандарти якості | ~4 500 токенів |

**Де зберігаються тексти кодексів:** [`shared/legal_texts.py`](shared/legal_texts.py)

Константи: `COURT_FEE_LAW_TEXT`, `CPC_REQUIREMENTS_TEXT`, `ADMIN_CODE_TEXT`, `COMMERCIAL_CODE_TEXT`, `ALL_PROCEDURAL_CODES`, `LEGAL_QUALITY_STANDARDS`

### Умови кешування (Anthropic API)

- Мінімальний розмір: **1024 токени**
- Тривалість кешу: **5 хвилин** (ephemeral)
- Підтримувані моделі: claude-3-5-sonnet, claude-3-7-sonnet, claude-sonnet-4+

---

## 9. Моделі даних

**Файл:** [`shared/models.py`](shared/models.py)

### Ланцюг передачі між агентами

```
CaseDescription (V1, сумісна)
    └── використовується в IntakeResult.case_description
        └── передається в agent1_collector/retriever для RAG-пошуку

IntakeResult
    └── Agent1 → Agent2, Agent3, Agent4, Agent5

FeesCalculation
    └── Agent2 → Agent3, Agent4, Agent5

AnalysisReport (V1, сумісна)
    └── agent2_analyst → Agent3, Agent4, Agent5

CriticReview
    └── Agent3 → Agent4, Agent5

ComplianceResult
    └── Всередині GeneratedDocumentV2

GeneratedDocumentV2
    └── Agent4 → Agent5

ExpertReview
    └── Agent5 → Agent3 (feedback), Agent4 (mandatory_fixes)

PipelineState
    └── Оркестратор (зберігає весь стан між агентами)
```

### Ключові поля для маршрутизації

```python
# Agent3 → Agent1
CriticReview.questions_for_intake: list[str]   # питання для уточнення
CriticReview.needs_fee_recalculation: bool     # перерахувати збір

# Agent3 → Agent4
CriticReview.objections: list[str]             # заперечення → спростувати
CriticReview.missing_evidence: list[str]       # відсутні докази
CriticReview.suggestions: list[str]            # пропозиції

# Agent5 → оркестратор
ExpertReview.decision: str                     # approved/revise_generator/revise_critic
ExpertReview.mandatory_fixes: list[str]        # обов'язкові правки

# Agent5 → Agent4
ExpertReview.mandatory_fixes передається у dynamic_system Agent4
```

---

## 10. Потік даних між агентами

### Крок 1. Вхідні дані (CLI)

```bash
python main.py smart-pipeline --situation "..." --plaintiff "..." ...
```

CLI формує `PipelineState` та передає в `run_pipeline()`.

### Крок 2. Зовнішній цикл (оркестратор)

```python
for master_iter in range(state.max_iterations):  # 0, 1, 2
    # 1. Agent 3 (завжди)
    critic_review, _ = agent3.review(intake, fees, analysis, expert_feedback)

    # 2. Внутрішній цикл якщо потрібно
    if critic_review.status == "critical_issues" and master_iter < 2:
        intake = agent1.process(raw_situation, questions=critic_review.questions_for_intake)
        if critic_review.needs_fee_recalculation:
            fees = agent2.calculate(intake)
        critic_review = agent3.review(intake, fees, analysis)

    # 3. Agent 4 + Agent 5
    generated = agent4.generate(intake, fees, critic_review, analysis, expert_feedback)
    expert_review = agent5.review(generated, intake, fees, analysis)

    if expert_review.decision == "approved":
        break
    elif expert_review.decision == "revise_generator":
        # Швидка ревізія Agent 4 → Agent 5 без нового циклу
        generated = agent4.generate(..., expert_feedback=expert_review)
        expert_review2 = agent5.review(generated, ...)
        if expert_review2.decision == "approved":
            break
    # revise_critic → наступна зовнішня ітерація (Agent 3 отримає expert_feedback)
```

### Крок 3. Збереження .docx

```python
docx_path = agent4.build_docx(generated, case_parties, case_number, lawyer_name)
state.final_docx_path = docx_path
```

---

## 11. Поточні обмеження та ідеї для покращення

### Обмеження поточної системи

#### A. Агент 1 (Intake)
- **Не задає питань користувачу** — якщо у тексті ситуації відсутня ключова інформація (наприклад, дата договору), агент просто записує це у `missing_info`, а не запитує. Усі уточнення симулює AI, а не реальний користувач.
- **Немає пам'яті між сесіями** — кожен запуск починається з нуля.

#### B. Агент 2 (Fees)
- **Ставки ЗСЗ захардкоджені у промті** — розмір прожиткового мінімуму (3028 грн у 2024 р.) зашито у текст. При зміні законодавства потрібно оновлювати `shared/legal_texts.py` вручну.
- **Не генерує реальні реквізити ДКС** — вказує лише загальну форму IBAN UA без конкретного рахунку суду.

#### C. Агент 3 (Critic)
- **Немає пам'яті попередніх справ** — не порівнює з попередніми схожими позиціями.
- **Не перевіряє актуальність норм права** — може посилатися на статті, які були змінені або втратили чинність.

#### D. Агент 4 (Generator)
- **Compliance checker — окремий API-запит** — це додаткові токени та час. Можна об'єднати в один запит.
- **Не зберігає проміжні версії** — якщо Agent 5 відхилив, попередній текст не зберігається для порівняння.
- **Клопотання** — обмежена кількість типів (`motion_*`). Немає шаблонів для зустрічних позовів, заяв про зміну предмету позову тощо.

#### E. Агент 5 (Expert)
- **Оцінки суб'єктивні** — Claude може нестабільно оцінювати один і той самий документ. Немає тест-сету для калібрування.
- **Не перевіряє реальність посилань** — якщо Agent 4 вигадав справу ВС (чого за інструкцією не мав), Agent 5 може цього не помітити.

#### F. Загальні
- **Немає тестів** — жодних unit/integration тестів.
- **Немає валідації якості на реальних справах** — невідомо, який відсоток документів суди реально приймають.
- **Один модель для всіх агентів** — усі 5 агентів використовують один і той самий `claude-sonnet-4`. Для Agent 1/2 достатньо Haiku, для Agent 5 потрібен Opus.

### Ідеї для покращення (пріоритет)

#### Висока пріоритетність
1. **Інтерактивний режим для Agent 1** — замість AI-симуляції запитань, реально питати у користувача через CLI (`click.prompt`) або чат-інтерфейс.
2. **Різні моделі для різних агентів** — Agent 1/2 → Haiku (дешевше), Agent 5 → Opus (краще).
3. **Тест-сет** — набір 10–20 реальних справ з відомим результатом для оцінки якості.

#### Середня пріоритетність
4. **Динамічне підтягування ЗСЗ** — парсити актуальні ставки з zakon.rada.gov.ua.
5. **Валідація посилань на практику** — перед генерацією перевіряти, чи існує номер справи в ChromaDB.
6. **Об'єднати generation + compliance** в один запит (зберегти токени).
7. **Версіонування документа** — зберігати всі проміжні версії для дифу між ітераціями.

#### Нижча пріоритетність
8. **Веб-інтерфейс** — FastAPI + simple UI замість CLI.
9. **Async пайплайн** — паралельний запуск Agent 1 і Agent 2 (незалежні).
10. **Розширення типів документів** — зустрічний позов, заява про зміну предмету, мирова угода.
11. **Інтеграція з реєстром** — автоматична перевірка статусу справи після подачі.

---

*Документ актуальний станом на версію `a5ed492` (2025-03-03).*
