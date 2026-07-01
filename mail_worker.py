import email
import imaplib
import os
import sys
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

IMAP_HOST = os.environ.get("YANDEX_IMAP_HOST", "imap.yandex.com")
IMAP_PORT = int(os.environ.get("YANDEX_IMAP_PORT", "993"))
# Read lazily (via .get) so importing this module doesn't require creds — the
# bot can run without the mail worker. connect() validates they're present.
YANDEX_EMAIL = os.environ.get("YANDEX_EMAIL", "")
YANDEX_APP_PASSWORD = os.environ.get("YANDEX_APP_PASSWORD", "")


def mail_configured() -> bool:
    return bool(YANDEX_EMAIL and YANDEX_APP_PASSWORD)


@dataclass
class MailMessage:
    message_id: str
    sender: str
    subject: str
    date: datetime | None
    body: str

    def __str__(self) -> str:
        return (
            f"--- MailMessage ---\n"
            f"message_id: {self.message_id}\n"
            f"from:       {self.sender}\n"
            f"subject:    {self.subject}\n"
            f"date:       {self.date}\n"
            f"body:\n{self.body}\n"
            f"-------------------"
        )


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: Message) -> str:
    """Return the best-effort plain-text body of an email message."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():  # skip attachments
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if content_type == "text/plain":
            plain_parts.append(text)
        else:
            html_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    # fall back to raw HTML (parsers can strip tags as needed)
    return "\n".join(html_parts).strip()


def _parse_message(raw: bytes) -> MailMessage:
    msg = email.message_from_bytes(raw)
    date = None
    if msg.get("Date"):
        try:
            date = parsedate_to_datetime(msg.get("Date"))
        except (TypeError, ValueError):
            date = None
    return MailMessage(
        message_id=_decode(msg.get("Message-ID")),
        sender=_decode(msg.get("From")),
        subject=_decode(msg.get("Subject")),
        date=date,
        body=_extract_body(msg),
    )


class YandexMailClient:
    def __init__(self, host: str = IMAP_HOST, port: int = IMAP_PORT,
                 email_addr: str = YANDEX_EMAIL, password: str = YANDEX_APP_PASSWORD):
        self.host = host
        self.port = port
        self.email_addr = email_addr
        self.password = password
        self.conn: imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        if not (self.email_addr and self.password):
            raise RuntimeError("YANDEX_EMAIL / YANDEX_APP_PASSWORD not configured")
        self.conn = imaplib.IMAP4_SSL(self.host, self.port)
        self.conn.login(self.email_addr, self.password)

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            finally:
                self.conn = None

    def __enter__(self) -> "YandexMailClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch_recent(self, mailbox: str = "INBOX", limit: int = 10) -> list[MailMessage]:
        """Fetch the most recent `limit` messages from a mailbox (newest first)."""
        assert self.conn is not None, "call connect() first"
        self.conn.select(mailbox, readonly=True)
        typ, data = self.conn.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ} {data}")
        ids = data[0].split()
        if not ids:
            return []
        ids = ids[-limit:]
        messages = []
        for msg_id in reversed(ids):
            typ, msg_data = self.conn.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            messages.append(_parse_message(msg_data[0][1]))
        return messages

    def list_mailboxes(self) -> list[str]:
        assert self.conn is not None, "call connect() first"
        typ, data = self.conn.list()
        if typ != "OK":
            return []
        return [line.decode(errors="replace") for line in data]


def _debug_dump(limit: int = 10) -> None:
    print(f"Connecting to {IMAP_HOST}:{IMAP_PORT} as {YANDEX_EMAIL} ...", file=sys.stderr)
    with YandexMailClient() as client:
        print("Connected. Mailboxes:", file=sys.stderr)
        for mb in client.list_mailboxes():
            print(f"  {mb}", file=sys.stderr)
        print(f"\nFetching last {limit} messages from INBOX ...\n", file=sys.stderr)
        for m in client.fetch_recent(limit=limit):
            print(m)
            print()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    _debug_dump(n)
