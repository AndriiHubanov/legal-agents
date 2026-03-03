"""
Агент 1 — Архітектор позову (Intake Agent).

Аналізує вільний текст ситуації та формує структуровану правову позицію:
- визначає тип справи та процесуальний кодекс;
- виокремлює сторони, факти, вимоги та правові підстави;
- враховує зворотний зв'язок від Агента 3 при повторних ітераціях.

Промт-кешування: системна роль агента (>1024 токенів) кешується автоматично.
"""
import json
import re
from shared.claude_client import ClaudeClient, CacheStats
from shared.logger import get_logger
from shared.models import CaseDescription, IntakeResult

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Кешований блок: роль агента + методологія (статичний, не змінюється між ітераціями)
# ---------------------------------------------------------------------------

_CACHED_SYSTEM = """
Ти — Агент 1 «Архітектор позову» мультиагентної правової системи.
Твоя роль: прийняти вільний текст фактичної ситуації та перетворити його на
структуровану правову позицію, придатну для подальшого аналізу і написання процесуального документа.

═══ МЕТОДОЛОГІЯ СТРУКТУРУВАННЯ ПОЗИЦІЇ ═══

КРОК 1. Визначення типу справи:
— Цивільна (ЦПК): спори між фізичними особами, майнові спори, сімейні, трудові (якщо не ТК),
  спори про право власності, відшкодування шкоди, договірні спори.
— Адміністративна (КАС): спори з органами держвлади, оскарження рішень / дій / бездіяльності
  суб'єктів владних повноважень (ДПС, ДСНС, органи місцевого самоврядування тощо).
— Господарська (ГПК): спори між юридичними особами або між юридичною особою і підприємцем,
  банківські спори, спори про банкрутство.
— Кримінальна (КПК): кримінальне провадження — не розглядаємо в цій системі.

КРОК 2. Ідентифікація сторін:
— Позивач: хто подає документ, тип (фізична / юридична особа).
— Відповідач: хто є протилежною стороною.
— Треті особи: хто може бути залучений.

КРОК 3. Фактичні обставини:
— Хронологія подій з датами (якщо відомі).
— Ключові дії / бездіяльність відповідача, що порушили права позивача.
— Наявні докази (документи, свідки, експертизи).
— Розмір збитків або інша шкода.

КРОК 4. Правові підстави:
— Які норми матеріального права порушено (ЦК, ЗК, ГК, КЗпП тощо).
— Які норми процесуального права застосовуються.
— Строки позовної давності: чи дотримані?

КРОК 5. Позовні вимоги:
— Що конкретно просить суд зробити (стягнути, зобов'язати, визнати, скасувати).
— Чи є вимоги майновими (є ціна позову) чи немайновими.

КРОК 6. Відсутня інформація:
— Чого не вистачає для повноцінного позову?
— Які питання потрібно з'ясувати у клієнта?

═══ ФОРМАТ ВІДПОВІДІ ═══
Відповідай ВИКЛЮЧНО у форматі JSON:
{
  "case_type": "позов",
  "procedural_code": "ЦПК",
  "recommended_doc_type": "appeal",
  "plaintiff_type": "фізична особа",
  "case_description": {
    "category": "civil",
    "subject": "короткий опис предмета спору (1 речення)",
    "key_facts": "хронологічний виклад ключових фактів",
    "desired_outcome": "що позивач хоче отримати",
    "court_level": "first",
    "opposing_arguments": "можливі контраргументи відповідача"
  },
  "identified_claims": [
    "Конкретна вимога 1 з посиланням на норму права",
    "Конкретна вимога 2"
  ],
  "legal_basis_detected": [
    "ст.16 ЦК України — способи захисту цивільних прав",
    "ст.1166 ЦК України — загальні підстави відповідальності за завдану майнову шкоду"
  ],
  "missing_info": [
    "Точна дата укладення договору",
    "Розмір понесених збитків з документальним підтвердженням"
  ],
  "confidence": 0.85
}

Поля case_description.category: civil / admin / commercial / criminal / labor
Поле procedural_code: ЦПК / КАС / ГПК
Поле recommended_doc_type: appeal / cassation / objection / motion_security / claim (позовна заява)
Мова відповіді: ТІЛЬКИ українська.
НЕ вигадуй факти, яких немає у тексті ситуації.
"""


class IntakeAgent:
    """Агент 1: структурує ситуацію у правову позицію."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def process(
        self,
        raw_situation: str,
        critic_questions: list[str] | None = None,
        iteration: int = 0,
    ) -> tuple[IntakeResult, CacheStats]:
        """
        Обробляє вхідну ситуацію (з урахуванням питань від Агента 3).

        raw_situation    — оригінальний текст ситуації від користувача.
        critic_questions — питання/заперечення від Агента 3 (при повторних ітераціях).
        iteration        — номер ітерації (для логування).
        """
        dynamic_system = self._build_dynamic_system(iteration, critic_questions)
        user_message = self._build_user_message(raw_situation, critic_questions)

        logger.info(f"[Agent1] Ітерація {iteration}: аналізую ситуацію ({len(raw_situation)} символів)")

        raw_response, stats = self.claude.analyze_cached(
            cached_system=_CACHED_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            label=f"Agent1-iter{iteration}",
        )

        result = self._parse_response(raw_response, raw_situation, iteration)
        return result, stats

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dynamic_system(iteration: int, critic_questions: list[str] | None) -> str:
        if iteration == 0 or not critic_questions:
            return ""
        questions_text = "\n".join(f"  — {q}" for q in critic_questions)
        return (
            f"УВАГА: це ітерація {iteration}. Агент 3 (Критик) виявив такі проблеми/питання:\n"
            f"{questions_text}\n\n"
            "Врахуй ці зауваження та скоригуй правову позицію відповідно. "
            "Якщо в тексті ситуації немає відповіді на питання — вкажи це у missing_info."
        )

    @staticmethod
    def _build_user_message(raw_situation: str, critic_questions: list[str] | None) -> str:
        parts = [
            "## ТЕКСТ СИТУАЦІЇ / ПОЗОВУ\n",
            raw_situation.strip(),
        ]
        if critic_questions:
            parts.append("\n\n## ЗАУВАЖЕННЯ ВІД КРИТИКА (врахувати при аналізі)")
            for q in critic_questions:
                parts.append(f"— {q}")
        parts.append("\n\n## ЗАВДАННЯ\nПроведи аналіз та поверни структуровану правову позицію у форматі JSON.")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw: str, raw_situation: str, iteration: int) -> IntakeResult:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        obj_match = re.search(r"\{[\s\S]+\}", json_str)
        if obj_match:
            json_str = obj_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[Agent1] Не вдалося розпарсити JSON — використовую fallback")
            data = {}

        case_desc_raw = data.get("case_description", {})
        case_description = CaseDescription(
            category=case_desc_raw.get("category", "civil"),
            subject=case_desc_raw.get("subject", raw_situation[:100]),
            key_facts=case_desc_raw.get("key_facts", raw_situation[:500]),
            desired_outcome=case_desc_raw.get("desired_outcome", ""),
            court_level=case_desc_raw.get("court_level", "first"),
            opposing_arguments=case_desc_raw.get("opposing_arguments"),
        )

        return IntakeResult(
            raw_situation=raw_situation,
            case_description=case_description,
            identified_claims=data.get("identified_claims", []),
            legal_basis_detected=data.get("legal_basis_detected", []),
            missing_info=data.get("missing_info", []),
            case_type=data.get("case_type", "позов"),
            procedural_code=data.get("procedural_code", "ЦПК"),
            recommended_doc_type=data.get("recommended_doc_type", "appeal"),
            plaintiff_type=data.get("plaintiff_type", "фізична особа"),
            confidence=float(data.get("confidence", 0.5)),
            intake_iteration=iteration,
        )
