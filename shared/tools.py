"""
Інструменти для Claude Agent SDK (tool_use pattern).

Кожен інструмент: JSON-схема (для Claude) + Python-обробник (handler).
Обробники використовують lazy import щоб уникнути circular imports.

Набори інструментів:
  FEES_TOOLS / FEES_HANDLERS         → Agent2
  CRITIC_TOOLS / CRITIC_HANDLERS     → Agent3
  GENERATOR_TOOLS / GENERATOR_HANDLERS → Agent4
  EXPERT_TOOLS / EXPERT_HANDLERS     → Agent5
"""
import re
import json
from shared.logger import get_logger

logger = get_logger(__name__)


# ===========================================================================
# JSON-схеми інструментів (tool definitions для Claude API)
# ===========================================================================

SEARCH_COURT_DECISIONS = {
    "name": "search_court_decisions",
    "description": (
        "Семантичний пошук судових рішень у базі даних ChromaDB. "
        "Використовуй для перевірки чи існує посилання на конкретну справу або практику. "
        "Повертає реальні рішення з бази або повідомлення що рішень не знайдено."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Пошуковий запит: номер справи, правова позиція або тема",
            },
            "category": {
                "type": "string",
                "description": "Категорія справи: civil, administrative, commercial (опційно)",
            },
            "top_k": {
                "type": "integer",
                "description": "Кількість результатів (1–10), за замовчуванням 5",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

GET_LEGAL_NORM = {
    "name": "get_legal_norm",
    "description": (
        "Отримати текст конкретної статті закону з внутрішньої бази правових текстів. "
        "Використовуй для точного цитування норми права перед включенням у документ."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "article": {
                "type": "string",
                "description": "Стаття, напр: 'ст.22 ЦК України', 'ч.1 ст.175 ЦПК'",
            },
            "code": {
                "type": "string",
                "description": "Кодекс для звуження пошуку: ЦПК, КАС, ГПК, ЦК, ЗК (опційно)",
            },
        },
        "required": ["article"],
    },
}

GET_PROCEDURAL_REQUIREMENTS = {
    "name": "get_procedural_requirements",
    "description": (
        "Отримати обов'язкові елементи процесуального документа за відповідним кодексом. "
        "Використовуй для перевірки чи всі вимоги враховані при генерації або критиці."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "description": "Тип документа: claim, appeal, cassation, objection, motion_security тощо",
            },
            "procedural_code": {
                "type": "string",
                "description": "Процесуальний кодекс: ЦПК, КАС, ГПК",
            },
        },
        "required": ["doc_type", "procedural_code"],
    },
}

VALIDATE_DOCUMENT_STRUCTURE = {
    "name": "validate_document_structure",
    "description": (
        "Перевірити наявність обов'язкових секцій у готовому документі: "
        "шапка (адресат суду), назва документа, вступ, обставини, "
        "правове обґрунтування, ПРОШУ СУД, перелік додатків, підпис. "
        "Використовуй перед фінальним аудитом як першу перевірку."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_text": {
                "type": "string",
                "description": "Повний текст документа для перевірки",
            },
            "doc_type": {
                "type": "string",
                "description": "Тип документа (опційно для специфічних правил)",
            },
        },
        "required": ["document_text"],
    },
}

GET_DOCUMENT_TEMPLATE_HINTS = {
    "name": "get_document_template_hints",
    "description": (
        "Отримати рекомендовану структуру та типові формулювання для конкретного типу документа. "
        "Використовуй якщо потрібно уточнити що саме має бути у певній секції."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "description": "Тип документа: claim, appeal, cassation, objection",
            },
            "section": {
                "type": "string",
                "description": "Секція: header, intro, facts, legal_basis, claims, attachments (опційно)",
            },
        },
        "required": ["doc_type"],
    },
}

GET_FEE_RATE = {
    "name": "get_fee_rate",
    "description": (
        "Отримати поточну ставку судового збору для типу позовних вимог відповідно до ЗСЗ. "
        "Використовуй якщо тип вимог неоднозначний або потрібно підтвердити розрахунок."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "claim_type": {
                "type": "string",
                "description": "Тип: property (майнові), non_property (немайнові), appeal, cassation, admin",
            },
            "claim_amount": {
                "type": "number",
                "description": "Ціна позову у грн (для майнових вимог)",
            },
        },
        "required": ["claim_type"],
    },
}


# ===========================================================================
# Python-обробники (handlers)
# ===========================================================================

def handle_search_court_decisions(
    query: str,
    category: str = None,
    top_k: int = 5,
) -> str:
    """Пошук у ChromaDB через DecisionStorage."""
    try:
        from agent1_collector.storage import DecisionStorage
        storage = DecisionStorage()
        filters = {"category": category} if category else None
        top_k = max(1, min(top_k, 10))
        decisions = storage.search_similar(query, filters=filters, top_k=top_k)
    except Exception as e:
        logger.warning(f"[tool:search_court_decisions] Помилка пошуку: {e}")
        return f"Не вдалося виконати пошук: {e}"

    if not decisions:
        return "Рішень за запитом не знайдено в базі даних."

    lines = [f"Знайдено {len(decisions)} рішень:"]
    for i, d in enumerate(decisions, 1):
        positions = "; ".join(d.legal_positions[:2]) if d.legal_positions else "—"
        laws = ", ".join(d.cited_laws[:3]) if d.cited_laws else "—"
        lines.append(
            f"\n{i}. №{d.registry_number} | {d.court_name} | {d.decision_date} | {d.result}"
            f"\n   Предмет: {d.subject[:150]}"
            f"\n   Правові позиції: {positions}"
            f"\n   Норми права: {laws}"
        )
    return "\n".join(lines)


def handle_get_legal_norm(article: str, code: str = None) -> str:
    """Пошук тексту норми у shared/legal_texts.py."""
    try:
        from shared.legal_texts import CPC_REQUIREMENTS_TEXT, LEGAL_QUALITY_STANDARDS, COURT_FEE_LAW_TEXT
        sources = [CPC_REQUIREMENTS_TEXT, LEGAL_QUALITY_STANDARDS, COURT_FEE_LAW_TEXT]
        combined = "\n".join(sources)
    except Exception as e:
        return f"Не вдалося завантажити базу правових текстів: {e}"

    # Нормалізуємо запит: шукаємо різні варіанти написання
    search_terms = [article.lower()]
    # Додаємо варіанти: "ст. 22" → "ст.22" і навпаки
    search_terms.append(article.lower().replace("ст.", "ст. ").replace("  ", " "))
    search_terms.append(article.lower().replace("ст. ", "ст."))

    matching_lines = []
    for line in combined.split("\n"):
        line_lower = line.lower()
        if any(term in line_lower for term in search_terms):
            if code is None or code.lower() in line_lower:
                matching_lines.append(line.strip())

    if not matching_lines:
        return f"Норму '{article}' не знайдено у внутрішній базі правових текстів."

    result = "\n".join(matching_lines[:15])
    return f"Знайдено для '{article}':\n{result}"


def handle_get_procedural_requirements(
    doc_type: str,
    procedural_code: str,
) -> str:
    """Вимоги до процесуального документа з legal_texts.py."""
    try:
        from shared.legal_texts import CPC_REQUIREMENTS_TEXT
        text = CPC_REQUIREMENTS_TEXT
    except Exception as e:
        return f"Не вдалося завантажити текст кодексу: {e}"

    code_upper = procedural_code.upper()
    doc_lower = doc_type.lower()

    # Маппінг типів документів на ключові слова для пошуку
    doc_keywords = {
        "claim": ["позов", "позовна заява", "ст.175", "ст.177"],
        "appeal": ["апеляц", "ст.356", "ст.357"],
        "cassation": ["касац", "ст.392", "ст.394"],
        "objection": ["відзив", "ст.178", "ст.179"],
        "motion_security": ["забезпечення позову", "ст.149"],
        "motion_restore_deadline": ["поновлення строку", "ст.119"],
        "motion_evidence": ["витребування доказів", "ст.84"],
        "motion_expert": ["експертиза", "ст.101"],
    }

    keywords = doc_keywords.get(doc_lower, [doc_lower])
    matching = []
    for line in text.split("\n"):
        line_lower = line.lower()
        if code_upper.lower() in line_lower and any(kw in line_lower for kw in keywords):
            matching.append(line.strip())

    # Fallback: тільки по типу документа
    if not matching:
        for line in text.split("\n"):
            if any(kw in line.lower() for kw in keywords):
                matching.append(line.strip())

    if not matching:
        return (
            f"Специфічних вимог для '{doc_type}' за кодексом '{procedural_code}' "
            f"не знайдено. Перевір назву типу документа або кодексу."
        )

    return f"Вимоги для '{doc_type}' ({procedural_code}):\n" + "\n".join(matching[:20])


def handle_validate_document_structure(
    document_text: str,
    doc_type: str = None,
) -> str:
    """Regex-перевірка структури документа без API."""
    checks = {
        "Шапка (адресат суду)": bool(
            re.search(r"до\s+.{3,50}\s*суду", document_text, re.IGNORECASE)
            or re.search(r"(районн|міськ|апеляційн|верховн).{0,30}суд", document_text, re.IGNORECASE)
        ),
        "Назва документа": bool(
            re.search(
                r"(позовна заява|апеляційна скарга|касаційна скарга|відзив|клопотання)",
                document_text,
                re.IGNORECASE,
            )
        ),
        "Сторони (Позивач/Відповідач)": bool(
            re.search(r"(позивач|апелянт|скаржник)\s*:", document_text, re.IGNORECASE)
            and re.search(r"відповідач\s*:", document_text, re.IGNORECASE)
        ),
        "Обставини справи": bool(
            re.search(r"(обставин|факти|хронологі|перебіг)", document_text, re.IGNORECASE)
        ),
        "Правове обґрунтування": bool(
            re.search(r"(відповідно до|згідно з|на підставі|ст\.\s*\d)", document_text, re.IGNORECASE)
        ),
        "Прохальна частина (ПРОШУ СУД)": bool(
            re.search(r"прошу суд", document_text, re.IGNORECASE)
        ),
        "Перелік додатків": bool(
            re.search(r"(додатки|додаток|до заяви додаю)", document_text, re.IGNORECASE)
        ),
        "Підпис та дата": bool(
            re.search(r"\d{1,2}[\.\-]\d{1,2}[\.\-]\d{2,4}", document_text)
        ),
    }

    found = [k for k, v in checks.items() if v]
    missing = [k for k, v in checks.items() if not v]

    result = {
        "found": found,
        "missing": missing,
        "completeness": f"{len(found)}/{len(checks)}",
        "is_complete": len(missing) == 0,
    }

    lines = [f"Перевірка структури документа ({result['completeness']}):"]
    lines.append("\nПрисутні секції:")
    lines.extend(f"  [OK] {s}" for s in found)
    if missing:
        lines.append("\nВідсутні секції:")
        lines.extend(f"  [!] {s}" for s in missing)
    else:
        lines.append("\nВсі обов'язкові секції присутні.")

    return "\n".join(lines)


def handle_get_document_template_hints(
    doc_type: str,
    section: str = None,
) -> str:
    """Статичні підказки структури документа."""
    templates = {
        "claim": {
            "header": "До [найменування суду]\nПозивач: [ПІБ], [адреса], [РНОКПП], [тел.]\nВідповідач: [ПІБ або назва], [адреса]",
            "intro": "Вступна частина: опис суті спору в 2-3 реченнях. Посилання на підставу звернення.",
            "facts": "Обставини справи у хронологічному порядку: дата → подія → наслідок. Кожна обставина → посилання на доказ.",
            "legal_basis": "Правове обґрунтування: стаття + частина + пункт + закон. Чому норма застосовується. Посилання на практику ВС.",
            "claims": "ПРОШУ СУД:\n1. Стягнути з відповідача...\n2. Судовий збір у розмірі [сума] грн покласти на відповідача.",
            "attachments": "Перелік додатків:\n1. Копія договору від [дата]\n2. Квитанція про сплату судового збору\n3. [інші документи]",
        },
        "appeal": {
            "header": "До [найменування апеляційного суду]\nАпелянт (Позивач): [ПІБ], [адреса]\nВідповідач: [ПІБ або назва]",
            "intro": "Вступ: рішення суду першої інстанції від [дата], справа №[номер], яким [суть рішення]. Не погоджуємося з рішенням з таких підстав.",
            "legal_basis": "Доводи апелянта: неправильне застосування норм матеріального права (ст.XX); неправильне встановлення обставин; невідповідність висновків суду фактичним обставинам.",
            "claims": "ПРОСИМО СУД:\n1. Апеляційну скаргу задовольнити.\n2. Рішення [суду] від [дата] скасувати.\n3. Ухвалити нове рішення, яким позов задовольнити.",
        },
        "cassation": {
            "header": "До Верховного Суду\nКасатор (Позивач): [ПІБ], [адреса]\nВідповідач: [ПІБ або назва]",
            "intro": "Касаційна скарга на рішення суду першої інстанції та постанову апеляційного суду. Підстава касації: неправильне застосування норм матеріального/процесуального права.",
            "legal_basis": "Доводи касатора: суди неправильно застосували [ст.XX]; правова позиція ВС у справах [номери]; чому висновки судів суперечать практиці ВС.",
        },
        "objection": {
            "header": "До [найменування суду]\nВідповідач: [ПІБ або назва], [адреса]\nПозивач: [ПІБ або назва]",
            "intro": "Відзив на позовну заяву по справі №[номер]. Відповідач заперечує проти задоволення позову з таких підстав.",
            "legal_basis": "Правові заперечення: позивач не довів [обставину]; норма права [ст.XX] не застосовується, тому що...; строк позовної давності спливо...",
            "claims": "ПРОСИМО СУД:\n1. У задоволенні позову відмовити повністю.\n2. Судові витрати покласти на позивача.",
        },
    }

    doc_data = templates.get(doc_type.lower(), {})
    if not doc_data:
        available = ", ".join(templates.keys())
        return f"Шаблон для '{doc_type}' не знайдено. Доступні типи: {available}."

    if section and section in doc_data:
        return f"Секція '{section}' для {doc_type}:\n\n{doc_data[section]}"

    lines = [f"Структура документа '{doc_type}':"]
    for sec, content in doc_data.items():
        lines.append(f"\n[{sec.upper()}]\n{content}")
    return "\n".join(lines)


def handle_get_fee_rate(claim_type: str, claim_amount: float = None) -> str:
    """Розрахунок ставки судового збору відповідно до ЗСЗ."""
    # Прожитковий мінімум для розрахунку (2024)
    LIVING_WAGE = 3028.0
    MIN_FEE = LIVING_WAGE * 0.4  # 0.4 прожиткового мінімуму = 1211.20 грн

    claim_lower = claim_type.lower()

    if "property" in claim_lower or "майнов" in claim_lower:
        if claim_amount is None:
            return (
                "Для майнових вимог потрібна ціна позову (claim_amount). "
                "Ставка: 1% від суми (фіз. особа) або 1.5% (юр. особа), "
                f"мінімум {MIN_FEE:.2f} грн (ст.4 ч.1 п.1 ЗСЗ)."
            )
        fee_physical = max(claim_amount * 0.01, MIN_FEE)
        fee_legal = max(claim_amount * 0.015, MIN_FEE)
        return (
            f"Майнові вимоги | Ціна позову: {claim_amount:,.2f} грн\n"
            f"  Фізична особа: {fee_physical:,.2f} грн (1%, мін {MIN_FEE:.2f} грн) — ст.4 ч.1 п.1а ЗСЗ\n"
            f"  Юридична особа: {fee_legal:,.2f} грн (1.5%, мін {MIN_FEE:.2f} грн) — ст.4 ч.1 п.1б ЗСЗ"
        )

    elif "non_property" in claim_lower or "немайнов" in claim_lower:
        fee = LIVING_WAGE * 0.4
        return (
            f"Немайнові вимоги: {fee:.2f} грн (0.4 прожиткового мінімуму) — ст.4 ч.1 п.2 ЗСЗ\n"
            f"  Прожитковий мінімум: {LIVING_WAGE:.2f} грн"
        )

    elif "appeal" in claim_lower or "апеляц" in claim_lower:
        fee_property = LIVING_WAGE * 1.1 if claim_amount is None else claim_amount * 0.011
        fee_non_property = LIVING_WAGE * 0.44
        return (
            f"Апеляційна скарга:\n"
            f"  Майнові вимоги: 110% від збору першої інстанції (~{fee_property:.2f} грн) — ст.4 ч.2 ЗСЗ\n"
            f"  Немайнові вимоги: {fee_non_property:.2f} грн — ст.4 ч.2 п.1 ЗСЗ"
        )

    elif "cassation" in claim_lower or "касац" in claim_lower:
        fee_property = LIVING_WAGE * 2.2 if claim_amount is None else claim_amount * 0.022
        fee_non_property = LIVING_WAGE * 0.88
        return (
            f"Касаційна скарга:\n"
            f"  Майнові вимоги: 220% від збору першої інстанції (~{fee_property:.2f} грн) — ст.4 ч.3 ЗСЗ\n"
            f"  Немайнові вимоги: {fee_non_property:.2f} грн — ст.4 ч.3 п.1 ЗСЗ"
        )

    elif "admin" in claim_lower or "адмін" in claim_lower:
        fee = LIVING_WAGE * 0.4
        return (
            f"Адміністративний позов: {fee:.2f} грн (0.4 прожиткового мінімуму) — ст.4 ч.1 п.7 ЗСЗ"
        )

    else:
        return (
            f"Тип вимог '{claim_type}' не розпізнано.\n"
            "Доступні типи: property, non_property, appeal, cassation, admin.\n"
            f"Базова ставка (немайнові): {MIN_FEE:.2f} грн."
        )


# ===========================================================================
# Набори інструментів для кожного агента
# ===========================================================================

# Agent2: Калькулятор зборів
FEES_TOOLS = [GET_FEE_RATE]
FEES_HANDLERS = {
    "get_fee_rate": handle_get_fee_rate,
}

# Agent3: Критик
CRITIC_TOOLS = [SEARCH_COURT_DECISIONS, GET_LEGAL_NORM, GET_PROCEDURAL_REQUIREMENTS]
CRITIC_HANDLERS = {
    "search_court_decisions": handle_search_court_decisions,
    "get_legal_norm": handle_get_legal_norm,
    "get_procedural_requirements": handle_get_procedural_requirements,
}

# Agent4: Генератор
GENERATOR_TOOLS = [GET_LEGAL_NORM, GET_PROCEDURAL_REQUIREMENTS, GET_DOCUMENT_TEMPLATE_HINTS]
GENERATOR_HANDLERS = {
    "get_legal_norm": handle_get_legal_norm,
    "get_procedural_requirements": handle_get_procedural_requirements,
    "get_document_template_hints": handle_get_document_template_hints,
}

# Agent5: Експерт
EXPERT_TOOLS = [
    SEARCH_COURT_DECISIONS,
    GET_LEGAL_NORM,
    GET_PROCEDURAL_REQUIREMENTS,
    VALIDATE_DOCUMENT_STRUCTURE,
]
EXPERT_HANDLERS = {
    "search_court_decisions": handle_search_court_decisions,
    "get_legal_norm": handle_get_legal_norm,
    "get_procedural_requirements": handle_get_procedural_requirements,
    "validate_document_structure": handle_validate_document_structure,
}
