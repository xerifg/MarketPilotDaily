import smtplib
import unittest
from unittest.mock import Mock, patch

from jobs.mail import MailConfig, MailError, build_message, send_report


class MailTests(unittest.TestCase):
    def setUp(self):
        self.config = MailConfig("example@163.com", "test-auth-not-real", "example@163.com", "example@163.com")
        self.gateway = Mock()
        self.gateway.claim.return_value = True
        self.smtp = Mock()
        self.smtp.ehlo.return_value = (250, b"ok")
        self.smtp.mail.return_value = (250, b"ok")
        self.smtp.rcpt.return_value = (250, b"ok")
        self.smtp.data.return_value = (250, b"ok")

    def send(self):
        with patch("jobs.mail.smtplib.SMTP_SSL", return_value=self.smtp) as factory:
            result = send_report(self.config, self.gateway, "daily-2026-09-13", "日报测试", ["测试摘要"], [])
            return result, factory

    def test_multipart_escapes_untrusted_text_and_preserves_plaintext(self):
        message = build_message(self.config, "report-1", "日报测试", ["<script>alert(1)</script>"],
                                [("<source>", "https://example.com/?a=1&b=2")])
        self.assertIn("<script>", message.get_body(preferencelist=("plain",)).get_content())
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("a=1&amp;b=2", html)
        self.assertNotIn("test-auth", str(message))
        self.assertNotIn("example@163.com", repr(self.config))

    def test_rejects_header_injection_links_and_unconfirmed_recipient(self):
        for title, sources in [("title\r\nBcc: bad@example.com", []), ("title", [("x", "javascript:alert(1)")]),
                               ("title", [("x", "https://user:password@example.com")])]:
            with self.subTest(title=title, sources=sources), self.assertRaises(MailError):
                build_message(self.config, "id", title, ["text"], sources)
        config = MailConfig("example@163.com", "test", "example@163.com", "another@163.com")
        with self.assertRaises(MailError):
            build_message(config, "id", "title", ["text"], [])

    def test_claim_is_persisted_before_connection_and_tls_is_verified(self):
        def check_claim(*args, **kwargs):
            self.gateway.claim.assert_called_once_with("daily-2026-09-13")
            self.assertEqual(args, ("smtp.163.com", 465))
            self.assertTrue(kwargs["context"].check_hostname)
            return self.smtp
        with patch("jobs.mail.smtplib.SMTP_SSL", side_effect=check_claim):
            self.assertEqual(send_report(self.config, self.gateway, "daily-2026-09-13", "日报", ["摘要"], []), "smtp_accepted")
        self.gateway.finish.assert_called_once_with("daily-2026-09-13", "smtp_accepted", None)

    def test_duplicate_or_claim_failure_does_not_connect(self):
        for error in (None, RuntimeError("sensitive database response")):
            self.gateway.claim.return_value = False
            self.gateway.claim.side_effect = error
            with patch("jobs.mail.smtplib.SMTP_SSL") as factory, self.assertRaises(MailError) as caught:
                send_report(self.config, self.gateway, "id", "title", ["text"], [])
            factory.assert_not_called()
            self.assertNotIn("sensitive", str(caught.exception))

    def test_auth_failure_is_sanitized_and_never_sends_data(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private smtp response")
        result, _ = self.send()
        self.assertEqual(result, "failed_before_data")
        self.smtp.data.assert_not_called()
        self.gateway.finish.assert_called_once_with("daily-2026-09-13", result, "smtp_auth_failed")

    def test_recipient_rejection_stops_before_data(self):
        self.smtp.rcpt.return_value = (550, b"private mailbox")
        result, _ = self.send()
        self.assertEqual(result, "failed_before_data")
        self.smtp.data.assert_not_called()

    def test_timeout_after_data_is_uncertain_without_retry(self):
        self.smtp.data.side_effect = TimeoutError("private details")
        result, factory = self.send()
        self.assertEqual(result, "delivery_uncertain")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.smtp.data.call_count, 1)

    def test_explicit_data_rejection_is_recorded(self):
        for outcome in ("return", "raise"):
            with self.subTest(outcome=outcome):
                self.smtp.data.return_value = (554, b"no")
                self.smtp.data.side_effect = smtplib.SMTPDataError(554, b"no") if outcome == "raise" else None
                self.assertEqual(self.send()[0], "smtp_rejected")

    def test_close_failure_does_not_undo_acceptance(self):
        self.smtp.close.side_effect = OSError("socket closed")
        self.assertEqual(self.send()[0], "smtp_accepted")

    def test_result_write_failure_never_resends(self):
        self.gateway.finish.side_effect = RuntimeError("private storage details")
        with self.assertRaisesRegex(MailError, "delivery_result_unrecorded"):
            self.send()
        self.assertEqual(self.smtp.data.call_count, 1)


if __name__ == "__main__":
    unittest.main()
