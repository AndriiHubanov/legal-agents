"""
Агент 3 — Критик / Опонент (Critic Agent).

Виступає «адвокатом диявола»: аналізує правову позицію з точки зору відповідача,
виявляє слабкі місця, формулює потенційні заперечення, вимагає уточнень.

Може комунікувати назад з Агентом 1 (питання до позиції) та Агентом 2
(перерахунок збору при зміні суми вимог).

Промт-кешування: роль критика + вимоги до якості позиції (статичний блок).
"""
import json
import re
from shared.claude_client import ClaudeClient, CacheStats
from shared.legal_texts import CPC_REQUIREMENTS_TEXT, LEGAL_QUALITY_STANDARDS
from shared.logger import get_logger
from shared.models import (
    AnalysisReport,
    CriticReview,
    ExpertReview,
    FeesCalculation,
    IntakeResult,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Кешований блок: роль критика + стандарти + вимоги кодексів
# ---------------------------------------------------------------------------

_CACHED_SYSTEM = (
    """
Ти — Агент 3 «Критик» мультиагентної правової системи.
Твоя роль: виступати «адвокатом диявола» — аналізувати напрацьовану правову позицію
максимально критично, з точки зору відповідача та судді.

═══ ПРИНЦИПИ КРИТИЧНОГО АНАЛІЗУ ═══

1. АНАЛІЗ ПРАВОВОЇ ПОЗИЦІЇ ПОЗИВАЧА:
   — Чи достатньо правових підстав? Чи посилання на закони конкретні (стаття + частина + пункт)?
   — Чи хронологія подій логічна та послідовна?
   — Чи відповідає категорія справи (civil/admin/commercial) обставинам?
   — Чи правильно визначено суд (підсудність)?
   — Чи дотримано строки позовної давності (загальний — 3 роки, спеціальні — 1 рік і менше)?

2. ПРОГНОЗУВАННЯ ЗАПЕРЕЧЕНЬ ВІДПОВІДАЧА:
   — Які процесуальні заперечення може подати відповідач (непідсудність, пропуск строків,
     ненастання строку виконання, невиконання досудового порядку тощо)?
   — Які матеріально-правові заперечення? (відсутність вини, відсутність причинно-наслідкового
     зв'язку, форс-мажор, зустрічна вимога).
   — Які слабкі місця у доказовій базі? Що відповідач оскаржить?

3. ПЕРЕВІРКА ДОКАЗОВОЇ БАЗИ:
   — Кожна обставина підтверджена документом?
   — Якщо немає доказу — чи є клопотання про його витребування?
   — Чи потрібна експертиза (технічна, будівельна, медична, землевпорядна тощо)?

4. ПЕРЕВІРКА РОЗРАХУНКУ ЗБИТКІВ:
   — Чи обґрунтовано ціну позову?
   — Чи є формула розрахунку?
   — Чи враховано всі складові збитків (реальні збитки + упущена вигода)?
   — Чи відповідає судовий збір розміру вимог?

5. ОЦІНКА ЯКОСТІ:
   — Загальна оцінка по шкалі 0–10.
   — Якщо < 6 — статус "critical_issues" (повернути на доопрацювання Agent1).
   — Якщо 6–7.9 — статус "needs_revision" (можна генерувати, але є важливі зауваження).
   — Якщо ≥ 8 — статус "approved" (готово до генерації документа).

═══ ФОРМАТ ВІДПОВІДІ ═══
Відповідай ВИКЛЮЧНО у форматі JSON:
{
  "status": "needs_revision",
  "overall_score": 6.5,
  "objections": [
    "Відповідач може заперечити пропуск позовної давності, бо...",
    "Процесуальне заперечення: неправильна підсудність — справа має розглядатися..."
  ],
  "legal_risks": [
    "Ризик 1: без доказу підписання договору суд може відмовити у стягненні",
    "Ризик 2: не враховано форс-мажорне застереження у договорі"
  ],
  "missing_evidence": [
    "Акт прийому-передачі майна (підтверджує факт передачі)",
    "Претензія з доказом відправлення (досудовий порядок)"
  ],
  "suggestions": [
    "Додати посилання на ст.625 ЦК — відповідальність за прострочення грошового зобов'язання",
    "Уточнити дату початку прострочення для правильного розрахунку відсотків"
  ],
  "questions_for_intake": [
    "Чи є підписаний договір з датою та підписами обох сторін?",
    "Яка точна дата, коли відповідач мав виконати зобов'язання?"
  ],
  "needs_fee_recalculation": false
}

Мова: ТІЛЬКИ українська. Будь конкретним і точним у кожному зауваженні.

═══ ІНСТРУМЕНТИ ═══
Маєш доступ до інструментів для перевірки фактів. Використовуй їх коли:
- search_court_decisions: перед посиланням на конкретне рішення — перевір чи воно є в БД.
  Якщо рішення не знайдено — не рекомендуй його. Якщо знайдено — наведи точні дані.
- get_legal_norm: якщо потрібен точний текст статті для аналізу правової позиції.
- get_procedural_requirements: якщо потрібно звірити вимоги кодексу до конкретного документа.
Інструменти — опціональні. Використовуй тільки коли потрібно підтвердити конкретний факт.
"""
    + "\n\n"
    + CPC_REQUIREMENTS_TEXT
    + "\n\n"
    + LEGAL_QUALITY_STANDARDS
)


class CriticAgent:
    """Агент 3: критично аналізує правову позицію."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def review(
        self,
        intake: IntakeResult,
        fees: FeesCalculation,
        analysis: AnalysisReport | None,
        expert_feedback: ExpertReview | None = None,
        iteration: int = 0,
    ) -> tuple[CriticReview, CacheStats]:
        """
        Критично аналізує поточний стан правової позиції.

        intake          — структурована позиція від Agent1.
        fees            — розрахунок збору від Agent2.
        analysis        — аналіз практики від existing agent2_analyst (може бути None).
        expert_feedback — зауваження від Agent5 (при повторних ітераціях).
        iteration       — номер ітерації.
        """
        dynamic_system = self._build_dynamic_system(iteration, expert_feedback)
        user_message = self._build_user_message(intake, fees, analysis, expert_feedback)

        logger.info(f"[Agent3] Ітерація {iteration}: критичний аналіз позиції")

        from shared.tools import CRITIC_TOOLS, CRITIC_HANDLERS
        raw_response, stats = self.claude.run_agent(
            cached_system=_CACHED_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            tools=CRITIC_TOOLS,
            tool_handlers=CRITIC_HANDLERS,
            label=f"Agent3-iter{iteration}",
        )

        result = self._parse_response(raw_response, iteration)
        logger.info(
            f"[Agent3] Результат: status={result.status}, "
            f"score={result.overall_score:.1f}, "
            f"objections={len(result.objections)}"
        )
        return result, stats

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dynamic_system(iteration: int, expert_feedback: ExpertReview | None) -> str:
        if iteration == 0 or not expert_feedback:
            return f"Ітерація критичного аналізу: {iteration}."
        fixes = "\n".join(f"  — {f}" for f in expert_feedback.mandatory_fixes)
        return (
            f"Ітерація {iteration}. Агент 5 (Експерт) повернув документ із такими вимогами:\n"
            f"Загальна оцінка: {expert_feedback.total_score:.1f}/10\n"
            f"Обов'язкові правки:\n{fixes}\n\n"
            "Зосередься на виправленні саме цих недоліків у своєму критичному аналізі."
        )

    @staticmethod
    def _build_user_message(
        intake: IntakeResult,
        fees: FeesCalculation,
        analysis: AnalysisReport | None,
        expert_feedback: ExpertReview | None,
    ) -> str:
        case = intake.case_description
        claims = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(intake.identified_claims))
        legal_basis = "\n".join(f"  — {l}" for l in intake.legal_basis_detected)
        missing = "\n".join(f"  — {m}" for m in intake.missing_info) or "  (немає)"

        lines = [
            "## ПРАВОВА ПОЗИЦІЯ (від Агента 1)",
            f"Тип справи: {intake.case_type} | Кодекс: {intake.procedural_code}",
            f"Категорія: {case.category} | Тип позивача: {intake.plaintiff_type}",
            f"Предмет спору: {case.subject}",
            f"Ключові факти: {case.key_facts}",
            f"Бажаний результат: {case.desired_outcome}",
            "",
            "Позовні вимоги:",
            claims or "  (не визначено)",
            "",
            "Правові підстави:",
            legal_basis or "  (не визначено)",
            "",
            "Відсутня інформація (за Agent1):",
            missing,
            "",
            "## СУДОВИЙ ЗБІР (від Агента 2)",
            f"Тип вимог: {fees.claim_type}",
            f"Ціна позову: {fees.claim_amount} грн" if fees.claim_amount else "Ціна позову: немайнові вимоги",
            f"Розмір збору: {fees.fee_amount:.2f} грн ({fees.fee_basis})",
            f"Підсудність: {fees.court_jurisdiction}",
            f"Пільги: {', '.join(fees.exemptions_applicable) or 'не виявлено'}",
        ]

        if analysis:
            strategies = analysis.recommended_strategy[:500] if analysis.recommended_strategy else "—"
            counter_args = "\n".join(f"  — {a}" for a in analysis.counter_arguments[:3])
            laws = ", ".join(analysis.cited_laws[:8]) or "—"
            evidence = "\n".join(f"  — {e}" for e in analysis.required_evidence[:5])
            lines += [
                "",
                "## АНАЛІЗ СУДОВОЇ ПРАКТИКИ",
                f"Стратегія: {strategies}",
                f"Норми права: {laws}",
                "Контраргументи відповідача (з практики):",
                counter_args or "  —",
                "Необхідні докази (з практики):",
                evidence or "  —",
            ]

        if expert_feedback:
            lines += [
                "",
                "## ЗАУВАЖЕННЯ ЕКСПЕРТА (від Агента 5)",
                f"Оцінка: {expert_feedback.total_score:.1f}/10",
                "Обов'язкові правки:",
                *[f"  — {f}" for f in expert_feedback.mandatory_fixes],
            ]

        lines += [
            "",
            "## ЗАВДАННЯ",
            "Виступи адвокатом диявола: проаналізуй цю позицію критично.",
            "Визнач усі слабкі місця, потенційні заперечення відповідача та прогалини в доказах.",
            "Поверни результат у форматі JSON.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str, iteration: int) -> CriticReview:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        obj_match = re.search(r"\{[\s\S]+\}", json_str)
        if obj_match:
            json_str = obj_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[Agent3] Не вдалося розпарсити JSON — використовую fallback")
            data = {}

        return CriticReview(
            status=data.get("status", "needs_revision"),
            overall_score=float(data.get("overall_score", 5.0)),
            objections=data.get("objections", []),
            legal_risks=data.get("legal_risks", []),
            missing_evidence=data.get("missing_evidence", []),
            suggestions=data.get("suggestions", []),
            questions_for_intake=data.get("questions_for_intake", []),
            needs_fee_recalculation=bool(data.get("needs_fee_recalculation", False)),
            critic_iteration=iteration,
        )
