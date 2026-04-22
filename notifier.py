"""
notifier.py — SMS alerts (Twilio) + email summaries.

SMS flow for trade confirmation:
  1. Text is sent: "ALERT: BUY TSLA $401.20 — reply STOP within 60s to cancel"
  2. System waits CANCEL_WINDOW_SECONDS (default: 60)
  3. During wait, polls Twilio inbox for a "STOP" reply from your number
  4. If STOP received → trade cancelled. Otherwise → trade executes.
"""

import asyncio
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Twilio is imported lazily so the system still runs if not installed
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    logger.warning("twilio package not installed — SMS alerts disabled")


class Notifier:
    """Sends trade alerts via SMS and daily summary via email."""

    def __init__(self):
        self._sms_enabled = (
            TWILIO_AVAILABLE
            and bool(config.TWILIO_ACCOUNT_SID)
            and bool(config.TWILIO_AUTH_TOKEN)
            and bool(config.TWILIO_FROM_NUMBER)
            and bool(config.TWILIO_TO_NUMBER)
        )
        self._email_enabled = (
            bool(config.EMAIL_FROM)
            and bool(config.EMAIL_APP_PASSWORD)
            and bool(config.EMAIL_TO)
        )
        self._twilio: Optional[TwilioClient] = None

        if self._sms_enabled:
            self._twilio = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
            logger.info("SMS alerts enabled via Twilio")
        else:
            logger.warning("SMS alerts disabled — check Twilio credentials in .env")

        if self._email_enabled:
            logger.info("Email summaries enabled")

    # ── SMS ────────────────────────────────────────────────────────

    async def alert_with_cancel_window(
        self,
        symbol: str,
        action: str,       # "BUY" or "SELL"
        price: float,
        reason: str = "",
        confidence: float = 0.0,
    ) -> bool:
        """
        Send a trade alert SMS and wait for a cancel reply.
        Returns True if the trade should proceed, False if user replied STOP.
        If SMS is not enabled, logs the alert and returns True immediately.
        """
        mode = "PAPER" if config.IS_PAPER_TRADING else "LIVE"
        conf_str = f" | Conf: {confidence*100:.0f}%" if confidence > 0 else ""
        msg = (
            f"[{mode}] {action} {symbol} @ ${price:.2f}{conf_str}\n"
            f"{reason[:80]}\n"
            f"Reply STOP within {config.CANCEL_WINDOW_SECONDS}s to cancel."
        )

        if not config.REQUIRE_SMS_CONFIRMATION:
            logger.info(f"ALERT (confirmation disabled): {action} {symbol} @ ${price:.2f}")
            return True

        if self._sms_enabled:
            # --- SMS path ---
            sent_at = datetime.now(timezone.utc)
            self._send_sms(msg)
            logger.info(f"Trade alert sent via SMS. Waiting {config.CANCEL_WINDOW_SECONDS}s for STOP reply...")
            deadline = time.time() + config.CANCEL_WINDOW_SECONDS
            while time.time() < deadline:
                await asyncio.sleep(5)
                if self._check_for_stop_reply(sent_at):
                    cancel_msg = f"CANCELLED: {action} {symbol} — STOP received"
                    logger.info(cancel_msg)
                    self._send_sms(f"✓ Trade CANCELLED: {action} {symbol} @ ${price:.2f}")
                    return False
            logger.info(f"No STOP received — proceeding with {action} {symbol}")
            return True

        elif self._email_enabled:
            # --- Email fallback path ---
            subject = f"⚡ TRADE ALERT: {action} {symbol} @ ${price:.2f}"
            body = (
                f"<html><body style='font-family:monospace;background:#0d1117;color:#c9d1d9;padding:20px'>"
                f"<h2 style='color:#f0883e'>⚡ Trade Alert — Action Required</h2>"
                f"<p style='font-size:18px'><b>{action} {symbol}</b> @ <b>${price:.2f}</b></p>"
                f"<p>{reason[:200]}</p>"
                f"<p style='color:#8b949e'>Confidence: {confidence*100:.0f}%</p>"
                f"<hr style='border-color:#30363d'/>"
                f"<p style='color:#f85149'>⚠️ This trade will execute automatically in "
                f"<b>{config.CANCEL_WINDOW_SECONDS} seconds</b>.</p>"
                f"<p>To cancel, stop the trading system (Ctrl+C in PowerShell) before the timer expires.</p>"
                f"<p style='color:#444;font-size:10px'>Automated system. Not financial advice.</p>"
                f"</body></html>"
            )
            self._send_email(subject, body)
            logger.info(f"Trade alert sent via EMAIL. Waiting {config.CANCEL_WINDOW_SECONDS}s...")
            await asyncio.sleep(config.CANCEL_WINDOW_SECONDS)
            logger.info(f"Timer expired — proceeding with {action} {symbol}")
            return True

        else:
            # No alert method available — log and proceed
            logger.warning(f"No alert method configured. Proceeding with {action} {symbol} @ ${price:.2f}")
            return True

    def send_info(self, message: str) -> None:
        """Send a plain informational alert via SMS or email."""
        if self._sms_enabled:
            self._send_sms(message)
        elif self._email_enabled:
            self._send_email("Ripster Trader — Info", f"<pre>{message}</pre>")
        logger.info(f"INFO: {message}")

    def _send_email(self, subject: str, html_body: str) -> None:
        """Send an HTML email via Gmail SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = config.EMAIL_FROM
            msg["To"]      = config.EMAIL_TO
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(config.EMAIL_FROM, config.EMAIL_APP_PASSWORD)
                server.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
            logger.debug(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    def _send_sms(self, body: str) -> None:
        try:
            self._twilio.messages.create(
                body=body[:1600],   # Twilio max SMS length
                from_=config.TWILIO_FROM_NUMBER,
                to=config.TWILIO_TO_NUMBER,
            )
            logger.debug(f"SMS sent: {body[:60]}...")
        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")

    def _check_for_stop_reply(self, since: datetime) -> bool:
        """Check Twilio inbox for a STOP reply from the user's number."""
        try:
            messages = self._twilio.messages.list(
                to=config.TWILIO_FROM_NUMBER,    # Messages sent TO our Twilio number
                date_sent_after=since,
                limit=5,
            )
            for msg in messages:
                # Only listen to replies from our registered mobile number
                if (msg.from_ == config.TWILIO_TO_NUMBER
                        and "STOP" in (msg.body or "").upper()):
                    return True
        except Exception as e:
            logger.error(f"Error checking Twilio inbox: {e}")
        return False

    # ── Email ───────────────────────────────────────────────────────

    def send_daily_summary(
        self,
        trades: list[dict],
        portfolio_value: float,
        daily_pnl_pct: Optional[float],
        watchlist: list[str],
        headlines: dict[str, list[str]],
    ) -> None:
        """Send an end-of-day HTML email summary."""
        if not self._email_enabled or not config.SEND_DAILY_EMAIL_SUMMARY:
            return

        subject = (
            f"Ripster Trader — Daily Summary "
            f"{datetime.now().strftime('%b %d, %Y')}"
        )
        html = self._build_summary_html(trades, portfolio_value, daily_pnl_pct, watchlist, headlines)

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = config.EMAIL_FROM
            msg["To"]      = config.EMAIL_TO
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(config.EMAIL_FROM, config.EMAIL_APP_PASSWORD)
                server.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
            logger.info("Daily summary email sent")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    def _build_summary_html(
        self,
        trades: list[dict],
        portfolio_value: float,
        daily_pnl_pct: Optional[float],
        watchlist: list[str],
        headlines: dict[str, list[str]],
    ) -> str:
        pnl_color = "#3fb950" if (daily_pnl_pct or 0) >= 0 else "#f85149"
        pnl_str   = f"{daily_pnl_pct*100:+.2f}%" if daily_pnl_pct is not None else "n/a"

        trade_rows = ""
        for t in trades:
            pnl = t.get("pnl", 0)
            clr = "#3fb950" if pnl >= 0 else "#f85149"
            trade_rows += (
                f"<tr>"
                f"<td>{t.get('symbol','')}</td>"
                f"<td>{t.get('action','')}</td>"
                f"<td>${t.get('entry_price',0):.2f}</td>"
                f"<td>${t.get('exit_price',0):.2f}</td>"
                f"<td style='color:{clr}'>${pnl:+.2f}</td>"
                f"<td>{t.get('signal_reason','')[:50]}</td>"
                f"</tr>"
            )

        headline_html = ""
        for sym, hl_list in headlines.items():
            items = "".join(f"<li>{h}</li>" for h in hl_list[:3])
            headline_html += f"<h4>{sym} Headlines</h4><ul>{items}</ul>"

        return f"""
        <html><body style="font-family:monospace;background:#0d1117;color:#c9d1d9;padding:20px">
        <h2 style="color:#58a6ff">Ripster Trader — Daily Summary</h2>
        <p>{datetime.now().strftime('%A, %B %d, %Y')}</p>
        <table style="border-collapse:collapse;margin-bottom:16px">
          <tr><td style="padding:4px 12px;color:#8b949e">Portfolio Value</td>
              <td style="padding:4px 12px;color:#fff;font-weight:bold">${portfolio_value:,.2f}</td></tr>
          <tr><td style="padding:4px 12px;color:#8b949e">Daily P&amp;L</td>
              <td style="padding:4px 12px;color:{pnl_color};font-weight:bold">{pnl_str}</td></tr>
          <tr><td style="padding:4px 12px;color:#8b949e">Trades Executed</td>
              <td style="padding:4px 12px">{len(trades)}</td></tr>
          <tr><td style="padding:4px 12px;color:#8b949e">Watchlist</td>
              <td style="padding:4px 12px">{', '.join(watchlist)}</td></tr>
        </table>
        <h3 style="color:#58a6ff">Today's Trades</h3>
        <table style="border-collapse:collapse;width:100%;font-size:12px">
          <tr style="background:#161b22;color:#8b949e">
            <th style="padding:6px">Symbol</th><th>Action</th><th>Entry</th>
            <th>Exit</th><th>P&amp;L</th><th>Reason</th>
          </tr>
          {trade_rows if trade_rows else '<tr><td colspan="6" style="padding:8px;color:#444">No trades today</td></tr>'}
        </table>
        <h3 style="color:#58a6ff;margin-top:20px">Today's News</h3>
        {headline_html}
        <p style="color:#444;font-size:10px;margin-top:20px">
          ⚠️ Automated system. Review all trades. Not financial advice.
        </p>
        </body></html>
        """
