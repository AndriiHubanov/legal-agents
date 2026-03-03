"""
Аналіз релевантної судової практики за допомогою Claude
"""
import json
import re
from shared.claude_client import ClaudeClient
from shared.logger import get_logger
from shared.models import AnalysisReport, CaseDescription, CourtDecision
from agent2_analyst.ranker import rank_decisions

logger = get_logger(__name__)

SYSTEM_PROMPT = """Ти досвідчений адвокат в українській судовій системі з 15-річним практичним досвідом.
Твоє завдання — проаналізувати надану судову практику та визначити найсильніші правові аргументи
для вирішення справи на користь клієнта.

Відповідай ВИКЛЮЧНО українською мовою.
Будь конкретним: посилайся на конкретні справи за їх номерами.
Не вигадуй справи або норми права, яких немає у наданих даних.

Твоя відповідь має бути структурованим JSON з такими полями:
{
  "legal_arguments": ["аргумент 1", "аргумент 2", ...],
  "counter_arguments": ["контраргумент 1", ...],
  "recommended_strategy": "опис стратегії",
  "confidence_score": 0.75
}
"""


class PracticeAnalyzer:
    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def analyze(
        self,
        case: CaseDescription,
        decisions: list[CourtDecision],
        top_n: int = 10,
    ) -> AnalysisReport:
        """
        Аналізує релевантну практику та формує AnalysisReport.
        """
        # Ранжуємо рішення
        ranked = rank_decisions(case, decisions)
        top_decisions = [d for d, _ in ranked[:top_n]]

        logger.info(f"Аналізую {len(top_decisions)} рішень для справи: {case.subject[:60]}...")

        user_message = self._build_user_message(case, top_decisions)
        raw_response = self.claude.analyze(SYSTEM_PROMPT, user_message)

        # Парсимо структурований вивід
        parsed = self._parse_response(raw_response)
        avg_score = sum(s for _, s in ranked[:top_n]) / max(len(ranked[:top_n]), 1)

        return AnalysisReport(
            case_description=case,
            relevant_decisions=top_decisions,
            legal_arguments=parsed.get("legal_arguments", []),
            counter_arguments=parsed.get("counter_arguments", []),
            recommended_strategy=parsed.get("recommended_strategy", ""),
            confidence_score=parsed.get("confidence_score", round(avg_score, 2)),
        )

    def score_relevance(self, case: CaseDescription, decision: CourtDecision) -> float:
        """Оцінити релевантність одного рішення для справи (0.0 – 1.0)"""
        from agent2_analyst.ranker import score_relevance
        return score_relevance(case, decision)

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(case: CaseDescription, decisions: list[CourtDecision]) -> str:
        lines = [
            "## СПРАВА КЛІЄНТА",
            f"Категорія: {case.category}",
            f"Предмет спору: {case.subject}",
            f"Ключові факти: {case.key_facts}",
            f"Бажаний результат: {case.desired_outcome}",
            f"Рівень суду: {case.court_level}",
        ]
        if case.opposing_arguments:
            lines.append(f"Аргументи опонента: {case.opposing_arguments}")

        lines.append("\n## РЕЛЕВАНТНА СУДОВА ПРАКТИКА")
        for i, d in enumerate(decisions, 1):
            positions = "; ".join(d.legal_positions[:3]) if d.legal_positions else "—"
            lines.append(
                f"\n{i}. Справа №{d.registry_number} | {d.court_name} | {d.decision_date} | {d.result}\n"
                f"   Предмет: {d.subject[:200]}\n"
                f"   Правові позиції: {positions}"
            )

        lines.append(
            "\n## ЗАВДАННЯ\n"
            "На основі наведеної практики:\n"
            "1. Визначте 3–7 найсильніших правових аргументів на захист клієнта\n"
            "2. Перерахуйте 2–4 можливі контраргументи опонента\n"
            "3. Сформулюйте рекомендовану процесуальну стратегію\n"
            "4. Оцініть шанси на успіх (confidence_score від 0.0 до 1.0)\n"
            "\nВідповідь надай ТІЛЬКИ у форматі JSON."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Витягає JSON з відповіді Claude (може бути обгорнутий у ```json блок)"""
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Резервний варіант: витягнути хоча б перший JSON-об'єкт
            obj_match = re.search(r"\{[\s\S]+\}", json_str)
            if obj_match:
                try:
                    return json.loads(obj_match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning("Не вдалося розпарсити JSON відповідь Claude")
            return {
                "legal_arguments": [raw[:500]],
                "counter_arguments": [],
                "recommended_strategy": "",
                "confidence_score": 0.5,
            }
