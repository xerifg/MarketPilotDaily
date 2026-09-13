"""Short-lived GitHub OIDC credentials; no secrets or response bodies in errors."""
import json
import os
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from jobs.deepseek import NoRedirects

ORIGIN = "https://market-pilot-daily.market-pilot-daily.workers.dev"


class GatewayError(Exception):
    pass


class Gateway:
    def __init__(self):
        self.token = None
        self.token_time = 0
        self.run_id = None

    def _token(self):
        if self.token and time.monotonic() - self.token_time < 120:
            return self.token
        url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".actions.githubusercontent.com"):
            raise GatewayError("invalid_oidc_endpoint")
        request = Request(url + ("&" if "?" in url else "?") + urlencode({"audience": ORIGIN}),
                          headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
        with build_opener(NoRedirects()).open(request, timeout=25) as response:
            self.token = json.loads(response.read(32000))["value"]
        self.token_time = time.monotonic()
        return self.token

    def post(self, path, body):
        try:
            request = Request(ORIGIN + "/internal/" + path, data=json.dumps(body, ensure_ascii=False).encode(),
                              headers={"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"})
            with build_opener(NoRedirects()).open(request, timeout=30) as response:
                raw = response.read(250001)
                if len(raw) > 250000:
                    raise GatewayError("response_too_large")
                return json.loads(raw)
        except Exception:
            raise GatewayError("task_api_failed") from None

    def action(self, name, body=None):
        if not self.run_id:
            raise GatewayError("missing_run")
        return self.post(f"runs/{self.run_id}/{name}", body or {})

    def reserve(self, call_id):
        return self.action("ai-reserve")["allowed"]

    def settle(self, call_id, charged_micros):
        self.action("ai-settle", {"chargedMicros": charged_micros})

    def uncertain(self, call_id):
        self.action("ai-uncertain")

    def claim(self, report_id):
        if report_id != self.run_id:
            raise GatewayError("report_mismatch")
        return self.action("delivery-claim")["allowed"]

    def finish(self, report_id, state, error_code):
        if report_id != self.run_id:
            raise GatewayError("report_mismatch")
        self.action("delivery-finish", {"state": state, "errorCode": error_code})
