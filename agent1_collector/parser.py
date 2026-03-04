"""
Парсинг HTML рішень → структуровані дані
"""
import json
import re
from datetime import date, datetime
from bs4 import BeautifulSoup

from shared.logger import get_logger

logger = get_logger(__name__)

RESULT_KEYWORDS = {
    "задоволено": ["задоволено", "задовольнити", "позов задоволен"],
    "відмовлено": ["відмовлено", "відмовити", "в задоволенні відмовити"],
    "частково задоволено": ["частково задоволено", "задоволено частково"],
    "закрито": ["закрито", "закрити провадження", "припинити провадження"],
    "залишено без розгляду": ["залишено без розгляду", "без розгляду залишити"],
}

COURT_NAME_MAP = {
    "вс": "Верховний Суд",
    "верховний суд": "Верховний Суд",
    "вгсу": "Вищий господарський суд України",
    "васу": "Вищий адміністративний суд України",
}

_STRUCTURED_SYSTEM_PROMPT = """Ти аналітик судових рішень України.
Витягни зі судового рішення структуровані дані у форматі JSON.

Поля:
- legal_positions (list[str]): 3-5 ключових правових позицій суду (конкретні висновки)
- cited_laws (list[str]): всі статті нормативних актів у форматі "ст.X НПА" (наприклад "ст.22 ЦК України", "ст.156 ЗК України", "п.3 Порядку КМУ №284")
- damage_amount (float | null): сума збитків у гривнях якщо вказана в рішенні, інакше null
- evidence_types (list[str]): типи доказів, що згадуються (наприклад "акт перевірки Держгеокадастру", "висновок експерта", "протокол")

Відповідай ВИКЛЮЧНО JSON без жодного тексту навколо."""


def parse_decision_page(html: str) -> dict:
    """
    Парсить HTML-сторінку рішення.
    Повертає словник з полями: registry_number, court_name, judge_name,
    decision_date, category, subject, result.
    """
    soup = BeautifulSoup(html, "lxml")
    data: dict = {
        "registry_number": "",
        "court_name": "",
        "judge_name": None,
        "decision_date": date.today(),
        "category": "civil",
        "subject": "",
        "result": "невідомо",
    }

    # Номер справи
    case_number_el = soup.find(class_=re.compile(r"case.?number|registry.?number", re.I))
    if case_number_el:
        data["registry_number"] = case_number_el.get_text(strip=True)
    else:
        match = re.search(r"\d{1,3}-\d{3,6}/\d{4}", html)
        if match:
            data["registry_number"] = match.group(0)

    # Назва суду
    court_el = soup.find(class_=re.compile(r"court.?name|court", re.I))
    if court_el:
        data["court_name"] = normalize_court_name(court_el.get_text(strip=True))

    # Суддя
    judge_el = soup.find(class_=re.compile(r"judge", re.I))
    if judge_el:
        data["judge_name"] = judge_el.get_text(strip=True)

    # Дата
    date_el = soup.find(class_=re.compile(r"decision.?date|date", re.I))
    if date_el:
        data["decision_date"] = _parse_date(date_el.get_text(strip=True))
    else:
        match = re.search(r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})", html)
        if match:
            data["decision_date"] = _parse_date(match.group(0))

    # Предмет (заголовок або перший абзац)
    title_el = soup.find("h1") or soup.find("h2")
    if title_el:
        data["subject"] = title_el.get_text(strip=True)[:500]

    # Результат
    full_text = soup.get_text(separator=" ")
    data["result"] = detect_decision_result(full_text)

    return data


def extract_structured_positions(full_text: str, claude_client) -> dict:
    """
    Використовує Claude для витягу структурованих юридичних даних з рішення.
    Повертає словник з полями:
      - legal_positions (list[str]): 3-5 ключових правових позицій
      - cited_laws (list[str]): конкретні статті законів
      - damage_amount (float | None): сума збитків
      - evidence_types (list[str]): типи доказів
    """
    trimmed = full_text[:10_000]
    try:
        raw = claude_client.analyze(_STRUCTURED_SYSTEM_PROMPT, trimmed)

        # Витягуємо JSON з відповіді
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw.strip()

        if not json_str.startswith("{"):
            obj_match = re.search(r"\{[\s\S]+\}", json_str)
            json_str = obj_match.group(0) if obj_match else json_str

        data = json.loads(json_str)
        return {
            "legal_positions": _clean_list(data.get("legal_positions", [])),
            "cited_laws": _clean_list(data.get("cited_laws", [])),
            "damage_amount": _parse_damage_amount(data.get("damage_amount")),
            "evidence_types": _clean_list(data.get("evidence_types", [])),
        }
    except Exception as e:
        logger.error(f"Помилка extract_structured_positions: {e}")
        return {
            "legal_positions": _fallback_positions(full_text),
            "cited_laws": _extract_laws_regex(full_text),
            "damage_amount": _extract_damage_amount_regex(full_text),
            "evidence_types": [],
        }


def extract_legal_positions(full_text: str, claude_client) -> list[str]:
    """
    Зворотно сумісна обгортка: повертає тільки список правових позицій.
    """
    result = extract_structured_positions(full_text, claude_client)
    return result["legal_positions"]


def normalize_court_name(raw_name: str) -> str:
    """Нормалізує назву суду до стандартного формату"""
    name = raw_name.strip()
    lower = name.lower()
    for key, normalized in COURT_NAME_MAP.items():
        if key in lower:
            return normalized
    return re.sub(r"\s+", " ", name)


def detect_decision_result(text: str) -> str:
    """
    Визначає результат рішення за ключовими словами у тексті.
    Пріоритет: частково задоволено > задоволено > відмовлено > закрито > ...
    """
    lower = text.lower()
    for result, keywords in RESULT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return result
    return "невідомо"


def _parse_date(date_str: str) -> date:
    """Парсить дату з рядка у різних форматах"""
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    logger.warning(f"Не вдалося розпарсити дату: {date_str!r}")
    return date.today()


def _clean_list(items) -> list[str]:
    """Очищує список від порожніх рядків"""
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _parse_damage_amount(value) -> float | None:
    """Перетворює значення суми збитків на float або None"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _fallback_positions(text: str) -> list[str]:
    """Базовий витяг позицій без Claude (regex)"""
    positions = []
    patterns = [
        r"суд(?:ова колегія)?\s+(?:встановив|вважає|зазначає|дійшов)[^\.\!]{20,200}[\.!]",
        r"колегія суддів\s+(?:вважає|зазначає)[^\.\!]{20,200}[\.!]",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            positions.append(m.group(0).strip())
            if len(positions) >= 3:
                break
    return positions[:5]


def _extract_laws_regex(text: str) -> list[str]:
    """Витяг посилань на статті законів через regex (fallback)"""
    pattern = r"(?:ст(?:атт[яі])?\.?\s*\d+(?:[,\s]+\d+)*\s+[А-ЯІЇЄ][А-ЯІЇЄа-яіїє\s]+(?:України)?)"
    found = re.findall(pattern, text)
    cleaned = list({re.sub(r"\s+", " ", f).strip() for f in found})
    return cleaned[:10]


def _extract_damage_amount_regex(text: str) -> float | None:
    """Витяг суми збитків через regex (fallback)"""
    patterns = [
        r"(\d[\d\s]*[\d,\.]+)\s*(?:грн|гривень|гривні)",
        r"збитки.*?(\d[\d\s]*[\d,\.]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                amount_str = m.group(1).replace(" ", "").replace(",", ".")
                return float(amount_str)
            except ValueError:
                continue
    return None
