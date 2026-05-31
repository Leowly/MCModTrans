"""Async AI translation client (OpenAI-compatible API).

Features:
- Retry with exponential backoff for transient failures
- Rate limiting (requests per minute)
- Response parsing with markdown code block extraction
- Response validation (all keys present, no hallucinations)
- Graceful degradation: English fallback on total failure
- Auto-retry on abnormally low response rate (model fluke detection)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from ..models import TranslationBatch, TranslationResult
from .prompt import (
    SYSTEM_PROMPT,
    build_user_message,
)

logger = logging.getLogger(__name__)


class AIResponseParseError(Exception):
    """Could not parse the AI's response as valid JSON."""


class AIResponseValidationError(Exception):
    """AI response is valid JSON but doesn't match expected keys."""


# Threshold: retry if AI returns fewer than this fraction of expected keys
_MIN_RESPONSE_RATIO = 0.3


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

    async def translate_batch(self, batch: TranslationBatch) -> TranslationResult:
        """Translate a batch of mod entries.

        Args:
            batch: TranslationBatch with mods grouped together.

        Returns:
            TranslationResult with translated entries and usage metadata.
        """
        all_entries = dict(batch.entries)

        user_message = build_user_message(
            all_entries,
            mod_context=batch.context_info,
        )

        # Call + parse（异常低回复率自动重试一次）
        translations, usage = await self._call_and_parse(
            SYSTEM_PROMPT, user_message,
            expected=len(all_entries),
            batch_id=batch.batch_id,
        )

        if translations is None:
            logger.error("批次 %s API 调用完全失败", batch.batch_id)
            return TranslationResult(
                batch=batch,
                translations={},
                model=self._config.model,
                success=False,
                error="API 调用或解析失败",
            )

        expected_keys = set(all_entries.keys())

        # 缺失的 key — key 不在 AI 响应中
        missing_keys = expected_keys - set(translations.keys())
        missed_entries = {
            k: all_entries[k] for k in missing_keys if k in all_entries
        }

        # 空值 key — 英文原文有内容但 AI 返回空值，视同缺失，纳入补译
        # 注意：英文原文本身就是空的（如占位 tooltip）不算异常
        empty_keys = {
            k for k, v in translations.items()
            if k in expected_keys
            and (not v or not v.strip())
            and all_entries.get(k, "").strip()
        }
        if empty_keys:
            for k in empty_keys:
                missed_entries[k] = all_entries[k]
                del translations[k]
            logger.info(
                "批次 %s: %d 个翻译值为空，纳入集中补译",
                batch.batch_id, len(empty_keys),
            )

        if missed_entries:
            missing_count = len(missing_keys) + len(empty_keys)
            logger.info(
                "批次 %s: %d 个键未返回，纳入集中补译",
                batch.batch_id, missing_count,
            )

        # 校验（仅日志）
        validation_errors = self._validate_response(translations, expected_keys)
        if validation_errors:
            logger.info(
                "批次 %s: %s",
                batch.batch_id,
                "; ".join(validation_errors[:3]),
            )

        return TranslationResult(
            batch=batch,
            translations=translations,
            missed_entries=missed_entries,
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
        translations, _ = await self._call_and_parse(
            SYSTEM_PROMPT, user_message, expected=len(entries),
        )
        return translations if translations is not None else {}

    async def translate_missing(
        self,
        entries: dict[str, str],
        context: str = "",
    ) -> tuple[dict[str, str], dict]:
        """Translate a batch of entries that were missed in the main pass.

        Used for centralized 补译 after all mod batches finish.

        Args:
            entries: key → English text to translate.
            context: Human-readable context for the API.

        Returns:
            (key → zh_text dict, usage dict).
        """
        user_message = build_user_message(
            entries, mod_context=context or "集中补译",
        )
        translations, usage = await self._call_and_parse(
            SYSTEM_PROMPT, user_message, expected=len(entries),
        )
        if translations is None:
            translations = {}
            usage = {}

        # Fill missing with English fallback
        expected = set(entries.keys())
        found = set(translations.keys())
        still_missing = expected - found
        if still_missing:
            logger.warning(
                "补译仍有 %d 个键未返回，使用英文原文", len(still_missing),
            )
            for k in still_missing:
                translations[k] = entries[k]

        return translations, usage

    # ------------------------------------------------------------------
    # Internal: call + parse (with low-response retry)
    # ------------------------------------------------------------------

    async def _call_and_parse(
        self,
        system_prompt: str,
        user_message: str,
        *,
        expected: int = 0,
        batch_id: str = "",
    ) -> tuple[Optional[dict[str, str]], dict]:
        """Call API and parse response. Retries once if response is abnormally short.

        Args:
            system_prompt: Frozen system prompt.
            user_message: Dynamic user message with entries to translate.
            expected: Expected number of keys (for low-response detection).
            batch_id: Human-readable batch ID for logging.

        Returns:
            (translations dict or None on total failure, usage dict).
        """
        try:
            response_text, usage = await self._call_api(system_prompt, user_message)
        except Exception as e:
            logger.error("%sAPI 调用失败: %s", f"批次 {batch_id}: " if batch_id else "", e)
            return None, {}

        try:
            translations = await self._parse_response(response_text)
        except AIResponseParseError:
            logger.warning("%sJSON 解析失败", f"批次 {batch_id}: " if batch_id else "")
            translations = {}

        # Detect abnormally low response (model fluke) and retry once
        if expected >= 10 and translations:
            got = len(translations)
            if got < expected * _MIN_RESPONSE_RATIO:
                logger.warning(
                    "批次 %s: AI 仅返回 %d/%d 键 (%.0f%%)，疑似模型波动，重试一次",
                    batch_id, got, expected, got / expected * 100,
                )
                try:
                    response_text2, usage2 = await self._call_api(
                        system_prompt, user_message,
                    )
                    usage = _merge_usage(usage, usage2)
                    translations2 = await self._parse_response(response_text2)
                    if len(translations2) > got:
                        logger.info(
                            "批次 %s: 重试成功，获得 %d/%d 键 (%.0f%%)",
                            batch_id, len(translations2), expected,
                            len(translations2) / expected * 100,
                        )
                        translations = translations2
                except AIResponseParseError:
                    logger.debug("批次 %s: 重试 JSON 解析也失败了", batch_id)
                except Exception:
                    logger.debug("批次 %s: 重试 API 调用也失败了", batch_id)

        return translations, usage

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

        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        # DeepSeek v4 系列默认开启思考模式，翻译不需要，关掉省 token
        if "v4" in self._config.model.lower():
            payload["thinking"] = {"type": "disabled"}

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
                    wait = retry_after if retry_after else 2**attempt
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
                    wait = self._config.retry_base_delay * (2**attempt)
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
                logger.debug(
                    "API 响应 (finish=%s tokens=%s) 内容: %s",
                    finish_reason,
                    usage,
                    content[:2000],
                )
                if finish_reason == "length":
                    logger.warning(
                        "API 响应被截断 (finish_reason=length) — "
                        "部分翻译可能丢失"
                    )

                return content, usage

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                wait = self._config.retry_base_delay * (2**attempt)
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
        """从 AI 响应中提取 JSON 对象。

        已启用 response_format: json_object，AI 保证输出合法 JSON。
        保留轻量级容错处理（markdown 代码块等边界情况）。

        Raises:
            AIResponseParseError: 无法解析为有效 JSON。
        """
        if not raw_text or not raw_text.strip():
            raise AIResponseParseError("AI 返回空响应")

        text = raw_text.strip()
        logger.debug("AI 原始响应 (前 800 字符): %s", text[:800])

        # 直接解析 — JSON mode 下 99.9% 情况就是合法的
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return _coerce_values(result)
        except json.JSONDecodeError:
            pass

        # 容错：从 markdown 代码块提取（极少发生）
        md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if md_match:
            try:
                result = json.loads(md_match.group(1))
                if isinstance(result, dict):
                    return _coerce_values(result)
            except json.JSONDecodeError:
                pass

        logger.warning("无法解析的 AI 响应 (前 300 字符): %s", text[:300])
        raise AIResponseParseError("AI 响应不是合法 JSON")

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


def _merge_usage(a: dict, b: dict) -> dict:
    """Merge two API usage dicts by summing token counts."""
    if not a:
        return b
    if not b:
        return a
    merged: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = a.get(key, 0) + b.get(key, 0)
    # Cache tokens are already included in prompt_tokens
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        if key in a or key in b:
            merged[key] = a.get(key, 0) + b.get(key, 0)
    return merged


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    """Extract Retry-After header value."""
    value = response.headers.get("Retry-After", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
