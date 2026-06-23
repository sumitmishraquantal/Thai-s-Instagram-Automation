"""Send approval emails via Composio Gmail, SMTP, or local HTML file fallback."""
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
    scene_clips = p.get("scene_clips", [])
    script_lines = p.get("script_lines", [])
    thumbnail_url = p.get("thumbnail_url")
    workflow = record["workflow"]

    thumb_block = ""
    if thumbnail_url:
        thumb_block = f"""
    <div style="margin:0 0 14px;padding:12px;background:#f9fafb;border-radius:8px;border:1px solid #e5e7eb">
      <p style="margin:0 0 8px;font-weight:700;color:#111">Thumbnail</p>
      <p style="margin:0">
        <a href="{thumbnail_url}" style="color:#2563eb;font-weight:600">▶ Open thumbnail in browser</a>
      </p>
    </div>"""

    clip_rows = ""
    for clip in scene_clips:
        label = clip.get("label", clip.get("name", "Clip"))
        url = clip.get("url", "")
        clip_rows += f"""
    <div style="margin:0 0 14px;padding:12px;background:#f9fafb;border-radius:8px;border:1px solid #e5e7eb">
      <p style="margin:0 0 8px;font-weight:700;color:#111">{label}</p>
      <video controls preload="metadata" width="100%" style="max-width:360px;border-radius:8px;background:#000"
             src="{url}"></video>
      <p style="margin:8px 0 0">
        <a href="{url}" style="color:#2563eb;font-weight:600">▶ Open {label} in browser</a>
      </p>
    </div>"""

    media_block = ""
    if thumb_block or clip_rows:
        media_block = f"""
    <h3 style="color:#111;margin:18px 0 8px">Scene clips for review</h3>
    {thumb_block}
    {clip_rows}
    <p style="color:#888;font-size:12px;margin:0 0 6px">
      Open thumbnail and clips in your browser to review them.
    </p>"""

    script_rows = ""
    for ln in script_lines:
        who = ln.get("speaker", "")
        txt = ln.get("text", "")
        color = "#2563eb" if who.upper() == "HOST" else "#059669"
        script_rows += (
            f'<tr><td style="padding:4px 10px;font-weight:700;color:{color};'
            f'vertical-align:top;white-space:nowrap">{who}</td>'
            f'<td style="padding:4px 10px;color:#111">{txt}</td></tr>'
        )

    script_block = ""
    if script_rows:
        script_block = f"""
    <h3 style="color:#111;margin:18px 0 6px">Full script</h3>
    <table style="border-collapse:collapse;width:100%;font-size:14px;line-height:1.5">{script_rows}</table>"""

    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;padding:28px;border:1px solid #e5e7eb">
    <h2 style="margin:0 0 6px;color:#111">Approval needed — {workflow.title()}</h2>
    <p style="color:#555;margin:0 0 18px">"{title}"</p>

    {media_block}

    {script_block}

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
    """Send one HTML email through Composio's Gmail tool."""
    s = get_settings()
    if not s.composio_api_key:
        return "composio_api_key not set"
    try:
        from composio import Composio
    except Exception as e:  # noqa: BLE001
        return f"composio SDK not installed ({e}); run: pip install composio"

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

    user_id = s.composio_user_id or "me"
    base = {"user_id": user_id, "arguments": args}
    attempts = [{**base, "dangerously_skip_version_check": True}]
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
            last_err = e
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    if result is None:
        return f"composio send error: {last_err}"

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

    want_gmail = transport in ("auto", "gmail")
    want_smtp = transport in ("auto", "smtp")
    want_file = transport in ("auto", "file")

    smtp_ready = want_smtp and bool(s.smtp_host and s.smtp_user and s.smtp_password)
    server = None
    if smtp_ready:
        try:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
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

        if want_gmail and not sent:
            err = _send_via_composio(owner, subject, html)
            if err is None:
                results[owner] = "sent (gmail/composio)"
                sent = True
            else:
                logger.warning("Gmail/Composio send to %s failed: %s", owner, err)

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
