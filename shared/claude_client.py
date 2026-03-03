import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)


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
