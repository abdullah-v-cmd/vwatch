"""
V-Watch Backend - Notification Service
SMS and Email notifications for violations
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Dict
from datetime import datetime

from ..core.config import settings

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Send email notifications via SMTP."""

    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.enabled = bool(self.user and self.password)

    def send(self, to_email: str, subject: str, body_html: str) -> bool:
        if not self.enabled:
            logger.info(f"[Email MOCK] To: {to_email} | Subject: {subject}")
            return True

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.user
            msg["To"] = to_email
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, to_email, msg.as_string())

            logger.info(f"[Email] Sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"[Email] Failed to send to {to_email}: {e}")
            return False


class SMSNotifier:
    """Send SMS notifications via configurable providers."""

    def __init__(self):
        self.provider = settings.SMS_PROVIDER
        self.api_key = settings.SMS_API_KEY
        self.enabled = self.provider != "mock" and bool(self.api_key)

    def send(self, phone: str, message: str) -> bool:
        if not self.enabled:
            logger.info(f"[SMS MOCK] To: {phone} | Msg: {message}")
            return True

        if self.provider == "twilio":
            return self._send_twilio(phone, message)
        logger.warning(f"[SMS] Unknown provider: {self.provider}")
        return False

    def _send_twilio(self, phone: str, message: str) -> bool:
        try:
            from twilio.rest import Client
            account_sid = settings.SMS_API_KEY.split(":")[0]
            auth_token = settings.SMS_API_KEY.split(":")[1]
            client = Client(account_sid, auth_token)
            client.messages.create(body=message, from_="+1234567890", to=phone)
            return True
        except Exception as e:
            logger.error(f"[SMS] Twilio error: {e}")
            return False


class NotificationService:
    """Unified notification orchestrator."""

    VIOLATION_FINES = {
        "SPEEDING": settings.DEFAULT_SPEEDING_FINE,
        "RED_LIGHT": settings.DEFAULT_REDLIGHT_FINE,
        "WRONG_DIRECTION": settings.DEFAULT_WRONGDIR_FINE,
        "LANE_VIOLATION": settings.DEFAULT_LANE_FINE,
    }

    def __init__(self):
        self.email = EmailNotifier()
        self.sms = SMSNotifier()

    async def notify_violation_approved(
        self, violation: Dict, owner_email: Optional[str], owner_phone: Optional[str]
    ):
        """Send violation approved notification to vehicle owner."""
        fine = self.VIOLATION_FINES.get(violation.get("violation_type", ""), 200.0)

        if owner_email:
            subject = f"Traffic Violation Notice - {violation.get('evidence_id', '')[:8]}"
            body = self._violation_email_template(violation, fine)
            self.email.send(owner_email, subject, body)

        if owner_phone:
            msg = (
                f"V-Watch Alert: Traffic violation detected on your vehicle "
                f"({violation.get('plate_number', 'N/A')}) - "
                f"{violation.get('violation_type', 'Unknown')}. "
                f"Fine: ${fine:.2f}. Check your email for details."
            )
            self.sms.send(owner_phone, msg)

    async def notify_police_new_violation(
        self, violation: Dict, officer_email: Optional[str]
    ):
        """Notify traffic police about new pending violation."""
        if officer_email:
            subject = f"New Violation Pending Review - {violation.get('violation_type', '')}"
            body = f"""
            <html><body>
            <h2>New Traffic Violation - Pending Review</h2>
            <table border='1' cellpadding='5'>
              <tr><td><b>Evidence ID</b></td><td>{violation.get('evidence_id', '')[:8]}</td></tr>
              <tr><td><b>Plate</b></td><td>{violation.get('plate_number', 'UNKNOWN')}</td></tr>
              <tr><td><b>Violation</b></td><td>{violation.get('violation_type', '')}</td></tr>
              <tr><td><b>Location</b></td><td>{violation.get('location', '')}</td></tr>
              <tr><td><b>Time</b></td><td>{violation.get('violation_time', '')}</td></tr>
            </table>
            <p>Please log in to V-Watch to review this violation.</p>
            </body></html>
            """
            self.email.send(officer_email, subject, body)

    def _violation_email_template(self, violation: Dict, fine: float) -> str:
        return f"""
        <html>
        <head>
          <style>
            body {{ font-family: Arial, sans-serif; color: #333; }}
            .header {{ background: #c0392b; color: white; padding: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            .fine {{ font-size: 24px; color: #c0392b; font-weight: bold; }}
          </style>
        </head>
        <body>
          <div class='header'>
            <h1>V-Watch Traffic Violation Notice</h1>
          </div>
          <p>Dear Vehicle Owner,</p>
          <p>A traffic violation has been recorded for your vehicle.</p>
          <table>
            <tr><th>Evidence ID</th><td>{violation.get('evidence_id', '')[:8]}</td></tr>
            <tr><th>Plate Number</th><td>{violation.get('plate_number', 'UNKNOWN')}</td></tr>
            <tr><th>Violation Type</th><td>{violation.get('violation_type', '')}</td></tr>
            <tr><th>Location</th><td>{violation.get('location', '')}</td></tr>
            <tr><th>Date/Time</th><td>{violation.get('violation_time', '')}</td></tr>
            <tr><th>Camera</th><td>{violation.get('camera_id', '')}</td></tr>
          </table>
          <p class='fine'>Fine Amount: ${fine:.2f}</p>
          <p>Please pay within 30 days to avoid additional penalties.</p>
          <p>Contact traffic@vwatch.gov to appeal this violation.</p>
        </body>
        </html>
        """
