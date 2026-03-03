"""
Агент 5 — Надпотужний Експерт-Контролер (Expert Reviewer).

Проводить фінальний аудит згенерованого документа за найвищими юридичними стандартами.
Має повноваження:
— схвалити документ (approved) — пайплайн завершується;
— повернути на доопрацювання Агенту 3 (revise_critic) — переосмислити позицію;
— повернути на доопрацювання Агенту 4 (revise_generator) — переписати документ.

Промт-кешування: весь статичний блок (роль + стандарти якості) кешується.
Динамічний блок: ітерація, поточні оцінки, попередні рішення.
"""
import json
import re
from shared.claude_client import ClaudeClient, CacheStats
from shared.legal_texts import LEGAL_QUALITY_STANDARDS
from shared.logger import get_logger
from shared.models import (
    AnalysisReport,
    ComplianceResult,
    ExpertReview,
    FeesCalculation,
    GeneratedDocumentV2,
    IntakeResult,
)

logger = get_logger(__name__)

# Мінімальна оцінка для схвалення документа
APPROVAL_THRESHOLD = 7.5

# ---------------------------------------------------------------------------
# Кешований блок: роль експерта + стандарти якості
# ---------------------------------------------------------------------------

_CACHED_SYSTEM = (
    """
Ти — Агент 5 «Надпотужний Експерт» мультиагентної правової системи.
Ти — провідний партнер адвокатського об'єднання з 25-річним досвідом,
спеціаліст з цивільного, господарського та адміністративного судочинства України,
автор десятків успішних справ у Верховному Суді та ЄСПЛ.

Твоя роль: провести фінальний аудит процесуального документа та вирішити його долю.

═══ КРИТЕРІЇ ОЦІНКИ ═══

1. АРГУМЕНТАЦІЯ (argumentation_score, 0–10):
   — Наскільки переконлива правова позиція?
   — Чи кожна вимога підкріплена нормою права та доказом?
   — Чи спростовано очікувані заперечення відповідача?
   — Чи правильно застосовано норми матеріального права?
   — Чи використано актуальну судову практику?

2. ВІДПОВІДНІСТЬ ВИМОГАМ (compliance_score, 0–10):
   — Чи виконані всі формальні вимоги процесуального кодексу?
   — Чи правильно визначено суд і підсудність?
   — Чи правильно розраховано судовий збір?

3. ДОКАЗОВА БАЗА (evidence_score, 0–10):
   — Чи кожна ключова обставина підтверджена доказом?
   — Чи подані клопотання про витребування відсутніх доказів?
   — Чи достатньо доказів для винесення рішення?

4. ПЕРЕКОНЛИВІСТЬ (persuasiveness_score, 0–10):
   — Чи переконає цей документ суддю?
   — Чи логічна і послідовна структура?
   — Чи офіційно-діловий стиль без емоцій і помилок?

═══ ПРОХІДНИЙ БАЛ ═══
   — Кожен критерій: не менше 7.5/10.
   — Загальна оцінка (середнє арифметичне): не менше 8.0/10.
   — Якщо хоча б один критерій < 7.5 → відхилити.

═══ РІШЕННЯ ═══
   — "approved"         → всі критерії ≥ 7.5 і загальний ≥ 8.0. Документ готовий.
   — "revise_generator" → документ слабкий у формулюваннях/структурі, але позиція правильна.
                          Агент 4 переписує з урахуванням mandatory_fixes.
   — "revise_critic"    → фундаментальні проблеми у правовій позиції (неправильна норма права,
                          неправильна підсудність, пропуск строків, відсутність ключових доказів).
                          Агент 3 переосмислює позицію, а потім Агент 4 генерує заново.

═══ ФОРМАТ ВІДПОВІДІ ═══
Відповідай ВИКЛЮЧНО у форматі JSON:
{
  "argumentation_score": 8.5,
  "compliance_score": 9.0,
  "evidence_score": 7.0,
  "persuasiveness_score": 8.0,
  "total_score": 8.1,
  "decision": "revise_generator",
  "mandatory_fixes": [
    "Відсутній розрахунок 3% річних за ст.625 ЦК — додати формулу і суму",
    "Прохальна частина: не вказано вимогу про стягнення судових витрат"
  ],
  "optional_improvements": [
    "Можна додати посилання на постанову ВС від [дата] у схожій справі"
  ],
  "expert_opinion": "Документ має міцну правову основу, але потребує доопрацювання..."
}

Мова: ТІЛЬКИ українська. Будь конкретним і безкомпромісним — якість важливіша за швидкість.
"""
    + "\n\n"
    + LEGAL_QUALITY_STANDARDS
)


class ExpertReviewer:
    """Агент 5: фінальний аудит документа."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def review(
        self,
        generated: GeneratedDocumentV2,
        intake: IntakeResult,
        fees: FeesCalculation,
        analysis: AnalysisReport | None = None,
        iteration: int = 0,
        previous_reviews: list[ExpertReview] | None = None,
    ) -> tuple[ExpertReview, CacheStats]:
        """
        Проводить фінальний аудит документа.

        generated        — документ від Agent4 (текст + compliance).
        intake           — правова позиція від Agent1.
        fees             — судові збори від Agent2.
        analysis         — аналіз практики (опційно).
        iteration        — номер ітерації.
        previous_reviews — попередні рішення Експерта (для контексту).
        """
        dynamic_system = self._build_dynamic_system(iteration, previous_reviews)
        user_message = self._build_user_message(generated, intake, fees, analysis, previous_reviews)

        logger.info(f"[Agent5] Ітерація {iteration}: фінальний аудит документа")

        raw_response, stats = self.claude.analyze_cached(
            cached_system=_CACHED_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            label=f"Agent5-iter{iteration}",
        )

        result = self._parse_response(raw_response, iteration)
        logger.info(
            f"[Agent5] Рішення: {result.decision} | "
            f"Оцінка: {result.total_score:.1f}/10 "
            f"(arg={result.argumentation_score:.1f}, "
            f"comp={result.compliance_score:.1f}, "
            f"evid={result.evidence_score:.1f}, "
            f"pers={result.persuasiveness_score:.1f})"
        )
        return result, stats

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dynamic_system(
        iteration: int,
        previous_reviews: list[ExpertReview] | None,
    ) -> str:
        parts = [f"Ітерація експертного аудиту: {iteration}."]

        if previous_reviews:
            last = previous_reviews[-1]
            parts.append(
                f"Попереднє рішення (ітерація {last.expert_iteration}): "
                f"{last.decision}, оцінка {last.total_score:.1f}/10.\n"
                f"Попередні обов'язкові правки: {'; '.join(last.mandatory_fixes[:3])}.\n"
                "Перевір, чи виправлено ці недоліки у поточній версії."
            )

        return "\n\n".join(parts)

    @staticmethod
    def _build_user_message(
        generated: GeneratedDocumentV2,
        intake: IntakeResult,
        fees: FeesCalculation,
        analysis: AnalysisReport | None,
        previous_reviews: list[ExpertReview] | None,
    ) -> str:
        compliance = generated.compliance
        missing_elements = [el for el, present in compliance.required_elements.items() if not present]

        lines = [
            "## ДОКУМЕНТ НА АУДИТ",
            f"Тип документа: {generated.doc_type}",
            f"Процесуальний кодекс: {intake.procedural_code}",
            f"Ітерація генерації: {generated.generation_iteration}",
            "",
            "### РЕЗУЛЬТАТ ПЕРЕВІРКИ ВІДПОВІДНОСТІ (від Compliance Checker)",
            f"Відповідність: {'ТАК' if compliance.is_compliant else 'НІ'} "
            f"(score: {compliance.compliance_score:.1f}/10)",
        ]

        if compliance.violations:
            lines.append("Порушення формальних вимог:")
            for v in compliance.violations:
                lines.append(f"  — {v}")

        if missing_elements:
            lines.append("Відсутні обов'язкові елементи:")
            for el in missing_elements:
                lines.append(f"  — {el}")

        if compliance.warnings:
            lines.append("Застереження:")
            for w in compliance.warnings:
                lines.append(f"  — {w}")

        lines += [
            "",
            "### КОНТЕКСТ СПРАВИ",
            f"Категорія: {intake.case_description.category} | "
            f"Предмет: {intake.case_description.subject}",
            f"Правові підстави: {', '.join(intake.legal_basis_detected[:5]) or '—'}",
            f"Судовий збір: {fees.fee_amount:.2f} грн ({fees.fee_basis})",
        ]

        if analysis:
            lines += [
                f"Норми права (аналіз): {', '.join(analysis.cited_laws[:8]) or '—'}",
                f"Впевненість аналізу: {analysis.confidence_score:.0%}",
            ]

        if previous_reviews:
            lines.append("")
            lines.append("### ПОПЕРЕДНІ АУДИТИ")
            for pr in previous_reviews[-2:]:
                lines.append(
                    f"Ітерація {pr.expert_iteration}: {pr.decision}, "
                    f"оцінка {pr.total_score:.1f}/10"
                )

        lines += [
            "",
            "### ТЕКСТ ДОКУМЕНТА",
            "─" * 60,
            generated.content,
            "─" * 60,
            "",
            "### ЗАВДАННЯ",
            "Проведи повний аудит документа за чотирма критеріями.",
            "Визнач, що конкретно потрібно виправити (якщо є).",
            f"Прохідний бал: кожен критерій ≥ {APPROVAL_THRESHOLD}, загальний ≥ 8.0.",
            "Поверни результат у форматі JSON.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str, iteration: int) -> ExpertReview:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        obj_match = re.search(r"\{[\s\S]+\}", json_str)
        if obj_match:
            json_str = obj_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[Agent5] Не вдалося розпарсити JSON — fallback")
            data = {}

        arg = float(data.get("argumentation_score", 5.0))
        comp = float(data.get("compliance_score", 5.0))
        evid = float(data.get("evidence_score", 5.0))
        pers = float(data.get("persuasiveness_score", 5.0))
        total = data.get("total_score") or round((arg + comp + evid + pers) / 4, 2)

        # Автоматична корекція рішення, якщо JSON-значення не узгоджене з балами
        decision = data.get("decision", "revise_critic")
        if all(s >= APPROVAL_THRESHOLD for s in [arg, comp, evid, pers]) and float(total) >= 8.0:
            decision = "approved"

        return ExpertReview(
            argumentation_score=arg,
            compliance_score=comp,
            evidence_score=evid,
            persuasiveness_score=pers,
            total_score=float(total),
            decision=decision,
            mandatory_fixes=data.get("mandatory_fixes", []),
            optional_improvements=data.get("optional_improvements", []),
            expert_opinion=data.get("expert_opinion", ""),
            expert_iteration=iteration,
        )
