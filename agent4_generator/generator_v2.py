"""
Агент 4 — Генератор документів V2 (Generator + Compliance Check).

Генерує фінальний текст позову / апеляції / заперечення через Claude,
потім передає документ на перевірку відповідності ComplianceChecker.

Промт-кешування: системна роль + шаблони процесуальних документів кешуються.
Динамічний блок: поточний стан справи + зауваження критика + зауваження експерта.
"""
import json
import re
from pathlib import Path
from shared.claude_client import ClaudeClient, CacheStats
from shared.legal_texts import CPC_REQUIREMENTS_TEXT, LEGAL_QUALITY_STANDARDS
from shared.logger import get_logger
from shared.models import (
    AnalysisReport,
    ComplianceResult,
    CriticReview,
    ExpertReview,
    FeesCalculation,
    GeneratedDocumentV2,
    IntakeResult,
)
from agent4_generator.compliance import ComplianceChecker

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Кешований блок: роль генератора + структура документів + вимоги кодексів
# ---------------------------------------------------------------------------

_CACHED_SYSTEM = (
    """
Ти — Агент 4 «Генератор процесуальних документів» мультиагентної правової системи.
Твоя роль: на основі структурованої правової позиції, критичного аналізу та судової практики
сформувати повний текст процесуального документа найвищої якості.

═══ СТРУКТУРА ПРОЦЕСУАЛЬНОГО ДОКУМЕНТА ═══

1. ШАПКА (реквізити сторін та суду):
   До [найменування суду]
   Позивач/Апелянт: [ПІБ або назва], [адреса], [РНОКПП або ЄДРПОУ], [телефон]
   Відповідач: [ПІБ або назва], [адреса], [РНОКПП або ЄДРПОУ]
   [для юр. осіб: назва суду та адреса]

2. НАЗВА ДОКУМЕНТА (по центру, жирним):
   ПОЗОВНА ЗАЯВА / АПЕЛЯЦІЙНА СКАРГА / ВІДЗИВ / тощо

3. ВСТУПНА ЧАСТИНА:
   — Опис суті спору в 2–3 реченнях.
   — Посилання на підставу звернення (норма права або попереднє рішення).

4. ОБСТАВИНИ СПРАВИ (у хронологічному порядку):
   — Дата та суть кожної ключової події.
   — Дії / бездіяльність відповідача, що порушили права позивача.
   — Посилання на кожен доказ.

5. ПРАВОВЕ ОБҐРУНТУВАННЯ (мотивувальна частина):
   — Конкретні статті закону (стаття + частина + пункт + назва закону).
   — Чому діяння відповідача порушує зазначену норму.
   — Посилання на практику ВС / ЄСПЛ (ТІЛЬКИ з наданого переліку практики).
   — Спростування очікуваних заперечень відповідача.
   — Якщо є збитки — формула розрахунку та джерела.

6. ПРОХАЛЬНА ЧАСТИНА (нумерований перелік):
   ПРОШУ СУД:
   1. [конкретна вимога 1]
   2. [конкретна вимога 2]
   3. Судовий збір у розмірі [сума] грн покласти на відповідача.

7. ПЕРЕЛІК ДОДАТКІВ:
   1. [документ 1]
   2. [документ 2]

8. ПІДПИС ТА ДАТА

═══ ОБОВ'ЯЗКОВІ ПРАВИЛА ═══
— Мова: ТІЛЬКИ українська, офіційно-діловий стиль.
— Не вигадуй справи, яких немає у наданій практиці.
— Кожна обставина → посилання на доказ.
— Кожна вимога → посилання на норму права.
— Спростуй у тексті заперечення, виявлені критиком.
— Включи розрахунок судового збору у вступній частині або у прохальній.
— Якщо є пропущена інформація — вкажи [___] для заповнення вручну.

═══ ІНСТРУМЕНТИ ═══
Маєш доступ до інструментів для уточнення даних під час генерації:
- get_legal_norm: виклич перед цитуванням статті закону щоб отримати точне формулювання норми.
  Це запобігає помилковому цитуванню. Наприклад: get_legal_norm("ст.22 ЦК України").
- get_procedural_requirements: виклич якщо потрібно перевірити обов'язкові елементи документа.
- get_document_template_hints: виклич якщо потрібно уточнити структуру конкретної секції.
Інструменти — опціональні. Пріоритет — якість і точність цитування норм права.
"""
    + "\n\n"
    + CPC_REQUIREMENTS_TEXT
    + "\n\n"
    + LEGAL_QUALITY_STANDARDS
)

DOCUMENT_TITLES_UK = {
    "appeal": "АПЕЛЯЦІЙНА СКАРГА",
    "cassation": "КАСАЦІЙНА СКАРГА",
    "objection": "ВІДЗИВ НА ПОЗОВНУ ЗАЯВУ",
    "claim": "ПОЗОВНА ЗАЯВА",
    "motion_security": "КЛОПОТАННЯ ПРО ЗАБЕЗПЕЧЕННЯ ПОЗОВУ",
    "motion_restore_deadline": "КЛОПОТАННЯ ПРО ПОНОВЛЕННЯ СТРОКУ",
    "motion_evidence": "КЛОПОТАННЯ ПРО ВИТРЕБУВАННЯ ДОКАЗІВ",
    "motion_expert": "КЛОПОТАННЯ ПРО ПРИЗНАЧЕННЯ ЕКСПЕРТИЗИ",
}


class GeneratorAgentV2:
    """Агент 4: генерує документ і перевіряє відповідність процесуальному кодексу."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client
        self.compliance_checker = ComplianceChecker(claude_client)

    def generate(
        self,
        intake: IntakeResult,
        fees: FeesCalculation,
        critic_review: CriticReview,
        analysis: AnalysisReport | None = None,
        expert_feedback: ExpertReview | None = None,
        case_parties: dict | None = None,
        case_number: str = "",
        iteration: int = 0,
    ) -> tuple[GeneratedDocumentV2, CacheStats, CacheStats]:
        """
        Генерує документ і перевіряє його на відповідність.

        Повертає: (GeneratedDocumentV2, generation_stats, compliance_stats)
        """
        dynamic_system = self._build_dynamic_system(iteration, critic_review, expert_feedback)
        user_message = self._build_user_message(
            intake, fees, critic_review, analysis, case_parties or {}, case_number
        )

        logger.info(f"[Agent4] Ітерація {iteration}: генерую документ типу {intake.recommended_doc_type}")

        from shared.tools import GENERATOR_TOOLS, GENERATOR_HANDLERS
        raw_text, gen_stats = self.claude.run_agent(
            cached_system=_CACHED_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            tools=GENERATOR_TOOLS,
            tool_handlers=GENERATOR_HANDLERS,
            label=f"Agent4-gen-iter{iteration}",
        )

        logger.info(f"[Agent4] Документ згенеровано ({len(raw_text)} символів)")

        # Перевірка відповідності
        compliance, comp_stats = self.compliance_checker.check(
            document_text=raw_text,
            procedural_code=intake.procedural_code,
            iteration=iteration,
        )

        fees_summary = (
            f"Судовий збір: {fees.fee_amount:.2f} грн ({fees.fee_basis}). "
            f"Підсудність: {fees.court_jurisdiction}."
        )

        result = GeneratedDocumentV2(
            content=raw_text,
            doc_type=intake.recommended_doc_type,
            compliance=compliance,
            fees_summary=fees_summary,
            generation_iteration=iteration,
        )
        return result, gen_stats, comp_stats

    def build_docx(
        self,
        generated: GeneratedDocumentV2,
        case_parties: dict,
        case_number: str,
        lawyer_name: str = "",
    ) -> str:
        """Зберігає документ у .docx через існуючий DocxBuilder."""
        from shared.models import AnalysisReport, CaseDescription, DocumentRequest
        from agent3_writer.docx_builder import DocxBuilder

        # Мінімальний AnalysisReport для DocxBuilder (він зберігає тільки parties)
        dummy_case = CaseDescription(
            category="civil",
            subject=case_number,
            key_facts="",
            desired_outcome="",
            court_level="first",
        )
        dummy_report = AnalysisReport(case_description=dummy_case)
        doc_request = DocumentRequest(
            document_type=generated.doc_type,
            analysis_report=dummy_report,
            case_parties=case_parties,
            case_number=case_number,
            lawyer_name=lawyer_name or None,
        )
        builder = DocxBuilder()
        path = builder.build(generated.content, doc_request)
        return path

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dynamic_system(
        iteration: int,
        critic_review: CriticReview,
        expert_feedback: ExpertReview | None,
    ) -> str:
        parts = [f"Ітерація генерації: {iteration}."]

        if critic_review.objections:
            parts.append(
                "Критик виявив такі заперечення (ОБОВ'ЯЗКОВО спростувати в тексті):\n"
                + "\n".join(f"  — {o}" for o in critic_review.objections)
            )
        if critic_review.suggestions:
            parts.append(
                "Пропозиції критика:\n"
                + "\n".join(f"  — {s}" for s in critic_review.suggestions)
            )
        if critic_review.missing_evidence:
            parts.append(
                "Відсутні докази (додати клопотання або [___]):\n"
                + "\n".join(f"  — {e}" for e in critic_review.missing_evidence)
            )

        if expert_feedback and expert_feedback.mandatory_fixes:
            parts.append(
                f"Обов'язкові правки від Експерта (оцінка: {expert_feedback.total_score:.1f}/10):\n"
                + "\n".join(f"  — {f}" for f in expert_feedback.mandatory_fixes)
            )

        return "\n\n".join(parts)

    @staticmethod
    def _build_user_message(
        intake: IntakeResult,
        fees: FeesCalculation,
        critic_review: CriticReview,
        analysis: AnalysisReport | None,
        case_parties: dict,
        case_number: str,
    ) -> str:
        case = intake.case_description
        doc_title = DOCUMENT_TITLES_UK.get(intake.recommended_doc_type, "ПРОЦЕСУАЛЬНИЙ ДОКУМЕНТ")

        plaintiff = case_parties.get("plaintiff", "[Позивач]")
        plaintiff_details = case_parties.get("plaintiff_details", "[адреса, РНОКПП]")
        defendant = case_parties.get("defendant", "[Відповідач]")
        defendant_details = case_parties.get("defendant_details", "[адреса]")
        court = case_parties.get("court", fees.court_jurisdiction or "[назва суду]")

        claims_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(intake.identified_claims))
        legal_text = "\n".join(f"  — {l}" for l in intake.legal_basis_detected)

        lines = [
            f"## ДАНІ ДЛЯ ГЕНЕРАЦІЇ ДОКУМЕНТА: «{doc_title}»",
            "",
            "### РЕКВІЗИТИ СТОРІН",
            f"Номер справи: {case_number or '[___]'}",
            f"Суд: {court}",
            f"Позивач: {plaintiff}",
            f"Деталі позивача: {plaintiff_details}",
            f"Відповідач: {defendant}",
            f"Деталі відповідача: {defendant_details}",
            "",
            "### ПРАВОВА ПОЗИЦІЯ",
            f"Тип справи: {intake.case_type} ({intake.procedural_code})",
            f"Предмет спору: {case.subject}",
            f"Ключові факти: {case.key_facts}",
            f"Бажаний результат: {case.desired_outcome}",
            "",
            "Позовні вимоги:",
            claims_text or "  [визначити вимоги]",
            "",
            "Правові підстави:",
            legal_text or "  [визначити підстави]",
            "",
            "### СУДОВИЙ ЗБІР",
            f"Тип вимог: {fees.claim_type}",
            f"Розмір збору: {fees.fee_amount:.2f} грн ({fees.fee_basis})",
            f"Підсудність: {fees.court_jurisdiction}",
        ]

        if analysis:
            laws = ", ".join(analysis.cited_laws[:10]) or "—"
            strategy = analysis.recommended_strategy[:800] if analysis.recommended_strategy else "—"
            evidence = "\n".join(f"  — {e}" for e in analysis.required_evidence[:7])
            counter = "\n".join(f"  — {a}" for a in analysis.counter_arguments[:4])
            damage = analysis.damage_calculation_method

            lines += [
                "",
                "### СУДОВА ПРАКТИКА ТА АНАЛІЗ",
                f"Стратегія: {strategy}",
                f"Норми права: {laws}",
                "Необхідні докази:",
                evidence or "  —",
                "Контраргументи (спростувати в тексті):",
                counter or "  —",
            ]
            if damage:
                lines += ["", f"Методологія розрахунку збитків: {damage}"]

        lines += [
            "",
            "### ЗАВДАННЯ",
            f"Склади повний текст документа «{doc_title}».",
            "Дотримуйся структури: шапка → назва → вступ → обставини → правове обґрунтування "
            "→ прохальна частина → перелік додатків → підпис.",
            "Включи у мотивувальну частину спростування заперечень, зазначених критиком.",
            "Документ має бути готовий до подання до суду без змін (крім місць [___]).",
        ]
        return "\n".join(lines)
