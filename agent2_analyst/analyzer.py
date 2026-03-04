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
Будь конкретним: посилайся на конкретні справи за їх номерами та конкретні статті законів.
Не вигадуй справи або норми права, яких немає у наданих даних.

Твоя відповідь має бути структурованим JSON з такими полями:
{
  "legal_arguments": [
    "Аргумент із посиланням на конкретну справу та статтю закону",
    ...
  ],
  "counter_arguments": [
    "Контраргумент із поясненням як спростувати",
    ...
  ],
  "recommended_strategy": "Детальний опис процесуальної стратегії",
  "confidence_score": 0.75,
  "cited_laws": [
    "ст.22 ЦК України",
    "ст.1166 ЦК України",
    "ст.156 ЗК України",
    "ст.157 ЗК України",
    "Порядок КМУ №284 від 19.04.1993"
  ],
  "damage_calculation_method": "площа самовільно зайнятої ділянки (га) × середня врожайність культури (т/га) × ринкова ціна (грн/т) − витрати на збирання врожаю",
  "required_evidence": [
    "Акт перевірки Держгеокадастру (Держземінспекції) про факт самовільного зайняття",
    "Висновок агрономічного експерта про середню врожайність культури",
    "Ринкові ціни на сільськогосподарську продукцію за відповідний період",
    "Кадастровий план та витяг з кадастру на земельну ділянку"
  ]
}

Поле damage_calculation_method заповнюй ТІЛЬКИ якщо справа стосується відшкодування збитків.
Якщо справа не стосується збитків — залиш порожнім рядком "".
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
        ranked = rank_decisions(case, decisions)
        top_decisions = [d for d, _ in ranked[:top_n]]

        logger.info(f"Аналізую {len(top_decisions)} рішень для справи: {case.subject[:60]}...")

        user_message = self._build_user_message(case, top_decisions)
        raw_response = self.claude.analyze(SYSTEM_PROMPT, user_message)

        parsed = self._parse_response(raw_response)
        avg_score = sum(s for _, s in ranked[:top_n]) / max(len(ranked[:top_n]), 1)

        return AnalysisReport(
            case_description=case,
            relevant_decisions=top_decisions,
            legal_arguments=parsed.get("legal_arguments", []),
            counter_arguments=parsed.get("counter_arguments", []),
            recommended_strategy=parsed.get("recommended_strategy", ""),
            confidence_score=parsed.get("confidence_score", round(avg_score, 2)),
            cited_laws=parsed.get("cited_laws", []),
            damage_calculation_method=parsed.get("damage_calculation_method", ""),
            required_evidence=parsed.get("required_evidence", []),
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
            laws_str = ", ".join(d.cited_laws[:5]) if d.cited_laws else "—"
            evidence_str = ", ".join(d.evidence_types[:4]) if d.evidence_types else "—"
            damage_str = f"{d.damage_amount:,.2f} грн" if d.damage_amount else "—"
            lines.append(
                f"\n{i}. Справа №{d.registry_number} | {d.court_name} | {d.decision_date} | {d.result}\n"
                f"   Предмет: {d.subject[:200]}\n"
                f"   Правові позиції: {positions}\n"
                f"   Норми права: {laws_str}\n"
                f"   Докази у справі: {evidence_str}\n"
                f"   Сума збитків: {damage_str}"
            )

        lines.append(
            "\n## ЗАВДАННЯ\n"
            "На основі наведеної практики:\n"
            "1. Визначте 3–7 найсильніших правових аргументів на захист клієнта\n"
            "   (кожен аргумент: посилання на справу + конкретна стаття закону)\n"
            "2. Перерахуйте 2–4 можливі контраргументи опонента з методами спростування\n"
            "3. Сформулюйте детальну рекомендовану процесуальну стратегію\n"
            "4. Складіть зведений перелік норм права що застосовуються\n"
            "5. Якщо справа про збитки — опишіть методологію їх розрахунку\n"
            "6. Складіть перелік необхідних доказів для підтримки позиції\n"
            "7. Оцініть шанси на успіх (confidence_score від 0.0 до 1.0)\n"
            "\nВідповідь надай ТІЛЬКИ у форматі JSON."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Витягує JSON з відповіді Claude (може бути обгорнутий у ```json блок)"""
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
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
                "cited_laws": [],
                "damage_calculation_method": "",
                "required_evidence": [],
            }
