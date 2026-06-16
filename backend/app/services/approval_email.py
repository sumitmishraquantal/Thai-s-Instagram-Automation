"""Send approval emails.

Localhost reality: the connected Gmail MCP can only draft/search, not send, and
an MCP isn't reachable from a background pipeline thread anyway. So we send via
SMTP, which works from any local process. For Gmail SMTP, use an App Password
(Google account → Security → App passwords), NOT your normal password.

Set in backend/.env:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=youraddress@gmail.com
    SMTP_PASSWORD=your-16-char-app-password
    APPROVAL_BASE_URL=http://localhost:8000   # where approve/decline links point
    OWNER_EMAILS=owner1@x.com,owner2@y.com

If SMTP isn't configured, emails are written to backend/assets/sent_emails/ as
.html files instead (so you can still see/click them locally during testing).
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

FALLBACK_DIR = Path(__file__).resolve().parent.parent / "assets" / "sent_emails"
FALLBACK_DIR.mkdir(parents=True, exist_ok=True)


def _approval_html(*, record: dict, base_url: str) -> str:
    p = record.get("payload", {})
    title = p.get("title", "Untitled")
    script_lines = p.get("script_lines", [])
    audio_url = p.get("audio_url")
    workflow = record["workflow"]

    # Per-owner links are added when sending (token differs per owner), so leave a
    # placeholder the sender fills in.
    rows = ""
    for ln in script_lines:
        who = ln.get("speaker", "")
        txt = ln.get("text", "")
        color = "#2563eb" if who.upper() == "HOST" else "#059669"
        rows += (f'<tr><td style="padding:4px 10px;font-weight:700;color:{color};'
                 f'vertical-align:top;white-space:nowrap">{who}</td>'
                 f'<td style="padding:4px 10px;color:#111">{txt}</td></tr>')

    audio_block = (
        f'<p style="margin:14px 0"><a href="{audio_url}" '
        f'style="color:#2563eb;font-weight:600">▶ Listen to the full audio</a></p>'
        if audio_url else "")

    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;padding:28px;border:1px solid #e5e7eb">
    <h2 style="margin:0 0 6px;color:#111">Approval needed — {workflow.title()}</h2>
    <p style="color:#555;margin:0 0 18px">"{title}"</p>

    {audio_block}

    <h3 style="color:#111;margin:18px 0 6px">Full script</h3>
    <table style="border-collapse:collapse;width:100%;font-size:14px;line-height:1.5">{rows}</table>

    <div style="margin:26px 0 6px">
      <a href="{base_url}/api/approvals/act?token=__TOKEN__&action=approve"
         style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
                padding:12px 26px;border-radius:8px;font-weight:700;margin-right:10px">✓ Approve</a>
      <a href="{base_url}/api/approvals/act?token=__TOKEN__&action=decline"
         style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;
                padding:12px 26px;border-radius:8px;font-weight:700">✕ Decline</a>
    </div>
    <p style="color:#999;font-size:12px;margin-top:18px">
      First response decides. If the other owner has already responded, your click
      will simply show the current status — nothing is regenerated.
    </p>
  </div></body></html>"""



def _send_via_composio(owner: str, subject: str, html: str) -> str | None:
    """Send one HTML email through Composio's Gmail tool (automated, no SMTP).
    Returns None on success, or an error string on failure (so the caller can
    fall back to SMTP/file)."""
    s = get_settings()
    if not s.composio_api_key:
        return "composio_api_key not set"
    try:
        from composio import Composio
    except Exception as e:  # noqa: BLE001
        return f"composio SDK not installed ({e}); run: pip install composio"

    # Your installed SDK exposes `dangerously_skip_version_check` ON THE EXECUTE
    # call (confirmed via inspect.signature(client.tools.execute)). That's the
    # reliable lever: it tells Composio not to require a pinned toolkit version,
    # which is exactly what the "version=latest not supported" error wants.
    # We also support an explicit version string and the env var, for other SDK
    # builds, trying the most compatible form first and falling back.
    tk_version = getattr(s, "composio_toolkit_version", "") or ""
    if tk_version:
        os.environ.setdefault("COMPOSIO_TOOLKIT_VERSION_GMAIL", tk_version)

    try:
        client = Composio(api_key=s.composio_api_key)
    except Exception as e:  # noqa: BLE001
        return f"composio client init failed: {e}"

    args = {
        "recipient_email": owner,
        "subject": subject,
        "body": html,
        "is_html": True,
    }
    if s.gmail_sender:
        args["from_email"] = s.gmail_sender

    # user_id is the Gmail entity; "me" = the authenticated/connected account.
    user_id = s.composio_user_id or "me"

    base = {"user_id": user_id, "arguments": args}
    # Try, in order: skip-version-check (works on your SDK), explicit version,
    # then a plain call (in case the env var already pinned it).
    attempts = [
        {**base, "dangerously_skip_version_check": True},
    ]
    if tk_version:
        attempts.append({**base, "version": tk_version})
    attempts.append(dict(base))

    result = None
    last_err = None
    for kw in attempts:
        try:
            result = client.tools.execute("GMAIL_SEND_EMAIL", **kw)
            last_err = None
            break
        except TypeError as e:
            # this SDK build doesn't accept that kwarg — try the next form
            last_err = e
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    if result is None:
        return f"composio send error: {last_err}"

    # Response may be a ToolExecutionResponse object OR a dict; normalise both.
    def _field(obj, name):
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    successful = _field(result, "successful")
    if successful is None:
        successful = _field(result, "success")
    if successful:
        return None
    err = _field(result, "error") or str(result)[:200]
    return f"composio returned not-successful: {err}"


def send_approval_emails(record: dict) -> dict:
    """Email each owner their own approve/decline link. Returns a small report."""
    s = get_settings()
    base_url = s.approval_base_url
    html_template = _approval_html(record=record, base_url=base_url)

    results = {}
    transport = (s.email_transport or "auto").lower()

    # Decide which transports are allowed, in priority order.
    want_gmail = transport in ("auto", "gmail")
    want_smtp = transport in ("auto", "smtp")
    want_file = transport in ("auto", "file")

    # Open an SMTP session once if we may need it.
    smtp_ready = want_smtp and bool(s.smtp_host and s.smtp_user and s.smtp_password)
    server = None
    if smtp_ready:
        try:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            server.starttls()
            server.login(s.smtp_user, s.smtp_password)
        except Exception as e:  # noqa: BLE001
            logger.error("SMTP connect/login failed (%s)", e)
            server = None
            smtp_ready = False

    for owner, token in record["owner_tokens"].items():
        html = html_template.replace("__TOKEN__", token)
        subject = f"[Approval] {record['workflow'].title()}: {record['payload'].get('title','')}"
        sent = False

        # 1) Gmail via Composio (preferred — automated, no SMTP/app-password)
        if want_gmail and not sent:
            err = _send_via_composio(owner, subject, html)
            if err is None:
                results[owner] = "sent (gmail/composio)"
                sent = True
            else:
                logger.warning("Gmail/Composio send to %s failed: %s", owner, err)

        # 2) SMTP
        if want_smtp and not sent and smtp_ready and server is not None:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = s.smtp_user
                msg["To"] = owner
                msg.attach(MIMEText(html, "html"))
                server.sendmail(s.smtp_user, [owner], msg.as_string())
                results[owner] = "sent (smtp)"
                sent = True
            except Exception as e:  # noqa: BLE001
                logger.warning("SMTP send to %s failed: %s", owner, e)

        # 3) File fallback (always works locally so you can click the links)
        if want_file and not sent:
            f = FALLBACK_DIR / f"{record['id']}_{owner.replace('@','_at_')}.html"
            f.write_text(html, encoding="utf-8")
            results[owner] = f"written to {f}"
            sent = True

        if not sent:
            results[owner] = "FAILED: no transport succeeded"

    if server is not None:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass

    logger.info("Approval %s emails: %s", record["id"], results)
    return results
