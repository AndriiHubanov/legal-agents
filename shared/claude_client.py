import anthropic
from dataclasses import dataclass, field
from typing import Callable
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

    def run_agent(
        self,
        cached_system: str,
        dynamic_system: str,
        user_message: str,
        tools: list[dict],
        tool_handlers: dict[str, Callable[..., str]],
        label: str = "",
        max_tool_calls: int = 10,
    ) -> tuple[str, CacheStats]:
        """
        Agentic loop з Prompt Caching та tool_use (Claude Agent SDK pattern).

        Claude може автономно викликати інструменти для збору даних під час роботи.
        Кожен крок циклу використовує той самий cached_system (ephemeral cache),
        тому повторні виклики в межах 5 хвилин економлять ~90% токенів системного промпту.

        cached_system   — статичний блок (кешується через ephemeral cache)
        dynamic_system  — динамічний контекст (не кешується)
        user_message    — початковий запит
        tools           — JSON-схеми інструментів (tool definitions)
        tool_handlers   — {tool_name: python_callable} — обробники викликів
        max_tool_calls  — ліміт кроків agentic loop (захист від нескінченного циклу)

        Повертає: (фінальний текст відповіді, агреговані CacheStats по всіх кроках)
        """
        system_blocks: list[dict] = [
            {
                "type": "text",
                "text": cached_system,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if dynamic_system.strip():
            system_blocks.append({"type": "text", "text": dynamic_system})

        messages: list[dict] = [{"role": "user", "content": user_message}]
        total_stats = CacheStats()

        logger.info(
            f"[{label or 'agent'}] Запуск agentic loop, "
            f"{len(tools)} інструментів, max_steps={max_tool_calls}"
        )

        for step in range(max_tool_calls):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=settings.MAX_TOKENS,
                system=system_blocks,
                tools=tools,
                messages=messages,
            )

            # Акумулюємо статистику кешу по всіх кроках
            usage = response.usage
            total_stats.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
            total_stats.cache_read_tokens     += getattr(usage, "cache_read_input_tokens", 0) or 0
            total_stats.input_tokens          += getattr(usage, "input_tokens", 0) or 0
            total_stats.output_tokens         += getattr(usage, "output_tokens", 0) or 0

            # Claude завершив — повертаємо текст
            if response.stop_reason == "end_turn":
                text = next((b.text for b in response.content if hasattr(b, "text")), "")
                logger.info(f"[{label}] Завершено за {step + 1} крок(ів)")
                total_stats.log(label)
                return text, total_stats

            # Обробляємо tool_use блоки
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                # stop_reason не end_turn, але і tool_use немає — виходимо
                break

            logger.info(
                f"[{label}] Крок {step + 1}: Claude викликає "
                f"{len(tool_use_blocks)} інструмент(ів): "
                f"{[b.name for b in tool_use_blocks]}"
            )

            # Додаємо відповідь асистента до history
            messages.append({"role": "assistant", "content": response.content})

            # Виконуємо кожен інструмент і формуємо tool_result
            tool_results = []
            for tb in tool_use_blocks:
                handler = tool_handlers.get(tb.name)
                if handler:
                    try:
                        result = handler(**tb.input)
                        logger.info(
                            f"[{label}] Tool '{tb.name}' виконано → {len(str(result))} символів"
                        )
                    except Exception as e:
                        result = f"Помилка виконання інструменту '{tb.name}': {e}"
                        logger.warning(f"[{label}] {result}")
                else:
                    result = f"Інструмент '{tb.name}' не зареєстровано в handlers."
                    logger.warning(f"[{label}] {result}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": str(result),
                })

            messages.append({"role": "user", "content": tool_results})

        # Fallback: повертаємо останній доступний текст
        text = ""
        if response.content:
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
        logger.warning(f"[{label}] Agentic loop завершився примусово після {max_tool_calls} кроків")
        total_stats.log(label)
        return text, total_stats

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
