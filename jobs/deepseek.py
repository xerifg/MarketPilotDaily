"""DeepSeek JSON adapter. Requires a durable budget gateway before any paid call."""

from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

SETTINGS = json.loads((Path(__file__).resolve().parents[1] / "config" / "ai.json").read_text(encoding="utf-8"))
ENDPOINT = "https://api.deepseek.com/chat/completions"


class BudgetGateway(Protocol):
    """The task API must persist these operations in D1, not in runner files."""

    def reserve(self, call_id: str) -> bool: ...
    def settle(self, call_id: str, charged_micros: int) -> None: ...
    def uncertain(self, call_id: str) -> None: ...


class DeepSeekError(Exception):
    """Safe error code only; never includes upstream bodies, keys, or holdings."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(payload: bytes, api_key: str) -> dict:
    request = Request(ENDPOINT, data=payload, headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
    }, method="POST")
    try:
        with build_opener(NoRedirects()).open(request, timeout=120) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise DeepSeekError("response_too_large")
            return json.loads(raw)
    except HTTPError as error:
        raise DeepSeekError(f"http_{error.code}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise DeepSeekError("network_or_response_error") from None


@dataclass
class JsonCompletion:
    content: dict
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_cny: Decimal
    model: str


@dataclass
class DeepSeekClient:
    api_key: str = field(repr=False)
    budget: BudgetGateway

    def complete_json(self, call_id: str, messages: list[dict[str, str]]) -> JsonCompletion:
        if not self.api_key or not self.api_key.strip():
            raise DeepSeekError("missing_api_key")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
            raise DeepSeekError("invalid_messages")
        if any(not isinstance(item, dict) or set(item) != {"role", "content"}
               or item["role"] not in {"system", "user", "assistant"}
               or not isinstance(item["content"], str) for item in messages):
            raise DeepSeekError("invalid_messages")
        messages = [{"role": "system", "content": '只输出合法 JSON 对象，例如 {"summary":"摘要"}；不输出 Markdown。'}] + messages
        payload = json.dumps({
            "model": SETTINGS["model"], "thinking": {"type": SETTINGS["thinking"]},
            "response_format": {"type": "json_object"}, "messages": messages,
            "max_tokens": SETTINGS["maxOutputTokens"], "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        if len(payload) > SETTINGS["maxRequestBytes"]:
            raise DeepSeekError("input_too_large")
        if not self.budget.reserve(call_id):
            raise DeepSeekError("budget_exhausted_or_call_exists")
        try:
            raw = request_json(payload, self.api_key)
            usage = raw["usage"]
            prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
            if any(type(value) is not int or value < 0 for value in (prompt, completion)):
                raise DeepSeekError("invalid_usage")
            # Ignore cache/off-peak discounts: this is a conservative estimate, not an invoice.
            micros = int(Decimal(prompt) * Decimal(SETTINGS["peakInputCnyPerMillion"])
                          + Decimal(completion) * Decimal(SETTINGS["peakOutputCnyPerMillion"]))
            self.budget.settle(call_id, micros)
        except Exception as error:
            try:
                self.budget.uncertain(call_id)
            except Exception:
                pass  # The original persisted reservation still consumes budget.
            if isinstance(error, DeepSeekError):
                raise
            raise DeepSeekError("usage_or_budget_unconfirmed") from None
        try:
            choice = raw["choices"][0]
            if choice["finish_reason"] != "stop":
                raise DeepSeekError("incomplete_output")
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise DeepSeekError("empty_output")
            content = json.loads(text)
            if not isinstance(content, dict):
                raise DeepSeekError("output_not_object")
            if micros > int(Decimal(SETTINGS["reservePerCallCny"]) * 1_000_000):
                raise DeepSeekError("usage_exceeded_reservation")
            return JsonCompletion(content, prompt, completion, Decimal(micros) / 1_000_000, raw.get("model", SETTINGS["model"]))
        except DeepSeekError:
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            raise DeepSeekError("invalid_output") from None
