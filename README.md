# **email2tg**:  Email notifications to Telegram.

[![GitHub Stars](https://img.shields.io/github/stars/cloud65/email2tg?style=flat-square)](https://github.com/cloud65/email2tg/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/cloud65/email2tg?style=flat-square)](https://github.com/cloud65/email2tg/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/cloud65/email2tg?style=flat-square)](https://github.com/cloud65/email2tg/issues)
[![License](https://img.shields.io/github/license/cloud65/email2tg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](Dockerfile)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-ready-326CE5?style=flat-square&logo=kubernetes&logoColor=white)](manifest-example.yaml)


<img height="128" src="./logo/logo1024.png" title="email2image" width="128"/>



`email2tg` is a lightweight service that monitors an IMAP mailbox and sends notifications about new emails to a Telegram chat.

The service is designed to run continuously in Docker or Kubernetes and keeps track of already processed messages using SQLite.

## Features

- 📬 Monitor IMAP mailboxes for new messages
- 📁 Automatically discover subfolders under the configured IMAP root
- 📱 Send new email notifications to Telegram
- 💾 Persist processed message state in SQLite
- 🔄 Automatically reconnect to IMAP after connection failures
- 🛡️ Fetch email headers without marking messages as read
- 🧹 Perform an initial synchronization without notifying about existing emails
- 🌐 Optional SOCKS5 proxy support
- ❤️ Health file for basic container/service monitoring
- 🐳 Ready to run with Docker
- ☸️ Example Kubernetes manifest included

## How it works

```text
┌──────────────┐
│   IMAP mail  │
│    server    │
└──────┬───────┘
       │
       │ IMAP / SSL
       ▼
┌──────────────┐
│   email2tg   │
│              │
│  IMAP poll   │
│  + SQLite    │
└──────┬───────┘
       │
       │ Telegram Bot API
       ▼
┌──────────────┐
│   Telegram   │
│     chat     │
└──────────────┘
```

On the first start, existing messages are synchronized and marked as processed. They are **not** sent to Telegram. Only new messages detected afterwards generate notifications.

## Quick start

### Docker

Build the image:

```bash
docker build -t email2tg .
```

Run the service:

```bash
docker run -d \
  --name email2tg \
  -e IMAP_HOST=imap.example.com \
  -e IMAP_PORT=993 \
  -e IMAP_USER=your@email.com \
  -e IMAP_PASSWORD=your-password \
  -e TELEGRAM_TOKEN=123456:ABCDEF \
  -e TELEGRAM_CHAT_ID=123456789 \
  -v email2tg-data:/data \
  email2tg
```

The database is stored in `/data/state.db`.

### Docker Compose

Example:

```yaml
services:
  email2tg:
    build: .
    restart: unless-stopped
    environment:
      IMAP_HOST: imap.example.com
      IMAP_PORT: "993"
      IMAP_USER: your@email.com
      IMAP_PASSWORD: your-password
      TELEGRAM_TOKEN: "123456:ABCDEF"
      TELEGRAM_CHAT_ID: "123456789"
      IMAP_ROOT: INBOX
      POLL_INTERVAL: "30"
      RECONNECT_DELAY: "10"
      DB_PATH: /data/state.db
    volumes:
      - ./data:/data
```

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `IMAP_HOST` | — | IMAP server hostname |
| `IMAP_PORT` | `993` | IMAP server port |
| `IMAP_USER` | — | IMAP username |
| `IMAP_PASSWORD` | — | IMAP password |
| `IMAP_ROOT` | `INBOX` | Root mailbox to monitor |
| `TELEGRAM_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram destination chat ID |
| `SOCKS5_PROXY` | empty | Optional SOCKS5 proxy |
| `POLL_INTERVAL` | `30` | Mail polling interval in seconds |
| `RECONNECT_DELAY` | `10` | Delay before reconnecting after an error |
| `DB_PATH` | `state.db` | SQLite database path |
| `HEALTH_FILE` | `/tmp/email2tg-health` | Health timestamp file |
| `LOG_LEVEL` | `INFO` | Logging level |

Required variables are:

- `IMAP_HOST`
- `IMAP_USER`
- `IMAP_PASSWORD`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Kubernetes

An example Kubernetes deployment is provided in [`manifest-example.yaml`](manifest-example.yaml).

It includes:

- Namespace
- Secret for credentials
- ConfigMap for configuration
- PersistentVolumeClaim for SQLite state
- Deployment
- Resource requests and limits

Before deploying, fill in the required IMAP and Telegram values in the manifest.

```bash
kubectl apply -f manifest-example.yaml
```

The example uses the `cloud65/mail2tg:arm64` image and persists the SQLite database under `/data`.

## Telegram notifications

For every new email, `email2tg` sends a notification containing:

- mailbox/folder
- sender
- subject
- date

Only email headers are fetched from the IMAP server, using `BODY.PEEK`, so checking messages does not change their read/unread state.

## Reliability

Processed messages are stored by mailbox and IMAP UID in SQLite. If sending a Telegram notification fails, the message is not marked as processed and will be retried during the next polling cycle.

The service also periodically checks the IMAP connection and reconnects automatically after failures.

## Security

- Store IMAP and Telegram credentials in environment variables or Kubernetes Secrets.
- Do not commit passwords or bot tokens to the repository.
- Use a persistent volume for `DB_PATH` when running in containers.
- The Docker image runs the application as a non-root user.

## Requirements

For local development:

- Python 3.13+
- `requests`
- `PySocks` (only required when using SOCKS5 proxy support)

The Docker image is based on `python:3.13-slim`.

## License

This project is licensed under the **MIT License**.

See [`LICENSE`](LICENSE) for the full license text.