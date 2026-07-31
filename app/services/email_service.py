"""
Email Service — AWS SES with SMTP fallback.
All email sending goes through this single interface.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


class EmailService:
    """
    Priority: AWS SES → SMTP → Log-only (dev mode)
    """

    def __init__(self):
        from app.core.config import settings
        self.settings = settings
        self._ses_client = None
        self._mode = 'log'  # 'ses' | 'smtp' | 'log'
        self._setup()

    def _setup(self):
        s = self.settings
        # Try SES first
        if (
            getattr(s, 'aws_access_key_id', None)
            and s.aws_access_key_id not in ('change-me', 'replace-me', '')
            and getattr(s, 'aws_ses_region', None)
        ):
            try:
                import boto3
                self._ses_client = boto3.client(
                    'ses',
                    region_name=s.aws_ses_region,
                    aws_access_key_id=s.aws_access_key_id,
                    aws_secret_access_key=s.aws_secret_access_key,
                )
                self._mode = 'ses'
                logger.info('EmailService: using AWS SES')
                return
            except Exception as e:
                logger.warning(f'SES init failed: {e}')

        # Try SMTP
        smtp_host = getattr(s, 'smtp_host', None)
        if smtp_host and smtp_host not in ('', 'change-me'):
            self._mode = 'smtp'
            logger.info(f'EmailService: using SMTP ({smtp_host})')
            return

        logger.warning('EmailService: no email provider configured — running in log-only mode')
        self._mode = 'log'

    def send(
        self,
        to: str | list[str],
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        from_addr: Optional[str] = None,
    ) -> bool:
        recipients = [to] if isinstance(to, str) else to
        sender = from_addr or getattr(self.settings, 'email_from', 'noreply@pulselms.com')
        text_fallback = text_body or self._strip_html(html_body)

        if self._mode == 'ses':
            return self._send_ses(sender, recipients, subject, html_body, text_fallback)
        elif self._mode == 'smtp':
            return self._send_smtp(sender, recipients, subject, html_body, text_fallback)
        else:
            logger.info(f'[EMAIL LOG-ONLY] To: {recipients} | Subject: {subject}')
            logger.info(f'[EMAIL BODY] {text_fallback[:200]}')
            return True  # Treat as success in dev mode

    def _send_ses(self, sender, recipients, subject, html_body, text_body) -> bool:
        try:
            self._ses_client.send_email(
                Source=sender,
                Destination={'ToAddresses': recipients},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {
                        'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                        'Html': {'Data': html_body, 'Charset': 'UTF-8'},
                    },
                },
            )
            return True
        except Exception as e:
            logger.error(f'SES send failed: {e}')
            return False

    def _send_smtp(self, sender, recipients, subject, html_body, text_body) -> bool:
        s = self.settings
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = sender
            msg['To'] = ', '.join(recipients)
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            port = int(getattr(s, 'smtp_port', 587))
            with smtplib.SMTP(s.smtp_host, port, timeout=10) as server:
                server.ehlo()
                if port != 465:
                    server.starttls()
                if getattr(s, 'smtp_user', None):
                    server.login(s.smtp_user, s.smtp_password)
                server.sendmail(sender, recipients, msg.as_string())
            return True
        except Exception as e:
            logger.error(f'SMTP send failed: {e}')
            return False

    @staticmethod
    def _strip_html(html: str) -> str:
        import re
        return re.sub(r'<[^>]+>', '', html)

    # ─── Template helpers ──────────────────────────────────────────────────────

    def send_otp(self, to: str, otp: str, user_name: str = '') -> bool:
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:16px">
          <h2 style="color:#22d3ee;margin-bottom:8px">Pulse LMS — Password Reset</h2>
          <p>Hello {user_name or 'there'},</p>
          <p>Your one-time password (OTP) is:</p>
          <div style="font-size:36px;font-weight:900;letter-spacing:12px;color:#22d3ee;background:#1e293b;padding:20px;border-radius:12px;text-align:center;margin:20px 0">
            {otp}
          </div>
          <p style="color:#94a3b8;font-size:13px">This OTP expires in <strong>15 minutes</strong>. Do not share it with anyone.</p>
          <hr style="border-color:#1e293b;margin:24px 0">
          <p style="color:#475569;font-size:12px">If you did not request a password reset, ignore this email.</p>
        </div>"""
        return self.send(to, 'Pulse LMS — Your Password Reset OTP', html)

    def send_training_assigned(self, to: str, user_name: str, training_type: str, doc_title: str, due_date: str = '') -> bool:
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:16px">
          <h2 style="color:#22d3ee">New Training Assigned</h2>
          <p>Hello {user_name},</p>
          <p>You have been assigned a new training on <strong>Pulse LMS</strong>:</p>
          <table style="width:100%;background:#1e293b;border-radius:12px;padding:16px;margin:16px 0">
            <tr><td style="color:#94a3b8;padding:6px 0">Type</td><td style="color:#f1f5f9;font-weight:bold">{training_type.upper()}</td></tr>
            <tr><td style="color:#94a3b8;padding:6px 0">Document</td><td style="color:#f1f5f9">{doc_title}</td></tr>
            {'<tr><td style="color:#94a3b8;padding:6px 0">Due By</td><td style="color:#f59e0b;font-weight:bold">' + due_date + '</td></tr>' if due_date else ''}
          </table>
          <p>Log in to Pulse LMS to begin your training.</p>
        </div>"""
        return self.send(to, f'Pulse LMS — New {training_type.upper()} Training Assigned', html)

    def send_nq_alert(self, to: str, user_name: str, topic: str, retraining_deadline: str) -> bool:
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:16px">
          <h2 style="color:#f59e0b">⚠ Qualification Alert — Retraining Required</h2>
          <p>Hello {user_name},</p>
          <p>You have been marked <strong style="color:#f87171">Not Qualified (NQ)</strong> for:</p>
          <div style="background:#1e293b;border-left:4px solid #f59e0b;padding:16px;border-radius:8px;margin:16px 0">
            <p style="margin:0;font-weight:bold">{topic}</p>
          </div>
          <p>You must complete retraining by <strong style="color:#f59e0b">{retraining_deadline}</strong> (within 30 days).</p>
          <p>Please log in to Pulse LMS to begin your retraining.</p>
        </div>"""
        return self.send(to, 'Pulse LMS — Retraining Required (NQ Status)', html)

    def send_overdue_reminder(self, to: str, user_name: str, overdue_items: list[dict]) -> bool:
        rows = ''.join(
            f'<tr><td style="padding:8px;border-bottom:1px solid #1e293b">{i["training_type"].upper()}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #1e293b">{i.get("document_code","—")}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #1e293b;color:#f87171">{i.get("days_overdue",0)}d overdue</td></tr>'
            for i in overdue_items
        )
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:540px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:16px">
          <h2 style="color:#f87171">⏰ Overdue Training Reminder</h2>
          <p>Hello {user_name}, the following training assignments are overdue:</p>
          <table style="width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden;margin:16px 0">
            <thead><tr style="background:#0f172a">
              <th style="padding:10px;text-align:left;color:#94a3b8">Type</th>
              <th style="padding:10px;text-align:left;color:#94a3b8">Document</th>
              <th style="padding:10px;text-align:left;color:#94a3b8">Status</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>
          <p>Please log in to Pulse LMS immediately to complete your training.</p>
        </div>"""
        return self.send(to, 'Pulse LMS — Overdue Training Reminder', html)


# Singleton
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
