"""IMAP-backed mail tools (gmail_* compatible names)."""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
from email.header import decode_header
from email.message import EmailMessage
from email.message import Message
from typing import Any

from .base import Tool


def _decode_mime_words(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _extract_text_body(msg: Message, max_chars: int = 8000) -> str:
    texts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                texts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        texts.append(payload.decode(charset, errors="replace"))
    body = "\n".join(t.strip() for t in texts if t and t.strip()).strip()
    return body[:max_chars]


def _snippet(text: str, n: int = 160) -> str:
    s = " ".join((text or "").split())
    return s[:n] + ("..." if len(s) > n else "")


class _IMAPBaseTool(Tool):
    def _imap_connect(self) -> tuple[imaplib.IMAP4_SSL | None, str | None]:
        host = os.getenv("CORECODER_IMAP_HOST", "").strip()
        port = int(os.getenv("CORECODER_IMAP_PORT", "993"))
        username = os.getenv("CORECODER_EMAIL_ADDRESS", "").strip()
        password = (
            os.getenv("CORECODER_EMAIL_APP_PASSWORD", "").strip()
            or os.getenv("CORECODER_EMAIL_PASSWORD", "").strip()
        )
        if not host or not username or not password:
            return None, (
                "Error: IMAP env is incomplete. Required: "
                "CORECODER_IMAP_HOST, CORECODER_EMAIL_ADDRESS, "
                "CORECODER_EMAIL_APP_PASSWORD(or CORECODER_EMAIL_PASSWORD)."
            )
        try:
            client = imaplib.IMAP4_SSL(host=host, port=port)
            client.login(username, password)
            return client, None
        except Exception as exc:
            return None, f"Error: IMAP login/connect failed: {exc}"

    @staticmethod
    def _fetch_message(client: imaplib.IMAP4_SSL, uid: str) -> Message | None:
        typ, data = client.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        raw_bytes = data[0][1]
        return email.message_from_bytes(raw_bytes)


class _SMTPBaseTool(Tool):
    def _smtp_connect(self) -> tuple[smtplib.SMTP_SSL | None, str | None]:
        host = os.getenv("CORECODER_SMTP_HOST", "").strip()
        port = int(os.getenv("CORECODER_SMTP_PORT", "465"))
        username = os.getenv("CORECODER_EMAIL_ADDRESS", "").strip()
        password = (
            os.getenv("CORECODER_EMAIL_APP_PASSWORD", "").strip()
            or os.getenv("CORECODER_EMAIL_PASSWORD", "").strip()
        )
        if not host or not username or not password:
            return None, (
                "Error: SMTP env is incomplete. Required: "
                "CORECODER_SMTP_HOST, CORECODER_EMAIL_ADDRESS, "
                "CORECODER_EMAIL_APP_PASSWORD(or CORECODER_EMAIL_PASSWORD)."
            )
        try:
            client = smtplib.SMTP_SSL(host=host, port=port, timeout=20)
            client.login(username, password)
            return client, None
        except Exception as exc:
            return None, f"Error: SMTP login/connect failed: {exc}"


class GmailListRecentTool(_IMAPBaseTool):
    name = "gmail_list_recent"
    description = "List recent emails via IMAP. Returns uid/from/subject/date/snippet."
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max messages to return (1-100)."},
            "mailbox": {"type": "string", "description": "IMAP mailbox, default INBOX."},
        },
    }

    def execute(
        self,
        limit: int = 10,
        mailbox: str = "INBOX",
        max_results: int | None = None,
        maxResults: int | None = None,
        **_kwargs,
    ) -> str:
        if max_results is not None:
            limit = int(max_results)
        if maxResults is not None:
            limit = int(maxResults)
        limit = max(1, min(int(limit or 10), 100))
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox, readonly=True)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            typ, data = client.uid("search", None, "ALL")
            if typ != "OK":
                return "Error: IMAP search failed."
            uids = (data[0] or b"").decode().split()
            if not uids:
                return "No emails found."
            target = list(reversed(uids))[:limit]
            lines = [f"Recent emails ({len(target)}):"]
            for idx, uid in enumerate(target, start=1):
                msg = self._fetch_message(client, uid)
                if msg is None:
                    continue
                subject = _decode_mime_words(msg.get("Subject"))
                from_ = _decode_mime_words(msg.get("From"))
                date_ = _decode_mime_words(msg.get("Date"))
                snip = _snippet(_extract_text_body(msg, max_chars=300))
                lines.append(
                    f"{idx}. uid={uid}\n"
                    f"   From: {from_}\n"
                    f"   Subject: {subject}\n"
                    f"   Date: {date_}\n"
                    f"   Snippet: {snip}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass


class GmailSearchTool(_IMAPBaseTool):
    name = "gmail_search"
    description = "Search emails by query over subject/from/body snippet via IMAP."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword query."},
            "limit": {"type": "integer", "description": "Max results (1-100)."},
            "mailbox": {"type": "string", "description": "IMAP mailbox, default INBOX."},
        },
        "required": ["query"],
    }

    def execute(
        self,
        query: str,
        limit: int = 20,
        mailbox: str = "INBOX",
        max_results: int | None = None,
        maxResults: int | None = None,
        **_kwargs,
    ) -> str:
        q = (query or "").strip().lower()
        if not q:
            return "Error: query is required."
        if max_results is not None:
            limit = int(max_results)
        if maxResults is not None:
            limit = int(maxResults)
        limit = max(1, min(int(limit or 20), 100))
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox, readonly=True)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            typ, data = client.uid("search", None, "ALL")
            if typ != "OK":
                return "Error: IMAP search failed."
            uids = list(reversed((data[0] or b"").decode().split()))
            matched: list[dict[str, Any]] = []
            for uid in uids:
                if len(matched) >= limit:
                    break
                msg = self._fetch_message(client, uid)
                if msg is None:
                    continue
                subject = _decode_mime_words(msg.get("Subject"))
                from_ = _decode_mime_words(msg.get("From"))
                date_ = _decode_mime_words(msg.get("Date"))
                body = _extract_text_body(msg, max_chars=1200)
                hay = f"{subject}\n{from_}\n{body}".lower()
                if q not in hay:
                    continue
                matched.append(
                    {
                        "uid": uid,
                        "from": from_,
                        "subject": subject,
                        "date": date_,
                        "snippet": _snippet(body),
                    }
                )
            if not matched:
                return f"No emails matched query: {query}"
            lines = [f"Search results ({len(matched)}):"]
            for i, item in enumerate(matched, start=1):
                lines.append(
                    f"{i}. uid={item['uid']}\n"
                    f"   From: {item['from']}\n"
                    f"   Subject: {item['subject']}\n"
                    f"   Date: {item['date']}\n"
                    f"   Snippet: {item['snippet']}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass


class GmailGetContentTool(_IMAPBaseTool):
    name = "gmail_get_content"
    description = "Get full email content by IMAP uid."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "IMAP UID of email."},
            "mailbox": {"type": "string", "description": "IMAP mailbox, default INBOX."},
            "max_chars": {"type": "integer", "description": "Max body chars, default 8000."},
        },
        "required": ["uid"],
    }

    def execute(self, uid: str, mailbox: str = "INBOX", max_chars: int = 8000) -> str:
        uid = (uid or "").strip()
        if not uid:
            return "Error: uid is required."
        max_chars = max(200, min(int(max_chars or 8000), 50000))
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox, readonly=True)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            msg = self._fetch_message(client, uid)
            if msg is None:
                return f"Error: message uid={uid} not found."
            subject = _decode_mime_words(msg.get("Subject"))
            from_ = _decode_mime_words(msg.get("From"))
            to_ = _decode_mime_words(msg.get("To"))
            date_ = _decode_mime_words(msg.get("Date"))
            body = _extract_text_body(msg, max_chars=max_chars)
            return (
                f"uid: {uid}\n"
                f"From: {from_}\n"
                f"To: {to_}\n"
                f"Subject: {subject}\n"
                f"Date: {date_}\n\n"
                f"{body}"
            )
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass


class GmailSendEmailTool(_SMTPBaseTool):
    name = "gmail_send_email"
    description = "Send email via SMTP."
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email."},
            "subject": {"type": "string", "description": "Email subject."},
            "body": {"type": "string", "description": "Email body text."},
        },
        "required": ["to", "subject", "body"],
    }

    def execute(self, to: str, subject: str, body: str, **_kwargs) -> str:
        to = (to or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not to or not subject or not body:
            return "Error: to/subject/body are required."
        from_addr = os.getenv("CORECODER_EMAIL_ADDRESS", "").strip()
        smtp, err = self._smtp_connect()
        if err:
            return err
        assert smtp is not None
        try:
            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            smtp.send_message(msg)
            return f"Email sent to {to} with subject: {subject}"
        except Exception as exc:
            return f"Error: send failed: {exc}"
        finally:
            try:
                smtp.quit()
            except Exception:
                pass


class GmailReplyEmailTool(_IMAPBaseTool, _SMTPBaseTool):
    name = "gmail_reply_email"
    description = "Reply to an email by IMAP uid via SMTP."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "IMAP UID of original email."},
            "body": {"type": "string", "description": "Reply body text."},
            "mailbox": {"type": "string", "description": "IMAP mailbox, default INBOX."},
        },
        "required": ["uid", "body"],
    }

    def execute(self, uid: str, body: str, mailbox: str = "INBOX", **_kwargs) -> str:
        uid = (uid or "").strip()
        body = (body or "").strip()
        if not uid or not body:
            return "Error: uid/body are required."
        imap, err = self._imap_connect()
        if err:
            return err
        assert imap is not None
        try:
            typ, _ = imap.select(mailbox, readonly=True)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            src = self._fetch_message(imap, uid)
            if src is None:
                return f"Error: message uid={uid} not found."
            to = _decode_mime_words(src.get("Reply-To") or src.get("From"))
            subject = _decode_mime_words(src.get("Subject") or "")
            msgid = _decode_mime_words(src.get("Message-ID") or "")
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        if not to:
            return "Error: cannot resolve recipient from source email."
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        from_addr = os.getenv("CORECODER_EMAIL_ADDRESS", "").strip()
        smtp, serr = self._smtp_connect()
        if serr:
            return serr
        assert smtp is not None
        try:
            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to
            msg["Subject"] = subject
            if msgid:
                msg["In-Reply-To"] = msgid
                msg["References"] = msgid
            msg.set_content(body)
            smtp.send_message(msg)
            return f"Reply sent for uid={uid} to {to}"
        except Exception as exc:
            return f"Error: reply failed: {exc}"
        finally:
            try:
                smtp.quit()
            except Exception:
                pass


class GmailMarkReadTool(_IMAPBaseTool):
    name = "gmail_mark_read"
    description = "Mark an email as read by IMAP uid."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "IMAP UID."},
            "mailbox": {"type": "string", "description": "Mailbox, default INBOX."},
        },
        "required": ["uid"],
    }

    def execute(self, uid: str, mailbox: str = "INBOX", **_kwargs) -> str:
        uid = (uid or "").strip()
        if not uid:
            return "Error: uid is required."
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            typ, _ = client.uid("store", uid, "+FLAGS", "(\\Seen)")
            if typ != "OK":
                return f"Error: mark read failed for uid={uid}"
            return f"Marked read: uid={uid}"
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass


class GmailDeleteEmailTool(_IMAPBaseTool):
    name = "gmail_delete_email"
    description = "Delete an email by IMAP uid (mark deleted + expunge)."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "IMAP UID."},
            "mailbox": {"type": "string", "description": "Mailbox, default INBOX."},
        },
        "required": ["uid"],
    }

    def execute(self, uid: str, mailbox: str = "INBOX", **_kwargs) -> str:
        uid = (uid or "").strip()
        if not uid:
            return "Error: uid is required."
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            typ, _ = client.uid("store", uid, "+FLAGS", "(\\Deleted)")
            if typ != "OK":
                return f"Error: delete flag failed for uid={uid}"
            client.expunge()
            return f"Deleted email uid={uid}"
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass


class GmailMoveToTrashTool(_IMAPBaseTool):
    name = "gmail_move_to_trash"
    description = "Move email to trash mailbox by uid."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "IMAP UID."},
            "mailbox": {"type": "string", "description": "Source mailbox, default INBOX."},
            "trash_mailbox": {"type": "string", "description": "Trash mailbox name."},
        },
        "required": ["uid"],
    }

    def execute(self, uid: str, mailbox: str = "INBOX", trash_mailbox: str = "", **_kwargs) -> str:
        uid = (uid or "").strip()
        if not uid:
            return "Error: uid is required."
        trash = (trash_mailbox or os.getenv("CORECODER_IMAP_TRASH_MAILBOX", "")).strip() or "Trash"
        client, err = self._imap_connect()
        if err:
            return err
        assert client is not None
        try:
            typ, _ = client.select(mailbox)
            if typ != "OK":
                return f"Error: cannot select mailbox {mailbox}"
            typ, _ = client.uid("copy", uid, f'"{trash}"')
            if typ != "OK":
                return f"Error: copy to trash failed for uid={uid}, trash={trash}"
            client.uid("store", uid, "+FLAGS", "(\\Deleted)")
            client.expunge()
            return f"Moved to trash: uid={uid}, trash={trash}"
        except Exception as exc:
            return f"Error: {exc}"
        finally:
            try:
                client.logout()
            except Exception:
                pass

