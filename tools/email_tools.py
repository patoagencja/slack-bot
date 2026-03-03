"""Email tools (IMAP/SMTP) — no Slack app dependency."""
import os
import re
import json
import email
import logging
import smtplib
from datetime import datetime, timedelta
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from imapclient import IMAPClient

logger = logging.getLogger(__name__)


def get_user_email_config(user_id):
    """Pobierz konfigurację email dla danego użytkownika"""
    email_accounts_json = os.environ.get("EMAIL_ACCOUNTS", "{}")
    try:
        return json.loads(email_accounts_json).get(user_id)
    except json.JSONDecodeError:
        logger.error("Błąd parsowania EMAIL_ACCOUNTS")
        return None


def email_tool(user_id, action, **kwargs):
    """Zarządza emailami użytkownika. action: 'read' | 'send' | 'search'"""
    email_config = get_user_email_config(user_id)
    if not email_config:
        return {"error": "Nie masz skonfigurowanego konta email. Skontaktuj się z administratorem."}
    try:
        if action == "read":
            return read_emails(email_config, kwargs.get('limit', 10), kwargs.get('folder', 'INBOX'))
        elif action == "send":
            return send_email(email_config, kwargs.get('to'), kwargs.get('subject'), kwargs.get('body'))
        elif action == "search":
            return search_emails(email_config, kwargs.get('query'), kwargs.get('limit', 10))
        else:
            return {"error": f"Nieznana akcja: {action}"}
    except Exception as e:
        logger.error(f"Błąd email tool: {e}")
        return {"error": str(e)}


def read_emails(config, limit=10, folder='INBOX'):
    """Odczytaj najnowsze emaile"""
    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])
            client.select_folder(folder)

            messages = client.search(['ALL'])
            messages = messages[-limit:] if len(messages) > limit else messages

            emails_data = []
            for uid in reversed(messages):
                raw_message = client.fetch([uid], ['RFC822'])[uid][b'RFC822']
                msg = email.message_from_bytes(raw_message)

                def _decode_header_field(value):
                    parts = []
                    for part, charset in decode_header(value or ''):
                        if isinstance(part, bytes):
                            parts.append(part.decode(charset or 'utf-8', errors='replace'))
                        else:
                            parts.append(part or '')
                    return ''.join(parts)

                def _decode_payload(part_or_msg):
                    raw = part_or_msg.get_payload(decode=True)
                    if not raw:
                        return ""
                    charset = part_or_msg.get_content_charset()
                    for enc in [charset, 'utf-8', 'latin-1', 'cp1250', 'iso-8859-2']:
                        if not enc:
                            continue
                        try:
                            return raw.decode(enc)
                        except (UnicodeDecodeError, LookupError):
                            continue
                    return raw.decode('utf-8', errors='replace')

                subject = _decode_header_field(msg['Subject'])
                sender  = _decode_header_field(msg['From'])

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = _decode_payload(part)
                            break
                else:
                    body = _decode_payload(msg)

                is_newsletter = bool(
                    msg.get('List-Unsubscribe') or msg.get('List-Id') or
                    msg.get('X-Mailchimp-ID') or msg.get('X-Campaign') or
                    (msg.get('Precedence', '').lower() in ['bulk', 'list', 'junk'])
                )

                emails_data.append({
                    "from":         sender,
                    "subject":      subject,
                    "date":         msg['Date'],
                    "body_preview": body[:200] + "..." if len(body) > 200 else body,
                    "is_newsletter": is_newsletter,
                })

            return {"folder": folder, "count": len(emails_data), "emails": emails_data}

    except Exception as e:
        return {"error": f"Błąd odczytu emaili: {str(e)}"}


def _normalize_subject(subject):
    """Usuwa prefixes Re:/Fwd:/Odp: i whitespace żeby porównać wątki."""
    subject = subject or ""
    subject = re.sub(r'^(Re|Fwd|FW|Odp|ODP|AW|SV|VS)(\s*\[\d+\])?:\s*',
                     '', subject, flags=re.IGNORECASE).strip()
    return subject.lower()


def find_unreplied_emails(config, received_emails, days_back=3):
    """Sprawdza które z podanych emaili nie mają odpowiedzi w folderze SENT."""
    SENT_FOLDERS = [
        "Sent", "SENT", "Sent Items", "Sent Messages",
        "[Gmail]/Sent Mail", "INBOX.Sent", "Poczta wysłana",
    ]
    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])

            sent_folder = None
            for folder in SENT_FOLDERS:
                try:
                    client.select_folder(folder, readonly=True)
                    sent_folder = folder
                    break
                except Exception:
                    continue

            if not sent_folder:
                logger.warning("Nie znaleziono folderu SENT — pomijam sprawdzanie odpowiedzi")
                return []

            since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
            sent_uids  = client.search(['SINCE', since_date])

            sent_subjects = set()
            for uid in sent_uids:
                try:
                    raw = client.fetch([uid], ['RFC822.HEADER'])[uid][b'RFC822.HEADER']
                    sent_msg = email.message_from_bytes(raw)
                    parts = decode_header(sent_msg.get('Subject', '') or '')
                    s_parts = []
                    for p, ch in parts:
                        if isinstance(p, bytes):
                            s_parts.append(p.decode(ch or 'utf-8', errors='replace'))
                        else:
                            s_parts.append(p or '')
                    sent_subjects.add(_normalize_subject(''.join(s_parts)))
                except Exception:
                    continue

            unreplied = []
            for em in received_emails:
                normalized = _normalize_subject(em.get('subject', ''))
                if normalized not in sent_subjects:
                    days_waiting = 0
                    try:
                        from email.utils import parsedate_to_datetime
                        em_date = parsedate_to_datetime(em['date']).date()
                        days_waiting = (datetime.now().date() - em_date).days
                    except Exception:
                        pass
                    unreplied.append({**em, 'days_waiting': days_waiting})

            return unreplied

    except Exception as e:
        logger.error(f"Błąd find_unreplied_emails: {e}")
        return []


def send_email(config, to, subject, body):
    """Wyślij email"""
    try:
        signature = os.environ.get("EMAIL_SIGNATURE", "")
        if signature:
            body = f"{body}\n\n{signature}"

        msg = MIMEMultipart()
        msg['From']    = config['email']
        msg['To']      = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP_SSL(config['smtp_server'], 465) as server:
            server.login(config['email'], config['password'])
            server.send_message(msg)

        return {"success": True, "message": f"Email wysłany do {to}", "subject": subject}
    except Exception as e:
        return {"error": f"Błąd wysyłania emaila: {str(e)}"}


def search_emails(config, query, limit=10):
    """Szukaj emaili po frazie"""
    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])
            client.select_folder('INBOX')

            messages = client.search(['OR', 'SUBJECT', query, 'BODY', query])
            messages = messages[-limit:] if len(messages) > limit else messages

            emails_data = []
            for uid in reversed(messages):
                raw_message = client.fetch([uid], ['RFC822'])[uid][b'RFC822']
                msg = email.message_from_bytes(raw_message)

                subject = decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()

                emails_data.append({"from": msg['From'], "subject": subject, "date": msg['Date']})

            return {"query": query, "count": len(emails_data), "emails": emails_data}
    except Exception as e:
        return {"error": f"Błąd wyszukiwania: {str(e)}"}
