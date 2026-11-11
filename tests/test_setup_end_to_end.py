"""First install -> real local HTTP setup -> provider -> scheduled source scan.

All provider data is fictional. No external network, account or mailbox is used.
"""
import argparse
import contextlib
import http.client
import io
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from job_harness.__main__ import build_service, initialize, load_config
from job_harness.console import make_handler
from job_harness.service import setting


class SetupEndToEnd(unittest.TestCase):
    def test_fresh_install_connect_start_scan_restart_without_outbound(self):
        requests = []

        class ModelServer(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers.get('Authorization'), body))
                payload = json.dumps({
                    'choices': [{'message': {'content': json.dumps({
                        'summary': '虚构连接测试通过', 'review': ['核实岗位要求']}, ensure_ascii=False)},
                        'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8},
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        class Careers:
            calls = 0

            def discover(self, _sources):
                self.calls += 1
                return [{'channel': 'official', 'company': '虚构测试公司', 'title': '平面设计师',
                         'external_id': 'fictional-designer', 'url': 'https://fixture.example/careers/design',
                         'domain': 'fixture.example', 'location': '杭州', 'salary': '15-20K',
                         'status': 'open', 'published_at': '2020-01-01',
                         'description': '负责品牌平面设计、视觉方案和产品包装，整理需求并与项目团队核对交付内容。',
                         'requirements': '熟悉平面设计与排版，具备可展示的作品集及设计实践。'}]

        with TemporaryDirectory() as directory:
            root = Path(directory) / 'private-data'
            args = argparse.Namespace(inheritance=None, account='', port=None)
            with contextlib.redirect_stdout(io.StringIO()):
                initialize(args, root)
            service = build_service(root, load_config(root))
            careers = Careers()
            service.adapters['official'] = careers
            self.assertTrue(service.snapshot()['onboarding']['required'])
            with service.store.tx() as c:
                self.assertTrue(setting(c, 'stopped', False))
            provider = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
            console = ThreadingHTTPServer(('127.0.0.1', 0), None)
            console.RequestHandlerClass = make_handler(service, 'fixture-csrf', console.server_port)
            threads = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval': .01})
                       for s in (provider, console)]
            worker = threading.Thread(target=service.run)
            for thread in threads:
                thread.start()
            worker.start()
            try:
                origin = 'http://127.0.0.1:' + str(console.server_port)

                def post(action, fields=None):
                    connection = http.client.HTTPConnection('127.0.0.1', console.server_port, timeout=10)
                    try:
                        connection.request('POST', '/action', urlencode({
                            'action': action, 'token': 'fixture-csrf', 'next': '/setup', **(fields or {})}),
                            {'Content-Type': 'application/x-www-form-urlencoded',
                             'Accept': 'application/json', 'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                self.assertEqual(post('setup_start')[0], 400)
                self.assertEqual(careers.calls, 0)
                self.assertEqual(requests, [])
                self.assertEqual(post('setup_profile', {
                    'cities': '杭州', 'directions': '平面设计', 'matching_preset': 'general',
                    'summary': '虚构测试：完成过平面设计作品。'})[0], 200)
                self.assertEqual(post('setup_model', {
                    'provider': 'openai_compatible', 'name': 'fixture-model', 'enabled': 'true',
                    'base_url': 'http://127.0.0.1:' + str(provider.server_port) + '/v1',
                    'api_key_env': 'JOB_HARNESS_ISOLATED_TEST_KEY', 'api_key': 'fictional-key-e2e',
                    'max_calls': '3', 'timeout': '5'})[0], 200)
                self.assertEqual(requests, [], 'saving must not call a paid provider')
                self.assertEqual(post('setup_source', {
                    'url': 'https://fixture.example/careers', 'company': '虚构测试公司'})[0], 200)
                self.assertEqual(post('setup_start')[0], 400, 'untested model must block start')
                status, result = post('setup_test_model')
                self.assertEqual(status, 200, result)
                self.assertIn('连接测试通过', result['notice'])
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0][0], '/v1/chat/completions')
                self.assertEqual(requests[0][1], 'Bearer fictional-key-e2e')
                self.assertNotIn('完成过平面设计作品', json.dumps(requests[0][2], ensure_ascii=False))
                self.assertEqual(post('setup_start')[0], 200)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with service.store.tx() as c:
                        jobs = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
                    if jobs:
                        break
                    service.wake.set()
                    time.sleep(.01)
                self.assertEqual(jobs, 1)
                self.assertEqual(post('setup_start')[0], 200)
                with service.store.tx() as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks WHERE dedupe_key LIKE 'onboarding:start:%'").fetchone()[0], 1)
                    self.assertTrue(setting(c, 'outbound_paused', False))
                    self.assertFalse(service.preferences(c)['auto_send'])
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
                public = json.dumps(service.snapshot(), ensure_ascii=False)
                self.assertNotIn('fictional-key-e2e', public)
                reopened = build_service(root, load_config(root)).snapshot()
                self.assertTrue(reopened['onboarding']['complete'])
                self.assertTrue(reopened['onboarding']['model_ready'])
            finally:
                service.shutdown.set()
                service.wake.set()
                worker.join(5)
                for server in (console, provider):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(5)
            self.assertFalse(worker.is_alive())
