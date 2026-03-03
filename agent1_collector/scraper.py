"""
Асинхронний Playwright-скрапер для https://reyestr.court.gov.ua/
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeout

from agent1_collector.filters import SearchFilters
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://reyestr.court.gov.ua"
SEARCH_URL = f"{BASE_URL}/search"


class CourtScraper:
    def __init__(self):
        self._context: Optional[BrowserContext] = None
        self._cookies_file = Path(settings.RAW_DATA_PATH) / "session_cookies.json"
        self._delay = settings.SCRAPE_DELAY_SECONDS

    # ------------------------------------------------------------------
    # Управління браузером
    # ------------------------------------------------------------------

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        browser = await self._playwright.chromium.launch(
            headless=settings.HEADLESS_BROWSER,
        )
        self._context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="uk-UA",
        )
        await self._load_cookies()
        return self

    async def __aexit__(self, *args):
        await self._save_cookies()
        await self._context.browser.close()
        await self._playwright.stop()

    async def _load_cookies(self) -> None:
        if self._cookies_file.exists():
            try:
                cookies = json.loads(self._cookies_file.read_text(encoding="utf-8"))
                await self._context.add_cookies(cookies)
                logger.info("Завантажено збережені cookies")
            except Exception as e:
                logger.warning(f"Не вдалося завантажити cookies: {e}")

    async def _save_cookies(self) -> None:
        try:
            cookies = await self._context.cookies()
            self._cookies_file.parent.mkdir(parents=True, exist_ok=True)
            self._cookies_file.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Не вдалося зберегти cookies: {e}")

    # ------------------------------------------------------------------
    # Публічний API
    # ------------------------------------------------------------------

    async def search(
        self,
        filters: SearchFilters,
    ) -> list[dict]:
        """Пошук рішень за фільтрами, повертає список мета-даних"""
        page = await self._context.new_page()
        results: list[dict] = []
        current_page = 1

        try:
            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
            await self._apply_filters(page, filters)

            while len(results) < filters.max_results:
                items = await self._extract_list_items(page)
                if not items:
                    break
                results.extend(items)
                logger.info(f"Сторінка {current_page}: зібрано {len(items)} рішень (всього {len(results)})")

                if len(results) >= filters.max_results:
                    break
                if not await self._go_to_next_page(page):
                    break
                current_page += 1
                await self._sleep()

        except PWTimeout:
            logger.error("Timeout при завантаженні сторінки пошуку")
        except Exception as e:
            logger.error(f"Помилка пошуку: {e}")
        finally:
            await page.close()

        return results[: filters.max_results]

    async def get_decision_text(self, decision_id: str, url: str) -> str:
        """Отримати повний текст рішення за URL"""
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            await self._sleep()
            text = await page.inner_text("body")
            return text
        except PWTimeout:
            logger.warning(f"Timeout для рішення {decision_id}")
            return ""
        except Exception as e:
            logger.error(f"Помилка отримання тексту {decision_id}: {e}")
            return ""
        finally:
            await page.close()

    async def scrape_batch(self, filters: SearchFilters) -> list[dict]:
        """Повний збір: пошук + завантаження текстів"""
        meta_list = await self.search(filters)
        decisions: list[dict] = []

        for i, meta in enumerate(meta_list, 1):
            logger.info(f"[{i}/{len(meta_list)}] Завантаження: {meta.get('registry_number', meta.get('id', ''))}")
            try:
                full_text = await self.get_decision_text(meta["id"], meta["url"])
                meta["full_text"] = full_text
                decisions.append(meta)
            except Exception as e:
                logger.error(f"Помилка для {meta.get('id')}: {e}")
            await self._sleep()

        logger.info(f"Зібрано {len(decisions)} рішень")
        return decisions

    # ------------------------------------------------------------------
    # Допоміжні методи
    # ------------------------------------------------------------------

    async def _apply_filters(self, page: Page, filters: SearchFilters) -> None:
        """Заповнити форму пошуку на сайті"""
        try:
            params = filters.to_query_params()
            # Дата "від"
            date_from_input = page.locator("input[name='date_from'], input[placeholder*='від'], #dateFrom")
            if await date_from_input.count():
                await date_from_input.first.fill(params.get("date_from", ""))

            # Дата "до"
            date_to_input = page.locator("input[name='date_to'], input[placeholder*='до'], #dateTo")
            if await date_to_input.count():
                await date_to_input.first.fill(params.get("date_to", ""))

            # Ключові слова
            if "text" in params:
                text_input = page.locator("input[name='text'], input[name='q'], #searchText")
                if await text_input.count():
                    await text_input.first.fill(params["text"])

            # Кнопка пошуку
            search_btn = page.locator("button[type='submit'], input[type='submit'], .search-btn")
            if await search_btn.count():
                await search_btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception as e:
            logger.warning(f"Помилка застосування фільтрів: {e}")

    async def _extract_list_items(self, page: Page) -> list[dict]:
        """Витягти список рішень з поточної сторінки результатів"""
        items: list[dict] = []
        try:
            # Типові селектори реєстру
            rows = page.locator(".result-item, tr.decision-row, .search-result-item")
            count = await rows.count()

            for i in range(count):
                row = rows.nth(i)
                try:
                    link = row.locator("a").first
                    href = await link.get_attribute("href") or ""
                    text = await link.inner_text()

                    # Витягуємо ID з URL
                    decision_id = href.split("/")[-1].strip() if href else f"unknown_{i}"

                    items.append({
                        "id": decision_id,
                        "registry_number": text.strip(),
                        "url": f"{BASE_URL}{href}" if href.startswith("/") else href,
                        "full_text": "",
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Помилка парсингу списку: {e}")
        return items

    async def _go_to_next_page(self, page: Page) -> bool:
        """Перейти на наступну сторінку. Повертає False якщо наступної немає."""
        try:
            next_btn = page.locator("a.next, .pagination .next, [aria-label='Наступна']")
            if await next_btn.count() and await next_btn.first.is_enabled():
                await next_btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=15_000)
                return True
        except Exception:
            pass
        return False

    async def _sleep(self) -> None:
        """Затримка між запитами для дотримання rate limit"""
        await asyncio.sleep(self._delay)

    async def _sleep_backoff(self, attempt: int) -> None:
        """Exponential backoff при rate limiting"""
        delay = min(self._delay * (2 ** attempt), 60)
        logger.warning(f"Rate limit — очікування {delay:.1f}с")
        await asyncio.sleep(delay)
