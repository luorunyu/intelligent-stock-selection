"""将已保存的 Markdown/文本报告通过 SMTP 发送为纯文本邮件。"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


DEFAULT_EMAIL = "1341797778@qq.com"


def load_env_file(path: Path) -> None:
    """加载邮件配置文件中的 KEY=VALUE；不输出任何敏感值。"""
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
    """读取必填环境变量，缺失时中止发送而不是使用空凭据。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_message(subject: str, body: str) -> EmailMessage:
    """基于环境变量中的发件人/收件人构建纯文本邮件对象。"""
    sender = os.environ.get("MAIL_SENDER", DEFAULT_EMAIL)
    recipient = os.environ.get("MAIL_RECIPIENT", DEFAULT_EMAIL)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return message


def send_email(message: EmailMessage) -> None:
    """按端口选择 SSL 或 STARTTLS 连接，认证后发送邮件。"""
    host = os.environ.get("MAIL_SMTP_HOST", "smtp.qq.com")
    port = int(os.environ.get("MAIL_SMTP_PORT", "465"))
    username = os.environ.get("MAIL_USERNAME", DEFAULT_EMAIL)
    password = get_required_env("MAIL_PASSWORD")

    # QQ SMTP 常用 465 直连 SSL；其他端口使用 STARTTLS 升级连接。
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
    """定义邮件标题、正文文件和可选环境配置文件。"""
    parser = argparse.ArgumentParser(description="Send a plain text email.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    return parser.parse_args()


def main() -> int:
    """加载配置、读取非空正文、发送邮件并仅打印收件人。"""
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
