#!/usr/bin/env python3
"""
Optional email sender for the CanadaBuys monitor.

Sends the HTML report (with the .md and .csv attached) over SMTP.
Configured entirely through environment variables / GitHub Secrets so that
no credentials ever live in the repo. If the required secrets are missing,
maybe_send() is a no-op and the job still succeeds (the report is uploaded
as a workflow artifact regardless).

Required secrets to enable sending:
  SMTP_HOST     e.g. smtp.gmail.com
  SMTP_USER     the login / from address
  SMTP_PASS     an app password (NOT your normal password)
  EMAIL_TO      comma-separated recipient(s)

Optional:
  SMTP_PORT     default 587 (STARTTLS)
  EMAIL_FROM    default = SMTP_USER

Gmail note: create an "App password" (Google Account → Security → App passwords)
and use that as SMTP_PASS. The CI job never sees your real password.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate


def maybe_send(subject: str, html_body: str, attachments=None) -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to_addrs = os.environ.get("EMAIL_TO")

    if not all([host, user, password, to_addrs]):
        print("[email] SMTP secrets not fully configured — skipping send "
              "(report still saved as artifact).")
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    from_addr = os.environ.get("EMAIL_FROM", user)
    recipients = [a.strip() for a in to_addrs.split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("This report is best viewed as HTML. "
                    "Attachments include Markdown and CSV versions.")
    msg.add_alternative(html_body, subtype="html")

    for path in attachments or []:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            name = os.path.basename(path)
            subtype = "csv" if name.endswith(".csv") else "markdown"
            msg.add_attachment(data, maintype="text", subtype=subtype, filename=name)
        except FileNotFoundError:
            print(f"[email] attachment not found, skipping: {path}")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.ehlo()
            s.starttls(context=context)
            s.login(user, password)
            s.send_message(msg)
    print(f"[email] sent to {', '.join(recipients)}")
