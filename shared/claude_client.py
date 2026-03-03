import anthropic
from dataclasses import dataclass, field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CacheStats:
    """Статистика використання кешу Prompt Caching"""
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def saved_tokens(self) -> int:
        return self.cache_read_tokens

    @property
    def total_cost_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_tokens

    def log(self, label: str = "") -> None:
        prefix = f"[{label}] " if label else ""
        logger.info(
            f"{prefix}Токени: input={self.input_tokens}, "
            f"cache_write={self.cache_creation_tokens}, "
            f"cache_read={self.cache_read_tokens} (зекономлено ~{self.cache_read_tokens} вх. токенів), "
            f"output={self.output_tokens}"
        )


class ClaudeClient:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.CLAUDE_MODEL

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def analyze(self, system_prompt: str, user_message: str) -> str:
        """Надіслати запит до Claude і отримати відповідь"""
        logger.info(f"Запит до Claude ({self.model}), ~{len(user_message)} символів")
        response = self.client.messages.create(
            model=self.model,
            max_tokens=settings.MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def analyze_cached(
        self,
        cached_system: str,
        dynamic_system: str,
        user_message: str,
        label: str = "",
    ) -> tuple[str, CacheStats]:
        """
        Запит до Claude з Prompt Caching.

        cached_system  — великий статичний блок (тексти кодексів, інструкції агента).
                         Кешується після першого запиту (мін. 1024 токени).
        dynamic_system — невеликий динамічний блок (контекст поточної справи, ітерації).
                         НЕ кешується.
        user_message   — повідомлення користувача (дані конкретного запиту).

        Повертає: (текст відповіді, CacheStats)
        """
        system_blocks: list[dict] = [
            {
                "type": "text",
                "text": cached_system,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if dynamic_system.strip():
            system_blocks.append({
                "type": "text",
                "text": dynamic_system,
            })

        logger.info(
            f"[{label or 'cached'}] Запит до Claude з кешуванням, "
            f"~{len(cached_system)} + {len(dynamic_system)} символів системного промпту"
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=settings.MAX_TOKENS,
            system=system_blocks,
            messages=[{"role": "user", "content": user_message}],
        )

        usage = response.usage
        stats = CacheStats(
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
        stats.log(label)
        return response.content[0].text, stats

    def analyze_with_history(
        self,
        system_prompt: str,
        messages: list[dict],
    ) -> str:
        """Запит з повною історією повідомлень"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=settings.MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
