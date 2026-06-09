from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


DEFAULT_EMAIL = "1341797778@qq.com"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_message(subject: str, body: str) -> EmailMessage:
    sender = os.environ.get("MAIL_SENDER", DEFAULT_EMAIL)
    recipient = os.environ.get("MAIL_RECIPIENT", DEFAULT_EMAIL)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return message


def send_email(message: EmailMessage) -> None:
    host = os.environ.get("MAIL_SMTP_HOST", "smtp.qq.com")
    port = int(os.environ.get("MAIL_SMTP_PORT", "465"))
    username = os.environ.get("MAIL_USERNAME", DEFAULT_EMAIL)
    password = get_required_env("MAIL_PASSWORD")

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(username, password)
            server.send_message(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a plain text email.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        load_env_file(args.env_file)
    else:
        load_env_file(Path.cwd() / ".env")

    body = args.body_file.read_text(encoding="utf-8").strip()
    if not body:
        raise RuntimeError(f"Email body file is empty: {args.body_file}")

    message = build_message(args.subject, body)
    send_email(message)
    print(f"Email sent to {message['To']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
