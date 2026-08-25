import email
import imaplib
import logging
import os
import re
import sqlite3
import time
from email.header import decode_header

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMAP_HOST = os.environ.get("IMAP_HOST", )
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ["IMAP_PASSWORD"]

IMAP_ROOT = os.environ.get("IMAP_ROOT", "INBOX")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

RECONNECT_DELAY = int(os.environ.get("RECONNECT_DELAY", "10"))

DB_PATH = os.environ.get("DB_PATH", "state.db")

HEALTH_FILE = os.environ.get("HEALTH_FILE", "/tmp/email2tg-health")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(), format=("%(asctime)s " "%(levelname)s " "%(name)s: " "%(message)s")
)

logger = logging.getLogger("mail-telegram-notify")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def update_health():
    try:
        with open(HEALTH_FILE, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        logger.exception("Unable to update health file")

def init_db():
    path = os.path.dirname(DB_PATH)

    if path:
        os.makedirs(path, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed (
            mailbox TEXT NOT NULL,
            uid INTEGER NOT NULL,
            PRIMARY KEY (mailbox, uid)
        )
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

    conn.commit()

    logger.info("Database initialized: %s", DB_PATH)

    return conn


def already_processed(conn, mailbox, uid):
    row = conn.execute(
        """
        SELECT 1
        FROM processed
        WHERE mailbox = ? AND uid = ?
        """,
        (mailbox, uid),
    ).fetchone()

    return row is not None


def mark_processed(conn, mailbox, uid):
    conn.execute(
        """
        INSERT OR IGNORE INTO processed (
            mailbox,
            uid
        )
        VALUES (?, ?)
        """,
        (mailbox, uid),
    )

    conn.commit()


def get_state(conn, key):
    row = conn.execute(
        """
        SELECT value
        FROM state
        WHERE key = ?
        """,
        (key,),
    ).fetchone()

    return row[0] if row else None


def set_state(conn, key, value):
    conn.execute(
        """
        INSERT INTO state (
            key,
            value
        )
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )

    conn.commit()


# ---------------------------------------------------------------------------
# IMAP mailbox handling
# ---------------------------------------------------------------------------


def list_mailboxes(conn, parent="INBOX"):
    """
    Return parent mailbox and all its children.

    Mailbox names are intentionally NOT decoded from Modified UTF-7.
    We keep exactly the representation returned by the IMAP server and
    pass it back to SELECT.
    """

    status, rows = conn.list("", f"{parent}*")
    if status != "OK":
        raise RuntimeError(f"IMAP LIST failed: {status}")

    result = []
    for row in rows:
        if not row:
            continue

        line = row.decode("ascii", errors="replace")

        logger.debug("IMAP LIST: %s", line)

        match = re.match(r'^\((.*?)\)\s+"(.*?)"\s+(.*)$', line)

        if not match:
            logger.warning("Unable to parse IMAP LIST response: %s", line)
            continue

        flags = match.group(1)
        name = match.group(3).strip()

        if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
            name = name[1:-1]

        if "\\Noselect" in flags:
            logger.debug("Skipping non-selectable mailbox: %s", name)
            continue

        if name == parent or name.startswith(parent + "/"):
            result.append(name)

    return sorted(set(result))


def get_all_uids(conn, mailbox):
    """
    Return all message UIDs from mailbox.
    """

    status, data = conn.select(mailbox, readonly=True)
    if status != "OK":
        logger.error("Cannot select mailbox %s: %s %s", mailbox, status, data)
        return []

    status, data = conn.uid("search", None, "ALL")
    if status != "OK":
        logger.error("IMAP SEARCH failed for %s: %s", mailbox, status)
        return []

    if not data or not data[0]:
        return []

    return [int(uid) for uid in data[0].split() if uid]


# ---------------------------------------------------------------------------
# Initial synchronization
# ---------------------------------------------------------------------------


def initial_sync(conn, db):
    """
    Mark all currently existing messages as processed.

    Existing messages will NOT generate Telegram notifications.
    """

    logger.info("Starting initial mailbox synchronization")

    mailboxes = list_mailboxes(conn, IMAP_ROOT)

    logger.info("Found %d mailboxes for initial sync", len(mailboxes))

    total_messages = 0

    try:
        db.execute("BEGIN")

        for mailbox in mailboxes:
            logger.info("Initializing mailbox: %s", mailbox)

            uids = get_all_uids(conn, mailbox)

            logger.info("Mailbox %s: %d existing messages", mailbox, len(uids))

            for uid in uids:
                db.execute(
                    """
                    INSERT OR IGNORE INTO processed (
                        mailbox,
                        uid
                    )
                    VALUES (?, ?)
                    """,
                    (mailbox, uid),
                )

            total_messages += len(uids)

        db.commit()

    except Exception:
        db.rollback()

        logger.exception("Initial mailbox synchronization failed")

        raise

    logger.info("Initial synchronization complete: " "%d messages marked as processed", total_messages)


# ---------------------------------------------------------------------------
# Email parsing
# ---------------------------------------------------------------------------


def decode_mime(value):
    if not value:
        return ""

    result = []

    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(part)

    return "".join(result)


def format_message(msg, mailbox):
    sender = decode_mime(msg.get("From", ""))
    subject = decode_mime(msg.get("Subject", ""))
    date = msg.get("Date", "")
    return "📬 Новое письмо\n\n" f"Папка: {mailbox}\n" f"От: {sender}\n" f"Тема: {subject or '(без темы)'}\n" f"Дата: {date}"


def fetch_message_header(conn, uid):
    """
    Fetch only message headers.

    BODY.PEEK prevents changing Seen/Unseen state.
    """

    status, data = conn.uid("fetch", str(uid), "(BODY.PEEK[HEADER])")
    if status != "OK":
        logger.error("Unable to fetch UID %s: %s", uid, status)
        return None

    raw = next((item[1] for item in data if isinstance(item, tuple)), None)
    if not raw:
        logger.warning("Empty header for UID %s", uid)
        return None

    return email.message_from_bytes(raw)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def telegram_send(text):
    url = "https://api.telegram.org/" f"bot{TELEGRAM_TOKEN}/sendMessage"

    proxies = {"http": SOCKS5_PROXY, "https": SOCKS5_PROXY}

    logger.debug("Sending Telegram message via proxy %s", SOCKS5_PROXY)

    response = requests.post(
        url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": "true"}, proxies=proxies, timeout=30
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


# ---------------------------------------------------------------------------
# Mail processing
# ---------------------------------------------------------------------------


def process_mailbox(conn, db, mailbox):
    logger.info("Checking mailbox: %s", mailbox)

    uids = get_all_uids(conn, mailbox)

    logger.debug("Mailbox %s contains %d messages", mailbox, len(uids))

    new_count = 0

    for uid in uids:
        if already_processed(db, mailbox, uid):
            continue

        logger.info("New message detected: " "%s UID %d", mailbox, uid)

        msg = fetch_message_header(conn, uid)
        if msg is None:
            continue

        text = format_message(msg, mailbox)

        try:
            telegram_send(text)
        except Exception:
            logger.exception("Telegram send failed for " "%s UID %d", mailbox, uid)
            # Do not mark as processed.
            # Retry on the next iteration.
            continue

        mark_processed(db, mailbox, uid)

        logger.info("Notification sent: " "%s UID %d", mailbox, uid)

        new_count += 1

    return new_count


def check_mail(conn, db):
    mailboxes = list_mailboxes(conn, IMAP_ROOT)

    logger.info("Found %d mailboxes under %s", len(mailboxes), IMAP_ROOT)

    logger.debug("Mailboxes: %s", ", ".join(mailboxes))

    total_new = 0

    for mailbox in mailboxes:
        try:
            total_new += process_mailbox(conn, db, mailbox)
        except Exception:
            logger.exception("Error processing mailbox %s", mailbox)

    if total_new:
        logger.info("Processed %d new messages", total_new)


# ---------------------------------------------------------------------------
# IMAP connection
# ---------------------------------------------------------------------------


def connect():
    logger.info("Connecting to IMAP %s:%d as %s", IMAP_HOST, IMAP_PORT, IMAP_USER)
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(IMAP_USER, IMAP_PASSWORD)
    logger.info("IMAP authentication successful")
    return conn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    logger.info("Starting mail-telegram-notify")
    logger.info("IMAP: %s:%d", IMAP_HOST, IMAP_PORT)
    logger.info("IMAP root mailbox: %s", IMAP_ROOT)
    logger.info("Telegram SOCKS proxy: %s", SOCKS5_PROXY)
    logger.info("Poll interval: %d seconds", POLL_INTERVAL)

    db = init_db()
    # -----------------------------------------------------------------------
    # Initial synchronization
    # -----------------------------------------------------------------------

    if get_state(db, "initial_sync_complete") != "true":
        conn = None
        try:
            conn = connect()
            initial_sync(conn, db)
            set_state(db, "initial_sync_complete", "true")
            logger.info("Initial sync state saved")

        except Exception:
            logger.exception("Initial synchronization failed; " "service will retry on next start")
            raise

        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

    else:
        logger.info("Initial synchronization already completed")

    # -----------------------------------------------------------------------
    # Main polling loop
    # -----------------------------------------------------------------------

    while True:
        conn = None
        try:
            conn = connect()
            while True:
                check_mail(conn, db)

                update_health()

                status, _ = conn.noop()
                if status != "OK":
                    raise RuntimeError(f"IMAP NOOP failed: {status}")
                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break

        except Exception:
            logger.exception("IMAP loop failed; " "reconnecting in %d seconds", RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)

        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
    db.close()

    logger.info("Stopped")


if __name__ == "__main__":
    main()
