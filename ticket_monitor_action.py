"""
Ticket monitor script for GitHub Actions

This script is designed to be used in a scheduled GitHub Actions workflow to
periodically check a Twickets event page and send notifications when tickets
become available.  It uses simple HTML scraping rather than Selenium to
minimise dependencies and ensure it runs reliably on GitHub's hosted Linux
environment.

How it works:

* The script fetches the event page using the requests library.  It looks
  for key phrases that indicate tickets are NOT available, such as
  "Sorry, we don't currently have any tickets" or "no results found".  If
  those phrases are absent, it assumes that at least one listing exists.

* When tickets are detected, the script will send an email and optionally
  an SMS using Textbelt (a free SMS API).  The email is sent via an SMTP
  server configured in environment variables.  For example, to use
  Outlook/Hotmail you can set:

      SMTP_SERVER=smtp.office365.com
      SMTP_PORT=587
      SMTP_USERNAME=your_email@outlook.com
      SMTP_PASSWORD=your_app_password

  For Gmail you would set SMTP_SERVER to smtp.gmail.com and use an
  app-specific password (see Google's documentation on App Passwords).

* The phone number for SMS notifications should be provided as an
  international number without a leading + (e.g. 447912345678 for a UK
  mobile).  You can obtain one free daily SMS per phone number using the
  default Textbelt API key.  If you need more messages, you can purchase a
  Textbelt key and set TEXTBELT_KEY accordingly.

* A simple state file is written to the working directory to remember
  whether tickets were available on the previous run.  This prevents
  duplicate notifications when the workflow runs repeatedly.  The state is
  stored in JSON and can be cached between workflow runs using the
  actions/cache step in your workflow configuration.

Environment variables required:

    EVENT_URL      – The Twickets event URL to monitor.
    SMTP_SERVER    – Hostname of the SMTP server (e.g. smtp.office365.com).
    SMTP_PORT      – Port number for SMTP (e.g. 587 for TLS).
    SMTP_USERNAME  – Username for the SMTP server (usually your email).
    SMTP_PASSWORD  – Password or app password for the SMTP account.
    EMAIL_FROM     – From address used when sending email (should match
                     SMTP_USERNAME in most cases).
    EMAIL_TO       – Comma‑separated list of recipient email addresses.
    SMS_PHONE      – (Optional) International number for SMS notifications.
    TEXTBELT_KEY   – (Optional) API key for Textbelt.  Defaults to "textbelt"
                     for a free daily SMS.

Usage:

Run the script with `python ticket_monitor_action.py`.  It will check the
page and exit after sending notifications if necessary.  To use it on
GitHub Actions, schedule it using the `cron` trigger and make sure to
restore/cache the state file if you want to avoid duplicate alerts.
"""

import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests
from bs4 import BeautifulSoup


def log(msg: str) -> None:
    """Prints log messages prefixed with the script name."""
    print(f"[ticket_monitor] {msg}")


def send_email(subject: str, message: str) -> None:
    """Send an email notification using SMTP settings from environment."""
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("EMAIL_FROM", username)
    to_addrs = os.environ.get("EMAIL_TO")

    if not all([smtp_server, username, password, to_addrs]):
        log("Email configuration incomplete; skipping email notification.")
        return

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addrs
    msg["Subject"] = subject
    msg.attach(MIMEText(message, "plain"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(username, password)
        server.sendmail(from_addr, to_addrs.split(","), msg.as_string())
        server.quit()
        log("Email notification sent.")
    except Exception as e:
        log(f"Failed to send email: {e}")


def send_sms(message: str) -> None:
    """Send an SMS using the Textbelt API if a phone number is configured."""
    phone = os.environ.get("SMS_PHONE")
    if not phone:
        return
    key = os.environ.get("TEXTBELT_KEY", "textbelt")
    try:
        resp = requests.post(
            "https://textbelt.com/text",
            data={"phone": phone, "message": message, "key": key},
            timeout=15,
        )
        data = resp.json()
        if data.get("success"):
            log("SMS notification sent.")
        else:
            log(f"SMS failed: {data}")
    except Exception as e:
        log(f"Failed to send SMS: {e}")


def check_tickets(url: str) -> bool:
    """Check if tickets are available by scraping the event page.

    Returns True if tickets appear to be available; False if not.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TicketMonitor/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            log(f"Received status code {response.status_code} from event page.")
            return False
        html = response.text.lower()
        # If phrases indicating no tickets are present, return False
        no_phrases = [
            "sorry, we don't currently have any tickets",
            "no results found",
            "alerts not currently available",
        ]
        for phrase in no_phrases:
            if phrase in html:
                return False
        # Otherwise assume tickets are available
        return True
    except Exception as e:
        log(f"Error fetching event page: {e}")
        return False


def load_state(path: str) -> Optional[dict]:
    """Load state from a JSON file if it exists."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Failed to read state file: {e}")
        return None


def save_state(path: str, state: dict) -> None:
    """Save state to a JSON file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"Failed to write state file: {e}")


def main() -> None:
    event_url = os.environ.get("EVENT_URL")
    if not event_url:
        log("EVENT_URL environment variable is not set; exiting.")
        sys.exit(1)

    state_file = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_file) or {}
    last_status = state.get("has_tickets")

    has_tickets = check_tickets(event_url)
    log(f"Tickets available: {has_tickets}")

    # Send notifications only when tickets are newly available
    if has_tickets and not last_status:
        subject = "Twickets Alert: Tickets Available!"
        body = (
            f"Tickets are now available for your event!\n"
            f"Event URL: {event_url}\n\n"
            f"Please visit the page quickly to purchase them.\n"
            f"This is an automated notification."
        )
        send_email(subject, body)
        send_sms(body)

    # Update state
    save_state(state_file, {"has_tickets": has_tickets})


if __name__ == "__main__":
    main()
