"""One cloud run. Log only fixed status codes, never portfolio or credentials."""
from datetime import datetime, timezone
import os
import sys
import time
from zoneinfo import ZoneInfo
from jobs.deepseek import DeepSeekClient
from jobs.gateway import Gateway, GatewayError
from jobs.mail import MailConfig, MailError, send_report
from jobs.market import collect
from jobs.report import build_report


def main():
    gateway = Gateway()
    report_saved = False
    stage = 'configuration'
    try:
        config = MailConfig(os.environ["SMTP_USERNAME"], os.environ["SMTP_AUTH_CODE"], os.environ["MAIL_FROM"], os.environ["MAIL_TO"])
        config.validate()
        key = os.environ["DEEPSEEK_API_KEY"]
        if not key.strip():
            raise ValueError("missing_key")
        mode = os.environ.get("REPORT_MODE", "daily")
        if mode not in ("daily", "test"):
            raise ValueError("invalid_mode")
        stage = 'claim'
        version = int(os.environ.get('REPORT_VERSION', '1')) if mode == 'test' else 1
        run = gateway.post("runs/claim", {"mode": mode, "version": version})
        if run.get("skipped"):
            print("status=paused")
            return 0
        gateway.run_id = run["id"]
        if run["delivery"]:
            print("status=delivery_already_recorded")
            return 0
        report = run["report"]
        if not report:
            if not run["owned"] or run["state"] != "collecting":
                print("status=run_needs_review")
                return 1
            stage = 'generation'
            cutoff = datetime.now(timezone.utc)
            evidence = collect(run["snapshot"], cutoff)
            report = build_report(run["snapshot"], evidence, cutoff, DeepSeekClient(key, gateway))
            gateway.action("report", report)
        report_saved = True
        if mode == 'test' and report['evidence'].get('analysisStatus') == 'failed':
            print('status=test_analysis_needs_review')
            return 1
        if mode == "daily":
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            target = now.replace(hour=5, minute=15, second=0, microsecond=0)
            wait = (target - now).total_seconds()
            if wait > 1800:
                print("status=outside_delivery_window")
                return 1
            while wait > 0:
                time.sleep(min(wait, 30))
                wait = (target - datetime.now(target.tzinfo)).total_seconds()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        late = mode == "daily" and (now.hour, now.minute) > (5, 25)
        title = (f"[测试·第{version}版] " if mode == "test" else "[延迟] " if late else "") + now.strftime("%Y-%m-%d ") + report["title"]
        paragraphs = [("这是一封联调测试日报。" if mode == "test" else "每日投资观察。") + f" 发送于 {now.strftime('%Y-%m-%d %H:%M')}（北京时间）。"]
        state = send_report(config, gateway, run["id"], title, paragraphs, [], report=report)
        print("status=" + state)
        return 0 if state == "smtp_accepted" else 1
    except MailError as error:
        print("status=" + str(error))  # MailError is restricted to code constants.
        return 1
    except Exception as error:
        if gateway.run_id and not report_saved:
            try:
                gateway.action("failed", {"code": "generation_or_storage_failed"})
            except Exception:
                pass
        print("status=" + (str(error) if isinstance(error, GatewayError) else stage + "_failed"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
