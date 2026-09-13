import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from jobs.gateway import Gateway, GatewayError


class GatewayTests(unittest.TestCase):
    def test_http_failure_never_exposes_token_url_or_response(self):
        gateway = Gateway()
        with patch.object(gateway, '_token', return_value='private-token'), patch('jobs.gateway.build_opener') as factory:
            factory.return_value.open.side_effect = HTTPError('https://private.example', 403, 'private message', {}, None)
            with self.assertRaisesRegex(GatewayError, '^task_api_http_403$'):
                gateway.post('runs/claim', {'mode': 'test'})
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.headers['User-agent'], 'MarketPilotDaily/0.1')

    def test_token_errors_preserve_only_safe_stage_code(self):
        with patch.dict('os.environ', {'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://unexpected.example/token',
                                      'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'private-token'}):
            with self.assertRaisesRegex(GatewayError, '^invalid_oidc_endpoint$'):
                Gateway().post('runs/claim', {'mode': 'test'})

    def test_delivery_cannot_be_written_for_another_report(self):
        gateway = Gateway()
        gateway.run_id = 'test-2026-09-13'
        with patch.object(gateway, 'post') as post:
            with self.assertRaises(GatewayError):
                gateway.claim('test-2026-09-12')
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
