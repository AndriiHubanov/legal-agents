"""
Генерація тексту процесуальних документів через Claude
"""
from shared.claude_client import ClaudeClient
from shared.logger import get_logger
from shared.models import AnalysisReport, CourtDecision, DocumentRequest

from agent3_writer.templates.appeal import SYSTEM_PROMPT as APPEAL_SYSTEM, get_appeal_prompt
from agent3_writer.templates.cassation import SYSTEM_PROMPT as CASSATION_SYSTEM, get_cassation_prompt
from agent3_writer.templates.objection import SYSTEM_PROMPT as OBJECTION_SYSTEM, get_objection_prompt
from agent3_writer.templates.motion import get_motion_prompt

logger = get_logger(__name__)


class DocumentGenerator:
    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def generate(self, request: DocumentRequest) -> str:
        """
        Генерує текст процесуального документа.
        Повертає рядок з готовим текстом документа.
        """
        case_data = self.format_case_data(request)
        decisions_text = self._format_decisions(request.analysis_report.relevant_decisions)

        doc_type = request.document_type.lower()
        logger.info(f"Генерую документ типу '{doc_type}' для справи {request.case_number}")

        if doc_type == "appeal":
            system = APPEAL_SYSTEM
            user = get_appeal_prompt(case_data, decisions_text)

        elif doc_type == "cassation":
            system = CASSATION_SYSTEM
            user = get_cassation_prompt(case_data, decisions_text)

        elif doc_type == "objection":
            system = OBJECTION_SYSTEM
            user = get_objection_prompt(case_data, decisions_text)

        elif doc_type.startswith("motion"):
            # motion_security / motion_restore_deadline / motion_evidence / ...
            motion_subtype = doc_type.replace("motion_", "").replace("motion", "security")
            system, user = get_motion_prompt(motion_subtype, case_data)

        else:
            raise ValueError(f"Невідомий тип документа: {doc_type!r}")

        text = self.claude.analyze(system, user)
        logger.info(f"Документ згенеровано ({len(text)} символів)")
        return text

    def format_case_data(self, request: DocumentRequest) -> str:
        """Формує структурований опис справи для prompt"""
        case = request.analysis_report.case_description
        parties = request.case_parties
        lines = [
            f"Номер справи: {request.case_number}",
            f"Тип документа: {request.document_type}",
            f"",
            f"СТОРОНИ:",
            f"  Позивач/Апелянт: {parties.get('plaintiff', '—')}",
            f"  Відповідач: {parties.get('defendant', '—')}",
            f"  Суд: {parties.get('court', '—')}",
            f"",
            f"СПРАВА:",
            f"  Категорія: {case.category}",
            f"  Предмет: {case.subject}",
            f"  Ключові факти: {case.key_facts}",
            f"  Бажаний результат: {case.desired_outcome}",
        ]
        if case.opposing_arguments:
            lines.append(f"  Аргументи опонента: {case.opposing_arguments}")
        if request.deadline:
            lines.append(f"  Строк подання: {request.deadline}")

        lines += [
            f"",
            f"РЕЗУЛЬТАТ АНАЛІЗУ:",
            f"  Стратегія: {request.analysis_report.recommended_strategy}",
            f"",
            f"  Правові аргументи:",
        ]
        for i, arg in enumerate(request.analysis_report.legal_arguments, 1):
            lines.append(f"    {i}. {arg}")

        lines.append(f"  Контраргументи опонента:")
        for i, arg in enumerate(request.analysis_report.counter_arguments, 1):
            lines.append(f"    {i}. {arg}")

        if request.lawyer_name:
            lines.append(f"  Адвокат: {request.lawyer_name}")

        return "\n".join(lines)

    @staticmethod
    def _format_decisions(decisions: list[CourtDecision]) -> str:
        """Форматує список рішень для включення у prompt"""
        if not decisions:
            return "Рішення не знайдено."
        lines = []
        for i, d in enumerate(decisions, 1):
            positions = "\n".join(f"    - {p}" for p in d.legal_positions[:3]) or "    —"
            lines.append(
                f"{i}. Справа №{d.registry_number}\n"
                f"   Суд: {d.court_name} | Дата: {d.decision_date} | Результат: {d.result}\n"
                f"   Предмет: {d.subject[:250]}\n"
                f"   Правові позиції:\n{positions}\n"
                f"   Посилання: {d.url}"
            )
        return "\n\n".join(lines)
