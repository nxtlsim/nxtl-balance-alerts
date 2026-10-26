#!/usr/bin/env python3
"""nxtl-balance-alerts: check an NXTL Business pooled balance and alert below a threshold.

Built against the published NXTL API reference (https://nxtlsim.com/nxtl-api-reference/);
not yet tested against the live API. Maintained by NXTL eSIM (https://nxtlsim.com).

Endpoints used: GET /balance, GET /usage/summary (header X-NXTL-Key).
The reference does not publish the exact JSON fields of /balance, so the balance value
is located with --balance-field (dot path) or auto-detected from common key names.
"""
import argparse, json, os, smtplib, sys
from email.message import EmailMessage
import requests

API = os.environ.get("NXTL_API_BASE", "https://nxtlsim.com/api/v1")
CANDIDATES = ["pool.available", "pool.balance", "available", "balance", "pool_balance", "total", "amount"]


def get(path, key, params=None):
    r = requests.get(API + path, headers={"X-NXTL-Key": key, "Accept": "application/json"},
                     params=params, timeout=20)
    if r.status_code >= 400:
        sys.exit(f"NXTL API {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def find_balance(data, field=None):
    for p in ([field] if field else []) + CANDIDATES:
        v = dig(data, p)
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
            return float(v), p
    return None, None


def send_telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=20).raise_for_status()
        return "telegram"


def send_slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        requests.post(url, json={"text": text}, timeout=20).raise_for_status()
        return "slack"


def send_email(text):
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("ALERT_EMAIL_TO")
    if not (host and to):
        return None
    msg = EmailMessage()
    msg["Subject"] = "NXTL balance alert"
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", to))
    msg["To"] = to
    msg.set_content(text)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return "email"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Alert when the NXTL pooled balance drops below a threshold.")
    ap.add_argument("--threshold", type=float, default=float(os.environ.get("NXTL_THRESHOLD", "20")),
                    help="alert when balance is below this amount (account currency)")
    ap.add_argument("--balance-field", default=os.environ.get("NXTL_BALANCE_FIELD"),
                    help="dot path to the balance number in the /balance response, e.g. pool.available")
    ap.add_argument("--days", type=int, default=7, help="days for the /usage/summary window")
    ap.add_argument("--always", action="store_true", help="send a message even when above threshold")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    ap.add_argument("--json", action="store_true", help="print raw API responses")
    a = ap.parse_args(argv)

    key = os.environ.get("NXTL_API_KEY")
    if not key:
        sys.exit("Set NXTL_API_KEY (API keys are issued to approved NXTL Business accounts).")
    bal = get("/balance", key)
    usage = get("/usage/summary", key, {"days": a.days})
    if a.json:
        print(json.dumps({"balance": bal, "usage_summary": usage}, indent=2))
    value, used_field = find_balance(bal, a.balance_field)
    if value is None:
        sys.exit("Could not find a numeric balance. Run with --json and set --balance-field.")

    low = value < a.threshold
    text = (f"{'LOW' if low else 'OK'}: NXTL pooled balance {value:.2f} "
            f"(threshold {a.threshold:.2f}, field '{used_field}').\n"
            f"Usage summary ({a.days} days): {json.dumps(usage)[:600]}")
    print(text)
    if (low or a.always) and not a.dry_run:
        sent = [c for c in (send_telegram(text), send_slack(text), send_email(text)) if c]
        print("Sent via: " + (", ".join(sent) if sent else "no channel configured"))
    return 2 if low else 0


if __name__ == "__main__":
    sys.exit(main())
