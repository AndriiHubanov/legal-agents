"""
Перевірка документа на відповідність вимогам процесуальних кодексів.
Використовується Агентом 4 після генерації тексту документа.
"""
import json
import re
from shared.claude_client import ClaudeClient, CacheStats
from shared.legal_texts import CPC_REQUIREMENTS_TEXT, ADMIN_CODE_TEXT, COMMERCIAL_CODE_TEXT
from shared.logger import get_logger
from shared.models import ComplianceResult

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Обов'язкові елементи для кожного кодексу (верифікаційний checklist)
# ---------------------------------------------------------------------------

_REQUIRED_ELEMENTS: dict[str, list[str]] = {
    "ЦПК": [
        "найменування суду",
        "ПІБ або найменування позивача",
        "місце проживання позивача",
        "ПІБ або найменування відповідача",
        "місце проживання відповідача",
        "зміст позовних вимог",
        "обставини справи",
        "докази",
        "ціна позову або немайновий характер",
        "підпис та дата",
        "перелік додатків",
    ],
    "КАС": [
        "найменування адміністративного суду",
        "ім'я позивача",
        "місцезнаходження позивача",
        "ім'я відповідача",
        "зміст позовних вимог",
        "обставини справи",
        "посилання на порушені норми права",
        "підпис та дата",
        "перелік додатків",
    ],
    "ГПК": [
        "найменування господарського суду",
        "найменування позивача",
        "ЄДРПОУ позивача",
        "найменування відповідача",
        "зміст позовних вимог",
        "обставини справи",
        "правове обґрунтування",
        "ціна позову",
        "підпис та дата",
        "перелік додатків",
    ],
}

# ---------------------------------------------------------------------------
# Кешований системний промт для перевірки відповідності
# ---------------------------------------------------------------------------

_CACHED_COMPLIANCE_SYSTEM = (
    """
Ти — юридичний редактор-верифікатор. Твоє завдання: перевірити текст процесуального
документа на відповідність вимогам процесуального кодексу України.

Перевір наявність КОЖНОГО обов'язкового елемента у наданому тексті.
Не аналізуй якість аргументації — тільки формальні реквізити та структуру.

═══ ФОРМАТ ВІДПОВІДІ ═══
Відповідай ВИКЛЮЧНО у форматі JSON:
{
  "procedural_code": "ЦПК",
  "required_elements": {
    "найменування суду": true,
    "ПІБ або найменування позивача": true,
    "місце проживання позивача": false,
    "...": true
  },
  "violations": [
    "Відсутнє місце проживання позивача (обов'язково — ст.175 ч.2 п.2 ЦПК)",
    "Не вказано РНОКПП позивача (ст.175 ч.2 п.2 ЦПК)"
  ],
  "warnings": [
    "Бажано додати контактний номер телефону та email позивача (ст.175 ч.4 ЦПК)"
  ],
  "is_compliant": false,
  "compliance_score": 7.5
}

Поле compliance_score: 0–10 (10 = всі обов'язкові елементи присутні).
Мова: ТІЛЬКИ українська.
"""
    + "\n\n"
    + CPC_REQUIREMENTS_TEXT
    + "\n\n"
    + ADMIN_CODE_TEXT
    + "\n\n"
    + COMMERCIAL_CODE_TEXT
)


class ComplianceChecker:
    """Перевіряє документ на відповідність вимогам процесуального кодексу."""

    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def check(
        self,
        document_text: str,
        procedural_code: str,
        iteration: int = 0,
    ) -> tuple[ComplianceResult, CacheStats]:
        """
        Перевіряє текст документа на відповідність вимогам.

        document_text    — повний текст згенерованого документа.
        procedural_code  — "ЦПК" / "КАС" / "ГПК".
        """
        required = _REQUIRED_ELEMENTS.get(procedural_code, _REQUIRED_ELEMENTS["ЦПК"])
        elements_list = "\n".join(f"  — {el}" for el in required)

        dynamic_system = (
            f"Перевіряємо документ за {procedural_code}. "
            f"Ітерація генерації: {iteration}.\n\n"
            f"Обов'язкові елементи для {procedural_code}:\n{elements_list}"
        )

        user_message = (
            f"## ТЕКСТ ДОКУМЕНТА ДЛЯ ПЕРЕВІРКИ ({procedural_code})\n\n"
            f"{document_text}\n\n"
            "## ЗАВДАННЯ\n"
            f"Перевір документ на наявність усіх обов'язкових елементів за {procedural_code}.\n"
            "Поверни результат у форматі JSON."
        )

        logger.info(f"[Compliance] Перевіряю документ ({len(document_text)} символів) за {procedural_code}")

        raw_response, stats = self.claude.analyze_cached(
            cached_system=_CACHED_COMPLIANCE_SYSTEM,
            dynamic_system=dynamic_system,
            user_message=user_message,
            label=f"Compliance-{procedural_code}-iter{iteration}",
        )

        result = self._parse_response(raw_response, procedural_code, required)
        logger.info(
            f"[Compliance] Результат: compliant={result.is_compliant}, "
            f"score={result.compliance_score:.1f}, "
            f"violations={len(result.violations)}"
        )
        return result, stats

    @staticmethod
    def _parse_response(
        raw: str,
        procedural_code: str,
        required: list[str],
    ) -> ComplianceResult:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()
        obj_match = re.search(r"\{[\s\S]+\}", json_str)
        if obj_match:
            json_str = obj_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[Compliance] Не вдалося розпарсити JSON — fallback")
            data = {}

        elements = data.get("required_elements", {el: False for el in required})

        return ComplianceResult(
            procedural_code=data.get("procedural_code", procedural_code),
            required_elements=elements,
            violations=data.get("violations", []),
            warnings=data.get("warnings", []),
            is_compliant=bool(data.get("is_compliant", False)),
            compliance_score=float(data.get("compliance_score", 5.0)),
        )
