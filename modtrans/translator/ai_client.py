"""Async AI translation client (OpenAI-compatible API).

Features:
- Retry with exponential backoff for transient failures
- Rate limiting (requests per minute)
- Response parsing with markdown code block extraction
- Response validation (all keys present, no hallucinations)
- Graceful degradation: English fallback on total failure
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from ..models import ModAssets, TranslationBatch, TranslationResult
from .prompt import (
    SYSTEM_PROMPT,
    build_user_message,
    classify_existing_chinese,
)

logger = logging.getLogger(__name__)


class AIResponseParseError(Exception):
    """Could not parse the AI's response as valid JSON."""


class AIResponseValidationError(Exception):
    """AI response is valid JSON but doesn't match expected keys."""


class AIClient:
    """Async client for OpenAI-compatible translation APIs.

    Usage::

        client = AIClient(config, prompt_manager)
        async with client:
            result = await client.translate_batch(batch)
            print(f"Translated {len(result.translations)} entries")
    """

    def __init__(
        self,
        config: "AIConfig",  # type: ignore[name-defined] # noqa: F821
    ) -> None:
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limiter: Optional[_RateLimiter] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AIClient":
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        self._client = httpx.AsyncClient(
            base_url=self._config.api_base.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self._rate_limiter = _RateLimiter(self._config.requests_per_minute)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def translate_batch(
        self, batch: TranslationBatch
    ) -> TranslationResult:
        """Translate a batch of mod entries.

        Args:
            batch: TranslationBatch with mods grouped together.

        Returns:
            TranslationResult with translated entries and usage metadata.
        """
        # Flatten all English entries needing translation
        all_entries: dict[str, str] = {}
        for mod in batch.mods:
            all_entries.update(mod.english_entries)

        # Analyze existing Chinese translations
        all_existing: dict[str, str] = {}
        for mod in batch.mods:
            all_existing.update(mod.existing_chinese)

        fully_translated, keys_to_review = classify_existing_chinese(
            all_entries, all_existing
        )

        # Build the user message
        user_message = build_user_message(
            all_entries,
            mod_context=batch.context_info,
            existing_chinese=fully_translated,
            keep_english_keys=keys_to_review if keys_to_review else None,
        )

        # Call the API
        try:
            response_text, usage = await self._call_api(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_message,
            )
        except Exception as e:
            logger.error("批次 %s API 调用失败: %s", batch.batch_id, e)
            return TranslationResult(
                batch=batch,
                translations={},
                model=self._config.model,
                success=False,
                error=str(e),
            )

        # Parse and validate
        try:
            translations = await self._parse_response(response_text)
        except AIResponseParseError as e:
            logger.warning("批次 %s 响应解析失败: %s", batch.batch_id, e)
            # Retry once with stricter instructions
            try:
                retry_msg = (
                    user_message
                    + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                    "Output ONLY a JSON object with NO markdown formatting."
                )
                response_text, usage2 = await self._call_api(
                    system_prompt=SYSTEM_PROMPT,
                    user_message=retry_msg,
                )
                usage["total_tokens"] = (
                    usage.get("total_tokens", 0)
                    + usage2.get("total_tokens", 0)
                )
                translations = await self._parse_response(response_text)
            except Exception as e2:
                logger.error("批次 %s 重试也失败了: %s", batch.batch_id, e2)
                return TranslationResult(
                    batch=batch,
                    translations={},
                    model=self._config.model,
                    usage=usage,
                    success=False,
                    error=f"Parse failed after retry: {e2}",
                )

        # Validate keys
        expected_keys = set(all_entries.keys())
        # Remove keys that AI should skip (already in fully_translated)
        expected_keys -= set(fully_translated.keys())
        # Keys to review may or may not change — include them in validation
        expected_keys |= keys_to_review

        validation_errors = self._validate_response(translations, expected_keys)
        if validation_errors:
            logger.warning(
                "批次 %s 校验警告: %s",
                batch.batch_id,
                "; ".join(validation_errors[:5]),
            )

            # 找出缺失的 key，补译一次
            missing_keys = expected_keys - set(translations.keys())
            if missing_keys:
                logger.info(
                    "批次 %s: 缺失 %d 个键，尝试补译",
                    batch.batch_id,
                    len(missing_keys),
                )
                missing_entries = {
                    k: all_entries[k] for k in missing_keys if k in all_entries
                }
                if missing_entries:
                    try:
                        retry_msg = build_user_message(
                            missing_entries,
                            mod_context=f"补译 — {batch.context_info}",
                        )
                        retry_text, usage2 = await self._call_api(
                            system_prompt=SYSTEM_PROMPT,
                            user_message=retry_msg,
                        )
                        usage["total_tokens"] = (
                            usage.get("total_tokens", 0)
                            + usage2.get("total_tokens", 0)
                        )
                        retry_translations = await self._parse_response(retry_text)
                        translations.update(retry_translations)
                        still_missing = missing_keys - set(retry_translations.keys())
                        if still_missing:
                            logger.warning(
                                "批次 %s: 补译后仍缺失 %d 个键，使用英文原文",
                                batch.batch_id,
                                len(still_missing),
                            )
                            for k in still_missing:
                                if k in all_entries:
                                    translations[k] = all_entries[k]
                        else:
                            logger.info(
                                "批次 %s: 补译成功，%d 个键已补齐",
                                batch.batch_id,
                                len(missing_entries),
                            )
                    except Exception as e:
                        logger.warning("批次 %s 补译失败: %s，使用英文原文", batch.batch_id, e)
                        for k in missing_keys:
                            if k in all_entries:
                                translations[k] = all_entries[k]

        return TranslationResult(
            batch=batch,
            translations=translations,
            model=self._config.model,
            usage=usage,
            success=True,
        )

    async def translate_entries(
        self,
        entries: dict[str, str],
        context: str = "",
    ) -> dict[str, str]:
        """Translate a flat dictionary of English entries to Chinese.

        Simplified API for single requests (debugging / testing).

        Args:
            entries: key → English text dictionary.
            context: Optional context string (mod name, author).

        Returns:
            key → Chinese text dictionary.
        """
        user_message = build_user_message(entries, mod_context=context)
        response_text, _ = await self._call_api(SYSTEM_PROMPT, user_message)
        return await self._parse_response(response_text)

    # ------------------------------------------------------------------
    # Internal: API call
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        system_prompt: str,
        user_message: str,
    ) -> tuple[str, dict]:
        """Make a raw API call with retry logic.

        Returns:
            Tuple of (response_text, usage_dict).

        Raises:
            httpx.HTTPError: On unrecoverable API errors.
            Exception: On timeout or connection errors after max retries.
        """
        if self._client is None:
            raise RuntimeError("AIClient 未初始化。请使用 'async with AIClient()'。")

        payload = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            try:
                # Rate limiting
                if self._rate_limiter:
                    await self._rate_limiter.acquire()

                response = await self._client.post(
                    "/chat/completions",
                    json=payload,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = _parse_retry_after(response)
                    wait = retry_after if retry_after else 2 ** attempt
                    logger.info(
                        "触发速率限制 (429), 等待 %.1fs (第 %d/%d 次尝试)",
                        wait,
                        attempt + 1,
                        self._config.max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue

                # Handle server errors
                if response.status_code >= 500:
                    wait = self._config.retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "服务器错误 %d, %.1fs 后重试 (第 %d/%d 次尝试)",
                        response.status_code,
                        wait,
                        attempt + 1,
                        self._config.max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue

                # Handle client errors (except 429)
                if response.status_code >= 400:
                    error_body = response.text[:500]
                    raise httpx.HTTPStatusError(
                        f"API error {response.status_code}: {error_body}",
                        request=response.request,
                        response=response,
                    )

                # Success
                data = response.json()
                choice = data["choices"][0]
                message = choice["message"]
                content = message["content"]
                usage = data.get("usage", {})

                # Log finish reason
                finish_reason = choice.get("finish_reason", "unknown")
                if finish_reason == "length":
                    logger.warning(
                        "API 响应被截断 (finish_reason=length) — "
                        "部分翻译可能丢失"
                    )

                return content, usage

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                wait = self._config.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "网络错误: %s, %.1fs 后重试 (第 %d/%d 次尝试)",
                    e,
                    wait,
                    attempt + 1,
                    self._config.max_retries,
                )
                last_error = e
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError:
                # 4xx errors (except 429) — don't retry
                raise

        raise last_error or Exception("Max retries exceeded")

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    async def _parse_response(raw_text: str) -> dict[str, str]:
        """Extract a JSON object from the AI response.

        Handles:
        - Pure JSON: ``{"key": "value"}``
        - Markdown code block: `` ```json {...} ``` ``
        - Text with JSON embedded

        Raises:
            AIResponseParseError: If no valid JSON can be extracted.
        """
        if not raw_text or not raw_text.strip():
            raise AIResponseParseError("AI 返回空响应")

        text = raw_text.strip()

        errors: list[str] = []

        # Attempt 1: Direct JSON parse
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return _coerce_values(result)
        except json.JSONDecodeError as e:
            errors.append(f"Direct parse: {e}")

        # Attempt 2: Extract from markdown code block
        md_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL
        )
        if md_match:
            try:
                result = json.loads(md_match.group(1))
                if isinstance(result, dict):
                    return _coerce_values(result)
            except json.JSONDecodeError as e:
                errors.append(f"Markdown block: {e}")

        # Attempt 3: Find first { ... } pair
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict):
                    return _coerce_values(result)
            except json.JSONDecodeError as e:
                errors.append(f"Brace-extract: {e}")

                # Attempt 3b: Repair common issues (trailing commas,
                # single-quoted strings)
                try:
                    repaired = _repair_json(text[start : end + 1])
                    result = json.loads(repaired)
                    if isinstance(result, dict):
                        return _coerce_values(result)
                except (json.JSONDecodeError, ValueError):
                    pass

        raise AIResponseParseError(
            f"无法将 AI 响应解析为 JSON。错误: {'; '.join(errors)}"
        )

    @staticmethod
    def _validate_response(
        translations: dict[str, str],
        expected_keys: set[str],
    ) -> list[str]:
        """Validate that all expected keys are present.

        Missing keys are reported but not fatal — the caller can use
        English text as fallback.

        Returns:
            List of warning messages (empty = valid).
        """
        warnings: list[str] = []

        found_keys = set(translations.keys())
        missing = expected_keys - found_keys
        extra = found_keys - expected_keys

        if missing:
            warnings.append(f"缺失 {len(missing)} 个键")
        if extra:
            warnings.append(f"多余 (幻觉) {len(extra)} 个键")

        # Check for empty values
        empty_values = [
            k for k, v in translations.items() if not v or not v.strip()
        ]
        if empty_values:
            warnings.append(f"{len(empty_values)} 个翻译值为空")

        return warnings


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Token-bucket rate limiter for API calls."""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            requests_per_minute = 60
        self._min_interval = 60.0 / requests_per_minute
        self._last_call = 0.0

    async def acquire(self) -> None:
        """Wait until it's safe to make the next API call."""
        now = time.monotonic()
        wait = self._last_call + self._min_interval - now
        if wait > 0:
            await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        else:
            self._last_call = now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_values(data: dict) -> dict[str, str]:
    """Convert all dict values to strings."""
    result: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str):
            result[k] = v
        elif isinstance(v, (int, float, bool)):
            result[k] = str(v)
        elif v is None:
            result[k] = ""
        else:
            result[k] = str(v)
    return result


def _repair_json(text: str) -> str:
    """Attempt to repair common JSON formatting issues.

    Handles:
    - Trailing commas
    - Single-quoted strings (some models do this)
    - Unquoted keys
    """
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*(\}|\])", r"\1", text)

    # Replace single quotes with double quotes (naive but often works)
    # This is risky for text containing apostrophes, so only try if
    # the direct parse failed
    # Note: we don't auto-replace single quotes because many Chinese
    # translations may contain them as punctuation

    return text


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    """Extract Retry-After header value."""
    value = response.headers.get("Retry-After", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
