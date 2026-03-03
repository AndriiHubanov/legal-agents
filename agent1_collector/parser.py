"""
Парсинг HTML рішень → структуровані дані
"""
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
        # Пошук через типовий шаблон номера справи
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


def extract_legal_positions(full_text: str, claude_client) -> list[str]:
    """
    Використовує Claude для витягу ключових правових позицій.
    Повертає список з 3–5 позицій.
    """
    system_prompt = (
        "Ти юридичний аналітик. Витягни 3-5 ключових правових позицій "
        "з цього судового рішення. Відповідай українською. "
        "Формат відповіді: нумерований список позицій, кожна на новому рядку. "
        "Тільки список, без вступу та коментарів."
    )
    # Обрізаємо текст до ~8000 символів для економії токенів
    trimmed = full_text[:8000]
    try:
        response = claude_client.analyze(system_prompt, trimmed)
        positions = []
        for line in response.splitlines():
            line = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            if line and len(line) > 10:
                positions.append(line)
        return positions[:5]
    except Exception as e:
        logger.error(f"Помилка витягу правових позицій: {e}")
        return []


def normalize_court_name(raw_name: str) -> str:
    """Нормалізує назву суду до стандартного формату"""
    name = raw_name.strip()
    lower = name.lower()
    for key, normalized in COURT_NAME_MAP.items():
        if key in lower:
            return normalized
    # Прибираємо зайві пробіли
    return re.sub(r"\s+", " ", name)


def detect_decision_result(text: str) -> str:
    """
    Визначає результат рішення за ключовими словами у тексті.
    Пріоритет: частково задоволено > задоволено > відмовлено > закрито > ...
    """
    lower = text.lower()
    # Перевіряємо від найконкретнішого до загального
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
