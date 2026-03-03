"""
Збереження рішень у ChromaDB (векторна БД) та JSON-файли
"""
import json
import uuid
from datetime import date
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from shared.config import settings
from shared.logger import get_logger
from shared.models import CourtDecision

logger = get_logger(__name__)

COLLECTION_NAME = "court_decisions"


class DecisionStorage:
    def __init__(self):
        Path(settings.CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
        Path(settings.RAW_DATA_PATH).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=settings.CHROMA_DB_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"ChromaDB ініціалізовано: {self._collection.count()} рішень у БД")

    # ------------------------------------------------------------------
    # Збереження
    # ------------------------------------------------------------------

    def save_decision(self, decision: CourtDecision) -> str:
        """
        Зберігає рішення у JSON та ChromaDB.
        Повертає embedding_id.
        """
        # 1. JSON на диск
        json_path = Path(settings.RAW_DATA_PATH) / f"{decision.id}.json"
        json_path.write_text(
            decision.model_dump_json(indent=2, default=str),
            encoding="utf-8",
        )

        # 2. Перевірити чи вже є в ChromaDB
        existing = self._collection.get(ids=[decision.id])
        if existing["ids"]:
            logger.debug(f"Рішення {decision.id} вже є в БД, пропускаємо")
            return decision.id

        # 3. Текст для embedding: предмет + правові позиції
        embed_text = self._build_embed_text(decision)

        # 4. Метадані для фільтрації
        metadata = {
            "category": decision.category,
            "court_name": decision.court_name,
            "decision_date": decision.decision_date.isoformat(),
            "result": decision.result,
            "registry_number": decision.registry_number,
            "url": decision.url,
        }

        self._collection.add(
            ids=[decision.id],
            documents=[embed_text],
            metadatas=[metadata],
        )
        logger.info(f"Збережено рішення {decision.registry_number} ({decision.id})")
        return decision.id

    def save_decisions_batch(self, decisions: list[CourtDecision]) -> int:
        """Зберегти пачку рішень, повертає кількість нових"""
        saved = 0
        for decision in decisions:
            try:
                self.save_decision(decision)
                saved += 1
            except Exception as e:
                logger.error(f"Помилка збереження {decision.id}: {e}")
        return saved

    # ------------------------------------------------------------------
    # Пошук
    # ------------------------------------------------------------------

    def search_similar(
        self,
        query_text: str,
        filters: dict | None = None,
        top_k: int = 20,
    ) -> list[CourtDecision]:
        """
        Семантичний пошук у ChromaDB.
        filters: {'category': ..., 'result': ..., 'decision_date_gte': 'YYYY-MM-DD'}
        """
        where: dict = {}
        if filters:
            conditions = []
            if "category" in filters:
                conditions.append({"category": {"$eq": filters["category"]}})
            if "result" in filters:
                conditions.append({"result": {"$eq": filters["result"]}})
            if conditions:
                where = {"$and": conditions} if len(conditions) > 1 else conditions[0]

        query_params: dict = {
            "query_texts": [query_text],
            "n_results": min(top_k, max(1, self._collection.count())),
        }
        if where:
            query_params["where"] = where

        try:
            results = self._collection.query(**query_params)
        except Exception as e:
            logger.error(f"Помилка пошуку в ChromaDB: {e}")
            return []

        decisions: list[CourtDecision] = []
        for doc_id in results["ids"][0]:
            decision = self.load_decision(doc_id)
            if decision:
                decisions.append(decision)
        return decisions

    def load_decision(self, decision_id: str) -> CourtDecision | None:
        """Завантажити рішення з JSON-файлу на диску"""
        json_path = Path(settings.RAW_DATA_PATH) / f"{decision_id}.json"
        if not json_path.exists():
            return None
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return CourtDecision.model_validate(data)
        except Exception as e:
            logger.error(f"Помилка завантаження {decision_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Статистика
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Статистика по БД"""
        total = self._collection.count()
        json_files = list(Path(settings.RAW_DATA_PATH).glob("*.json"))

        # Розподіл по категоріях (з метаданих ChromaDB)
        categories: dict[str, int] = {}
        results_dist: dict[str, int] = {}

        if total > 0:
            all_meta = self._collection.get(include=["metadatas"])
            for meta in all_meta["metadatas"]:
                cat = meta.get("category", "невідомо")
                categories[cat] = categories.get(cat, 0) + 1
                res = meta.get("result", "невідомо")
                results_dist[res] = results_dist.get(res, 0) + 1

        return {
            "total_in_chromadb": total,
            "total_json_files": len(json_files),
            "categories": categories,
            "results_distribution": results_dist,
            "db_path": settings.CHROMA_DB_PATH,
        }

    # ------------------------------------------------------------------
    # Утиліти
    # ------------------------------------------------------------------

    @staticmethod
    def _build_embed_text(decision: CourtDecision) -> str:
        parts = [decision.subject]
        if decision.legal_positions:
            parts.extend(decision.legal_positions)
        return " | ".join(parts)[:2000]
