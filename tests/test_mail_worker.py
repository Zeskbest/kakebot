"""Tests for fetching messages from the mailbox.

These don't touch the network: a fake IMAP connection feeds canned RFC822 bytes
into YandexMailClient so we exercise the fetch/parse path (ordering, limit,
SINCE criteria, header decoding, plain-vs-HTML body selection) deterministically.
"""
import unittest
from email.message import EmailMessage

from mail_worker import YandexMailClient, _parse_message


def _raw(message_id: str, subject: str, sender: str = "K PLUS <KPLUS@kasikornbank.com>",
         date: str = "Wed, 09 Jul 2026 15:41:09 +0700",
         plain: str | None = "plain body", html: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = date
    if plain is not None:
        msg.set_content(plain)
    if html is not None:
        if plain is None:
            msg.add_header("Content-Type", "text/html")
            msg.set_payload(html)
        else:
            msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


class FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL covering what the client calls."""

    def __init__(self, messages: dict[bytes, bytes]):
        self._messages = messages          # imap id -> raw RFC822
        self.selected: str | None = None
        self.search_calls: list[tuple] = []

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        return "OK", [str(len(self._messages)).encode()]

    def search(self, charset, *criteria):
        self.search_calls.append(criteria)
        return "OK", [b" ".join(sorted(self._messages))]

    def fetch(self, msg_id, spec):
        raw = self._messages[msg_id]
        return "OK", [(b"%s (RFC822)" % msg_id, raw)]


class ParseMessageTests(unittest.TestCase):
    def test_decodes_header_and_prefers_plain_text(self):
        raw = _raw("<a@x>", "Тест письмо", plain="the plain part", html="<b>html</b>")
        m = _parse_message(raw)
        self.assertEqual(m.subject, "Тест письмо")
        self.assertEqual(m.sender, "K PLUS <KPLUS@kasikornbank.com>")
        self.assertIn("the plain part", m.body)
        self.assertNotIn("<b>", m.body)
        self.assertIsNotNone(m.date)

    def test_falls_back_to_html_when_no_plain_part(self):
        raw = _raw("<a@x>", "s", plain=None, html="<p>only&nbsp;html</p>")
        m = _parse_message(raw)
        self.assertIn("html", m.body)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.client = YandexMailClient(email_addr="x", password="y")
        self.client.conn = FakeIMAP({
            b"1": _raw("<1@x>", "oldest"),
            b"2": _raw("<2@x>", "middle"),
            b"3": _raw("<3@x>", "newest"),
        })

    def test_fetch_recent_returns_newest_first_and_respects_limit(self):
        got = self.client.fetch_recent(limit=2)
        self.assertEqual([m.subject for m in got], ["newest", "middle"])

    def test_fetch_since_is_chronological(self):
        got = self.client.fetch_since(since=None)
        self.assertEqual([m.subject for m in got], ["oldest", "middle", "newest"])

    def test_fetch_since_uses_imap_since_criterion(self):
        import datetime
        self.client.fetch_since(since=datetime.datetime(2026, 7, 1))
        self.assertIn(("SINCE", "01-Jul-2026"), self.client.conn.search_calls)


if __name__ == "__main__":
    unittest.main()
