"""
Агент 2 — Калькулятор судових зборів (Fees Calculator).

Розраховує судовий збір відповідно до Закону України "Про судовий збір"
та визначає правильну підсудність справи.

Промт-кешування: повний текст ЗСЗ + правила підсудності кешуються,
dynamic_system містить тип позивача та тип справи (маленький блок).
"""
import json
import re
from shared.claude_client import ClaudeClient, CacheStats
from shared.legal_texts import COURT_FEE_LAW_TEXT, CPC_REQUIREMENTS_TEXT
from shared.logger import get_logger
from shared.models import IntakeResult, FeesCalculation

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Кешований блок: роль + ЗСЗ + правила підсудності
# ---------------------------------------------------------------------------

_CACHED_SYSTEM = (
    """
Ти — Агент 2 «Фінансовий аналітик позову» мультиагентної правової системи.
Твоя роль: на основі структурованої правової позиції розрахувати судовий збір
та визначити правильну підсудність.

═══ АЛГОРИТМ РОЗРАХУНКУ СУДОВОГО ЗБОРУ ═══

1. Визначи тип вимог: майнова (є ціна позову) / немайнова / змішана.
2. Визнач тип позивача: фізична особа / юридична особа.
3. Розрахуй збір за відповідним пунктом ст.4 ЗСЗ.
4. Перевір мінімум та максимум (прожитковий мінімум = 3028 грн у 2024 р.).
5. Перевір наявність пільг (ст.5 ЗСЗ).
6. Визнач підсудність (ЦПК / КАС / ГПК).
7. Вкажи реквізити: "рахунок IBAN UA у відповідному управлінні ДКС за місцем розгляду справи".

═══ ФОРМАТ ВІДПОВІДІ ═══
Відповідай ВИКЛЮЧНО у форматі JSON:
{
  "claim_type": "майнова",
  "claim_amount": 150000.00,
  "plaintiff_type": "фізична особа",
  "fee_rate_description": "1% від ціни позову (ст.4 ч.1 п.1 ЗСЗ, фіз. особа)",
  "fee_amount": 1500.00,
  "fee_basis": "ст.4 ч.1 п.1а ЗСЗ",
  "exemptions_applicable": ["ст.5 ч.1 п.1 ЗСЗ — якщо позов про стягнення заробітної плати"],
  "court_jurisdiction": "Районний/міський суд за місцем проживання відповідача (ст.27 ЦПК)",
  "payment_requisites": "Сплатити судовий збір на рахунок IBAN UA відповідного управління Державної казначейської служби за місцем знаходження суду.",
  "notes": [
    "Якщо позов задоволено частково — збір повертається пропорційно відмовленій частині"
  ]
}

Поле claim_amount: null якщо немайнова вимога.
Поле fee_amount: завжди числове (грн).
Мова: ТІЛЬКИ українська.

═══ ІНСТРУМЕНТИ ═══
Маєш доступ до інструменту get_fee_rate.
Використовуй його якщо тип вимог неоднозначний або потрібно підтвердити ставку збору
для нестандартного випадку. Для стандартних випадків — розраховуй самостійно.
"""
    + "\n\n"
    + COURT_FEE_LAW_TEXT
    + "\n\n"
    + CPC_REQUIREMENTS_TEXT
)


class FeesCalculator:
    """Агент 2: розраховує судовий збір і підсудність."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def calculate(
        self,
        intake: IntakeResult,
        iteration: int = 0,
    ) -> tuple[FeesCalculation, CacheStats]:
        """
        Розраховує судовий збір на основі IntakeResult.
        Повертає (FeesCalculation, CacheStats).
        """
        dynamic_system = (
            f"Поточна ітерація: {iteration}. "
            f"Процесуальний кодекс: {intake.procedural_code}. "
            f"Тип позивача: {intake.plaintiff_type}."
        )
        user_message = self._build_user_message(intake)

        logger.info(f"[Agent2] Ітерація {iteration}: розраховую судовий збір")

        from shared.tools import FEES_TOOLS, FEES_HANDLERS
        raw_response, stats = self.claude.run_agent(
            cached_system=_CACHED_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            tools=FEES_TOOLS,
            tool_handlers=FEES_HANDLERS,
            label=f"Agent2-iter{iteration}",
        )

        result = self._parse_response(raw_response, intake)
        return result, stats

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(intake: IntakeResult) -> str:
        case = intake.case_description
        claims_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(intake.identified_claims))
        legal_text = "\n".join(f"  — {l}" for l in intake.legal_basis_detected)

        lines = [
            "## ПРАВОВА ПОЗИЦІЯ ДЛЯ РОЗРАХУНКУ ЗБОРУ",
            f"Тип справи: {intake.case_type}",
            f"Процесуальний кодекс: {intake.procedural_code}",
            f"Тип позивача: {intake.plaintiff_type}",
            f"Категорія: {case.category}",
            f"Предмет спору: {case.subject}",
            f"Ключові факти: {case.key_facts[:500]}",
            f"Бажаний результат: {case.desired_outcome}",
            "",
            "## ПОЗОВНІ ВИМОГИ",
            claims_text or "  (не визначено)",
            "",
            "## ПРАВОВІ ПІДСТАВИ",
            legal_text or "  (не визначено)",
            "",
            "## ЗАВДАННЯ",
            "1. Визнач, чи є вимоги майновими, немайновими або змішаними.",
            "2. Якщо майнові — визнач ціну позову (або зазнач що її не визначено в тексті).",
            "3. Розрахуй судовий збір відповідно до ЗСЗ.",
            "4. Визнач підсудність (який суд розглядатиме справу).",
            "5. Перевір пільги.",
            "Поверни результат у форматі JSON.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str, intake: IntakeResult) -> FeesCalculation:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        obj_match = re.search(r"\{[\s\S]+\}", json_str)
        if obj_match:
            json_str = obj_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[Agent2] Не вдалося розпарсити JSON — використовую fallback")
            data = {}

        return FeesCalculation(
            claim_type=data.get("claim_type", "невизначено"),
            claim_amount=data.get("claim_amount"),
            plaintiff_type=data.get("plaintiff_type", intake.plaintiff_type),
            fee_rate_description=data.get("fee_rate_description", ""),
            fee_amount=float(data.get("fee_amount", 0.0)),
            fee_basis=data.get("fee_basis", ""),
            exemptions_applicable=data.get("exemptions_applicable", []),
            court_jurisdiction=data.get("court_jurisdiction", ""),
            payment_requisites=data.get(
                "payment_requisites",
                "Рахунок IBAN UA відповідного управління ДКС за місцем суду.",
            ),
            notes=data.get("notes", []),
        )
