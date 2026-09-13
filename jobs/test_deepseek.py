import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from jobs.deepseek import DeepSeekClient, DeepSeekError, request_json


class FakeBudget:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def reserve(self, call_id):
        self.calls.append(("reserve", call_id))
        return self.allowed

    def settle(self, call_id, charged_micros):
        self.calls.append(("settle", call_id, charged_micros))

    def uncertain(self, call_id):
        self.calls.append(("uncertain", call_id))


def response(content='{"summary":"测试摘要"}', reason="stop", usage=None):
    return {"model": "deepseek-flash", "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500},
            "choices": [{"finish_reason": reason, "message": {"content": content}}]}


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.budget = FakeBudget()
        self.client = DeepSeekClient("test-key-not-real", self.budget)
        self.messages = [{"role": "user", "content": "请输出测试 JSON"}]

    @patch("jobs.deepseek.request_json", return_value=response())
    def test_configured_json_call_records_conservative_usage(self, transport):
        result = self.client.complete_json("run-1", self.messages)
        body = json.loads(transport.call_args.args[0])
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_tokens"], 8192)
        self.assertNotIn("test-key-not-real", repr(self.client))
        self.assertEqual(result.content, {"summary": "测试摘要"})
        self.assertEqual(str(result.estimated_cost_cny), "0.006")
        self.assertEqual(self.budget.calls, [("reserve", "run-1"), ("settle", "run-1", 6000)])

    @patch("jobs.deepseek.request_json")
    def test_budget_exhaustion_stops_before_network(self, transport):
        self.budget.allowed = False
        with self.assertRaisesRegex(DeepSeekError, "budget_exhausted"):
            self.client.complete_json("run-1", self.messages)
        transport.assert_not_called()

    @patch("jobs.deepseek.request_json", side_effect=DeepSeekError("network_or_response_error"))
    def test_network_failure_keeps_reservation_and_does_not_retry(self, transport):
        with self.assertRaises(DeepSeekError):
            self.client.complete_json("run-1", self.messages)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.budget.calls[-1], ("uncertain", "run-1"))

    def test_invalid_and_truncated_outputs_still_account_for_paid_usage(self):
        for raw in [response(""), response("not-json"), response("[]"), response(reason="length")]:
            with self.subTest(raw=raw), patch("jobs.deepseek.request_json", return_value=raw):
                with self.assertRaises(DeepSeekError):
                    self.client.complete_json("run-1", self.messages)
                self.assertEqual(self.budget.calls[-1], ("settle", "run-1", 6000))

    @patch("jobs.deepseek.request_json", return_value=response(usage={"prompt_tokens": -1, "completion_tokens": 1}))
    def test_invalid_usage_keeps_reservation(self, _transport):
        with self.assertRaisesRegex(DeepSeekError, "invalid_usage"):
            self.client.complete_json("run-1", self.messages)
        self.assertEqual(self.budget.calls[-1], ("uncertain", "run-1"))

    @patch("jobs.deepseek.request_json")
    def test_large_input_or_missing_key_never_reserves_or_calls(self, transport):
        with self.assertRaisesRegex(DeepSeekError, "input_too_large"):
            self.client.complete_json("run-1", [{"role": "user", "content": "中" * 30000}])
        with self.assertRaisesRegex(DeepSeekError, "missing_api_key"):
            DeepSeekClient("", self.budget).complete_json("run-1", self.messages)
        self.assertEqual(self.budget.calls, [])
        transport.assert_not_called()

    @patch("jobs.deepseek.build_opener")
    def test_upstream_error_does_not_expose_response_or_key(self, opener):
        opener.return_value.open.side_effect = HTTPError("https://api.deepseek.com", 401, "sensitive body", {}, None)
        with self.assertRaises(DeepSeekError) as raised:
            request_json(b"{}", "test-key-not-real")
        self.assertEqual(str(raised.exception), "http_401")


if __name__ == "__main__":
    unittest.main()
