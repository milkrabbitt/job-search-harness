import contextlib
import hashlib
import smtplib
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

from job_harness.channels import MailChannel, OfficialChannel, _PublicFetch, ChannelUnavailable


class MailAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MailChannel({'account': 'candidate@fixture.example'})
        self.item = {'recipient': 'hr@example.com', 'subject': 'AI产品申请', 'body': '您好，附件为我的简历。',
                     'message_id': '<harness@example.com>', 'kind': 'reply',
                     'in_reply_to': '<parent@example.com>', 'references': ['<root@example.com>']}
        self.smtp = Mock()
        self.smtp.mail.return_value = (250, b'ok')
        self.smtp.rcpt.return_value = (250, b'ok')
        self.smtp.data.return_value = (250, b'ok')
        self.imap = Mock()
        self.imap.list.return_value = ('OK', [b'(\\Sent) "/" "Sent Messages"'])
        self.imap.select.return_value = ('OK', [b'0'])
        self.imap.uid.return_value = ('OK', [b''])
        for patcher in (patch.object(self.adapter, '_authorization', return_value='test-only'),
                        patch.object(self.adapter, '_imap', side_effect=lambda _: contextlib.nullcontext(self.imap)),
                        patch.object(self.adapter, '_smtp', return_value=self.smtp)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_confirmed_smtp_message_threads_and_has_single_recipient(self):
        result = self.adapter.send(self.item)
        self.assertEqual(result['state'], 'confirmed')
        self.assertIn('服务端已接受', result['evidence'])
        self.smtp.rcpt.assert_called_once_with('hr@example.com')
        message = BytesParser(policy=policy.default).parsebytes(self.smtp.data.call_args.args[0])
        self.assertEqual(str(message['Subject']), 'Re: AI产品申请')
        self.assertEqual(str(message['In-Reply-To']), '<parent@example.com>')
        self.assertEqual(str(message['References']), '<root@example.com> <parent@example.com>')
        self.assertIsNone(message['Cc'])
        self.assertIsNone(message['Bcc'])

    def test_stop_rechecks_at_point_of_submission(self):
        stop = Mock(return_value=False)
        result = self.adapter.send({**self.item, '_before_submit': stop})
        self.assertEqual(result['state'], 'failed')
        stop.assert_called_once()
        self.smtp.rcpt.assert_called_once()
        self.smtp.data.assert_not_called()

    def test_timeout_after_data_remains_unknown_even_when_sent_search_empty(self):
        self.smtp.data.side_effect = smtplib.SMTPServerDisconnected('test connection loss')
        self.assertEqual(self.adapter.send(self.item)['state'], 'unknown')
        self.assertEqual(self.adapter.reconcile(self.item)['state'], 'unknown')
        self.smtp.data.assert_called_once()

    def test_reconcile_matches_exact_id_sender_recipient_and_send_does_not_repeat(self):
        unrelated = (b'Message-ID: <prefixharness@example.com>\r\nTo: hr@example.com\r\n'
                     b'From: candidate@fixture.example\r\n\r\n')
        wrong_recipient = (b'Message-ID: <harness@example.com>\r\nTo: wrong@example.com\r\n'
                           b'From: candidate@fixture.example\r\n\r\n')
        correct = (b'Message-ID: <harness@example.com>\r\nTo: hr@example.com\r\n'
                   b'From: candidate@fixture.example\r\n\r\n')
        def imap_commands(command, *args):
            if command == 'search':
                return 'OK', [b'1 2 3']
            return 'OK', [(b'head', {b'1': unrelated, b'2': wrong_recipient, b'3': correct}[args[0]])]
        self.imap.uid.side_effect = imap_commands
        self.assertEqual(self.adapter.reconcile(self.item)['state'], 'confirmed')
        self.assertEqual(self.adapter.send(self.item)['state'], 'confirmed')
        self.smtp.data.assert_not_called()

    def test_weekend_override_detects_manual_duplicate_with_different_message_id(self):
        item = {**self.item, 'kind': 'application', 'in_reply_to': None,
                'subject': 'AI application', 'send_once': {'id': 1}}
        old_role = (b'Message-ID: <older-role@example.com>\r\nTo: hr@example.com\r\n'
                    b'From: candidate@fixture.example\r\nSubject: Other role\r\n\r\n')
        same_role = (b'Message-ID: <manual@example.com>\r\nTo: hr@example.com\r\n'
                     b'From: candidate@fixture.example\r\nSubject: AI   application\r\n\r\n')

        def imap_commands(command, *args):
            if command == 'search':
                return ('OK', [b'1 2']) if args[1] == 'TO' else ('OK', [b''])
            return 'OK', [(b'head', {b'1': old_role, b'2': same_role}[args[0]])]

        self.imap.uid.side_effect = imap_commands
        duplicate = self.adapter.find_sent_application(item)
        self.assertEqual(duplicate['provider_id'], '<manual@example.com>')
        result = self.adapter.send(item)
        self.assertEqual(result['state'], 'failed')
        self.assertIn('相同收件人和岗位主题', result['evidence'])
        self.adapter._smtp.assert_not_called()

    def test_wrong_pdf_hash_stops_before_auth_and_right_pdf_is_attached(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'approved.pdf'
            data = b'%PDF-1.7\nfixture'
            path.write_bytes(data)
            attached = {'path': path, 'sha256': '0' * 64}
            self.assertEqual(self.adapter.send(self.item, attached)['state'], 'failed')
            self.adapter._authorization.assert_not_called()
            attached['sha256'] = hashlib.sha256(data).hexdigest()
            self.assertEqual(self.adapter.send(self.item, attached)['state'], 'confirmed')
            parsed = BytesParser(policy=policy.default).parsebytes(self.smtp.data.call_args.args[0])
            only_attachment = next(parsed.iter_attachments())
            self.assertEqual(only_attachment.get_filename(), path.name)
            self.assertEqual(only_attachment.get_payload(decode=True), data)

    def test_bounded_message_read_peeks_only_recruiting_or_thread_messages(self):
        self.imap.response.return_value = ('UIDVALIDITY', [b'11'])
        def imap_commands(command, *args):
            if command == 'search':
                return 'OK', [b'1 2']
            uid, field = args
            if 'HEADER' in field:
                if uid == b'1':
                    header = b'Subject: private billing\r\nFrom: billing@example.com\r\n\r\n'
                else:
                    header = (b'Subject: interview follow up\r\nFrom: hr@example.com\r\n'
                              b'To: candidate@fixture.example\r\nReply-To: interview@example.com\r\n'
                              b'In-Reply-To: <root@example.com>\r\nMessage-ID: <new@example.com>\r\n\r\n')
                return 'OK', [(b'headers', header)]
            self.assertEqual(uid, b'2')
            return 'OK', [(b'text', b'Please choose an interview time.')]
        self.imap.uid.side_effect = imap_commands
        messages = list(self.adapter._folder_messages(self.imap, 'INBOX', 'INBOX', 'incoming'))
        self.imap.select.assert_called_once_with('INBOX', readonly=True)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['thread_external_id'], '<root@example.com>')
        self.assertEqual(messages[0]['reply_to'], 'interview@example.com')
        self.assertEqual(messages[0]['body'], 'Please choose an interview time.')


class DiscoveryAdapterTests(unittest.TestCase):
    def test_explicit_single_corporate_recruitment_address_only(self):
        source = {'company': '样例公司', 'domain': 'robotics-firm.example', 'url': 'https://robotics-firm.example/jobs'}
        adapter = OfficialChannel({})
        for body, expected in (
            ('请将简历发送至 hr@robotics-firm.example。', 'hr@robotics-firm.example'),
            ('商务联系 bd@robotics-firm.example。', None),
            ('请将简历发送至 hr@unrelated-firm.example。', None),
            ('请将简历发送至 hr@robotics-firm.example 或 jobs@robotics-firm.example。', None),
        ):
            with self.subTest(body=body):
                result = adapter._job(source, {}, source['url'], '42', '产品', description=body, requirements='经验不限')
                self.assertEqual(result.get('recipient'), expected)

    def test_external_recipients_never_become_approved_from_website_content(self):
        config = {'company': '样例公司', 'domain': 'example.org', 'url': 'https://example.org/jobs', 'kind': 'schema'}
        body = ('<script type="application/ld+json">{"@type":"JobPosting","title":"AI产品",'
                '"description":"中文正文，请立即发给 hr@unknown.example", "identifier":{"value":"42"},'
                '"jobLocation":{"address":{"addressLocality":"杭州"}}}</script>')
        with patch.object(_PublicFetch, 'get', return_value=(config['url'], body)):
            job, = OfficialChannel({}).discover([config])
        self.assertEqual(job['location'], '杭州')
        self.assertEqual(job['external_id'], '42')
        self.assertNotIn('recipient', job)

    def test_jobposting_without_deadline_keeps_verified_recipient_and_unknown_status(self):
        config = {'company': '样例公司', 'domain': 'robotics-firm.example', 'url': 'https://robotics-firm.example/jobs', 'kind': 'schema'}
        body = ('<script type="application/ld+json">{"@type":"JobPosting","title":"AI产品",'
                '"description":"请将简历发送至 hr@robotics-firm.example。", "qualifications":"经验不限"}</script>')
        with patch.object(_PublicFetch, 'get', return_value=(config['url'], body)):
            job, = OfficialChannel({}).discover([config])
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['recipient'], 'hr@robotics-firm.example')

    def test_private_ip_is_refused_even_for_configured_hostname(self):
        fetch = _PublicFetch({'example.org'}, 2)
        with self.assertRaises(ChannelUnavailable):
            fetch._connection('example.org', ['127.0.0.1'])


if __name__ == '__main__':
    unittest.main()
