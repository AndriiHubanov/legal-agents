from pydantic import BaseModel
from typing import Optional
from datetime import date


CATEGORIES = {
    "civil": "Цивільні справи",
    "admin": "Адміністративні справи",
    "commercial": "Господарські справи",
    "criminal": "Кримінальні справи",
    "labor": "Трудові спори",
}

COURT_LEVELS = {
    "first": "Суди першої інстанції",
    "appeal": "Апеляційні суди",
    "cassation": "Суди касаційної інстанції (ВС)",
}

REGIONS = [
    "Вінницька", "Волинська", "Дніпропетровська", "Донецька",
    "Житомирська", "Закарпатська", "Запорізька", "Івано-Франківська",
    "Київська", "Кіровоградська", "Луганська", "Львівська",
    "Миколаївська", "Одеська", "Полтавська", "Рівненська",
    "Сумська", "Тернопільська", "Харківська", "Херсонська",
    "Хмельницька", "Черкаська", "Чернівецька", "Чернігівська",
    "м. Київ",
]


class SearchFilters(BaseModel):
    category: str
    date_from: date
    date_to: date
    court_level: Optional[str] = None
    region: Optional[str] = None
    keywords: Optional[list[str]] = None
    max_results: int = 50

    def to_query_params(self) -> dict:
        """Конвертувати фільтри у параметри запиту для reyestr.court.gov.ua"""
        params: dict = {}
        if self.category in CATEGORIES:
            params["category"] = CATEGORIES[self.category]
        params["date_from"] = self.date_from.isoformat()
        params["date_to"] = self.date_to.isoformat()
        if self.court_level and self.court_level in COURT_LEVELS:
            params["court_level"] = COURT_LEVELS[self.court_level]
        if self.region:
            params["region"] = self.region
        if self.keywords:
            params["text"] = " ".join(self.keywords)
        return params
