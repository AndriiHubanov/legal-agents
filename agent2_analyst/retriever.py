"""
RAG-пошук релевантних рішень під конкретну справу
"""
from agent1_collector.storage import DecisionStorage
from shared.logger import get_logger
from shared.models import CaseDescription, CourtDecision

logger = get_logger(__name__)

# Рівні судів у порядку авторитетності (від найвищого)
COURT_AUTHORITY_ORDER = ["касація", "верховний", "вс ", "вгс", "вас", "апеляці"]


class PracticeRetriever:
    def __init__(self, storage: DecisionStorage):
        self.storage = storage

    def find_relevant(
        self,
        case: CaseDescription,
        top_k: int = 20,
    ) -> list[CourtDecision]:
        """
        Знаходить найбільш релевантні рішення для справи клієнта.
        """
        query = self._build_query(case)
        filters = {"category": case.category}

        decisions = self.storage.search_similar(query, filters=filters, top_k=top_k * 2)

        # Пріоритизувати за рівнем суду
        decisions = self.get_by_court_level(decisions, case.court_level)
        return decisions[:top_k]

    def find_opposing(
        self,
        case: CaseDescription,
        top_k: int = 10,
    ) -> list[CourtDecision]:
        """
        Знаходить рішення з протилежним результатом —
        для підготовки до контраргументів опонента.
        """
        query = self._build_query(case)

        # Шукаємо рішення з протилежним результатом
        opposing_results = {
            "задоволено": ["відмовлено"],
            "відмовлено": ["задоволено", "частково задоволено"],
        }
        target_results = opposing_results.get(
            case.desired_outcome.lower(), ["задоволено", "відмовлено"]
        )

        all_opposing: list[CourtDecision] = []
        for result in target_results:
            found = self.storage.search_similar(
                query,
                filters={"category": case.category, "result": result},
                top_k=top_k,
            )
            all_opposing.extend(found)

        # Дедублікація
        seen: set[str] = set()
        unique: list[CourtDecision] = []
        for d in all_opposing:
            if d.id not in seen:
                seen.add(d.id)
                unique.append(d)

        return unique[:top_k]

    def get_by_court_level(
        self,
        decisions: list[CourtDecision],
        preferred_level: str,
    ) -> list[CourtDecision]:
        """
        Сортує рішення: спочатку ВС та касаційні суди (найбільший авторитет),
        потім апеляційні, потім першої інстанції.
        """

        def authority_score(decision: CourtDecision) -> int:
            court_lower = decision.court_name.lower()
            for i, keyword in enumerate(COURT_AUTHORITY_ORDER):
                if keyword in court_lower:
                    return len(COURT_AUTHORITY_ORDER) - i
            return 0

        return sorted(decisions, key=authority_score, reverse=True)

    @staticmethod
    def _build_query(case: CaseDescription) -> str:
        parts = [case.subject, case.key_facts]
        if case.opposing_arguments:
            parts.append(case.opposing_arguments)
        return " ".join(parts)[:1500]
