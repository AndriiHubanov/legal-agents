# Phase V2 — Claude Agent SDK Integration
## Стратегічний документ: навіщо, як і що змінилося

---

## 1. Поточна проблема (до Phase V2)

V2 пайплайн мав 5 агентів, feedback loops, Prompt Caching — але принципове обмеження:
кожен агент бачив тільки те, що Python підготував і передав у промпті.
Claude не міг самостійно запитати додаткову інформацію.

**Конкретні проблеми:**
- Agent3 (критик) рекомендував посилання на рішення ВС без перевірки їх наявності в БД → галюцинації
- Agent5 (експерт) аудитував документ без можливості звірити цитату з першоджерелом
- Agent4 (генератор) писав «відповідно до ст.22 ЦК» без доступу до точного тексту статті
- Зайві ітерації через виправлення неперевірених фактів

---

## 2. Що таке Claude Agent SDK і як він вирішує це

### Звичайний виклик (до Phase V2)
```
Python → [готуємо всі дані] → Claude API → [одна відповідь] → Python
```

### Agent SDK / Tool Use (Phase V2)
```
Python → Claude API (з переліком tools)
              ↓
         Claude: «треба перевірити БД перед відповіддю»
              ↓
         Claude повертає: tool_use { search_court_decisions("652/364/19") }
              ↓
Python виконує функцію → повертає реальні дані з ChromaDB
              ↓
Python → Claude API (з результатом)
              ↓
         Claude: «рішення є, ось точна позиція» → end_turn
              ↓
         Фінальна відповідь з перевіреними фактами
```

### Аналогія з Cursor
- **Cursor без агента** = ти показуєш код у чаті, отримуєш відповідь на основі того, що показав
- **Cursor Agent** = ти кажеш «виправ баг», Cursor сам читає файли, запускає тести, дивиться на помилки

**Різниця: агент збирає контекст самостійно** замість того щоб чекати поки ти все підготуєш.

---

## 3. Комбінований підхід: чому не замінюємо оркестратор

Python-оркестратор (pipeline_v2.py) залишається незмінним.
Agent SDK додається **всередині** кожного агента для збору даних.

```
Orchestrator (pipeline_v2.py) — контролює послідовність, loops, feedback, state
│
├── Agent1 → analyze_cached()   [1 API call, без tools]
├── Agent2 → run_agent()        [1-2 API calls, get_fee_rate]
├── Agent3 → run_agent()        [2-4 API calls, 3 tools]
├── Agent4 → run_agent()        [2-4 API calls, 3 tools]
└── Agent5 → run_agent()        [2-5 API calls, 4 tools]
```

Оркестратор — для **потоку управління**. Tool use — для **збору даних**.

---

## 4. Інструменти (6 штук у shared/tools.py)

| Інструмент | Агенти | Обробник |
|------------|--------|----------|
| `search_court_decisions` | Agent3, Agent5 | `DecisionStorage.search_similar()` |
| `get_legal_norm` | Agent3, Agent4, Agent5 | Пошук по `legal_texts.py` |
| `get_procedural_requirements` | Agent3, Agent4, Agent5 | Секційний пошук по `legal_texts.py` |
| `validate_document_structure` | Agent5 | Regex, без API |
| `get_document_template_hints` | Agent4 | Статичний dict, без API |
| `get_fee_rate` | Agent2 | Статична таблиця ЗСЗ, без API |

---

## 5. Економіка: чому відносно дешево

### Overhead від tools
Кожен tool_use = додатковий API-виклик (~1500-2000 нових токенів).

### Prompt Caching компенсує всередині agentic loop
```
Крок 1 (think → tool_use):    cache_write=4000 tokens → 1.25× (разово)
Крок 2 (tool_result → answer): cache_read=4000 tokens  → 0.10× (знижка 90%)
```
Всі кроки циклу відбуваються за секунди → кеш гарантовано спрацьовує.

### Порівняння вартості на один run

| Сценарій | API calls | Орієнтовна вартість |
|----------|-----------|---------------------|
| Run без tools (до Phase V2) | 5 | ~$0.05 |
| Run з tools (Phase V2) | 10-15 | ~$0.065-0.08 |
| Зайва ітерація через галюцинацію | +5 | +$0.05 |

**Якщо точніші агенти скорочують хоча б одну зайву ітерацію — Phase V2 дешевша.**

---

## 6. Змінені файли

| Файл | Зміна |
|------|-------|
| `requirements.txt` | `anthropic>=0.40.0` |
| `shared/tools.py` | **Новий файл**: 6 інструментів + обробники + набори для кожного агента |
| `shared/claude_client.py` | Новий метод `run_agent()`: agentic loop + Prompt Caching |
| `agent2_fees/fees_calculator.py` | `analyze_cached` → `run_agent` + FEES_TOOLS |
| `agent3_critic/critic_agent.py` | `analyze_cached` → `run_agent` + CRITIC_TOOLS + оновлений промпт |
| `agent4_generator/generator_v2.py` | `analyze_cached` → `run_agent` + GENERATOR_TOOLS |
| `agent5_expert/expert_reviewer.py` | `analyze_cached` → `run_agent` + EXPERT_TOOLS |

**Не змінювалися:** `agent1_intake/`, `orchestrator/pipeline_v2.py`, `shared/models.py`, ChromaDB, CLI.

---

## 7. Промпт для Claude Code

Для запуску наступних покращень або відтворення Phase V2 з нуля — скопіюй у Claude Code:

```
Реалізуй Phase V2 — інтеграцію Claude Agent SDK у проєкті legal-agents.
Робоча директорія: c:\Users\annae\OneDrive\Рабочий стол\Projects\Eduland\Law_Support\legal-agents\

Прочитай: docs/Phase_v2.md, shared/claude_client.py, shared/tools.py, requirements.txt

Phase V2 вже реалізовано. Переконайся що:
1. shared/tools.py містить 6 інструментів (FEES, CRITIC, GENERATOR, EXPERT наборів)
2. shared/claude_client.py має метод run_agent()
3. Agent2, 3, 4, 5 використовують run_agent() замість analyze_cached()

Для перевірки:
python -c "from shared.tools import CRITIC_TOOLS, EXPERT_TOOLS; print(len(CRITIC_TOOLS), len(EXPERT_TOOLS))"
python -c "from shared.claude_client import ClaudeClient; c = ClaudeClient(); print(hasattr(c, 'run_agent'))"
```

---

*Реалізовано: 2026-03-04*
