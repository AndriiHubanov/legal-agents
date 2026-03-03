"""
Ранжування судових рішень за релевантністю до конкретної справи
"""
import re
from shared.models import CaseDescription, CourtDecision

# Ваги компонентів оцінки
W_CATEGORY = 0.30
W_KEYWORDS = 0.35
W_COURT_LEVEL = 0.20
W_RESULT_MATCH = 0.15

CASSATION_KEYWORDS = ["верховний суд", "вс ", "касаційн", "велика палата"]
APPEAL_KEYWORDS = ["апеляційн"]


def score_relevance(case: CaseDescription, decision: CourtDecision) -> float:
    """
    Оцінює релевантність рішення для справи у діапазоні [0.0, 1.0].
    Враховує: категорію, ключові слова, рівень суду, результат.
    """
    score = 0.0

    # 1. Категорія справи
    if decision.category.lower() == case.category.lower():
        score += W_CATEGORY

    # 2. Ключові слова зі справи клієнта у тексті рішення
    keyword_score = _keyword_overlap(
        source=f"{case.subject} {case.key_facts}",
        target=f"{decision.subject} {' '.join(decision.legal_positions)}",
    )
    score += W_KEYWORDS * keyword_score

    # 3. Рівень суду
    court_lower = decision.court_name.lower()
    level_score = 0.0
    if case.court_level == "cassation" and any(k in court_lower for k in CASSATION_KEYWORDS):
        level_score = 1.0
    elif case.court_level == "appeal" and any(k in court_lower for k in APPEAL_KEYWORDS):
        level_score = 1.0
    elif case.court_level == "first" and not any(
        k in court_lower for k in CASSATION_KEYWORDS + APPEAL_KEYWORDS
    ):
        level_score = 1.0
    # Рішення ВС мають часткову цінність незалежно від рівня
    elif any(k in court_lower for k in CASSATION_KEYWORDS):
        level_score = 0.6
    score += W_COURT_LEVEL * level_score

    # 4. Відповідність бажаного результату
    desired_lower = case.desired_outcome.lower()
    if decision.result.lower() in desired_lower or desired_lower in decision.result.lower():
        score += W_RESULT_MATCH
    elif decision.result in ("задоволено", "частково задоволено") and "задоволен" in desired_lower:
        score += W_RESULT_MATCH * 0.5

    return min(score, 1.0)


def rank_decisions(
    case: CaseDescription,
    decisions: list[CourtDecision],
) -> list[tuple[CourtDecision, float]]:
    """
    Повертає список (рішення, оцінка) відсортований за спаданням оцінки.
    """
    scored = [(d, score_relevance(case, d)) for d in decisions]
    return sorted(scored, key=lambda x: x[1], reverse=True)


def _keyword_overlap(source: str, target: str) -> float:
    """Частка слів з source, що зустрічаються у target (Jaccard-подібна метрика)"""
    def tokenize(text: str) -> set[str]:
        return set(re.findall(r"\b[а-яіїєґa-z]{4,}\b", text.lower()))

    src_tokens = tokenize(source)
    tgt_tokens = tokenize(target)
    if not src_tokens:
        return 0.0
    overlap = src_tokens & tgt_tokens
    return len(overlap) / len(src_tokens)
