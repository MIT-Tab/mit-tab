import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class EmailServiceError(Exception):
    """Raised when Amazon SES rejects a send request."""


@dataclass(frozen=True)
class EmailRequest:
    """Simple representation of an outbound email."""

    to_address: str
    subject: str
    text_body: str
    html_body: Optional[str] = None
    reply_to: Optional[Sequence[str]] = None
    from_address: Optional[str] = None


class EmailService:
    """Wrapper around Amazon SESv2 for sending transactional emails."""

    def __init__(self, ses_client=None):
        self.client = ses_client or self._build_client()
        self._ensured_contact_lists: Set[str] = set()

    def send_bulk(self, requests: Iterable[EmailRequest]) -> int:
        """Send each payload as its own SES email."""

        requests = list(requests)
        if not requests:
            return 0

        sent = 0
        failures: List[str] = []
        list_management = self._list_management_options()
        configuration_set = settings.AWS_SES_CONFIGURATION_SET

        for request in requests:
            if not request.to_address:
                logger.debug("Skipping email with empty recipient")
                continue

            payload = self._build_send_kwargs(
                request,
                list_management=list_management,
                configuration_set=configuration_set,
            )

            try:
                self.client.send_email(**payload)
                sent += 1
            except (BotoCoreError, ClientError) as exc:
                logger.exception("Amazon SES send failed: %s", exc)
                failures.append(str(exc))

        if failures:
            raise EmailServiceError(
                f"Failed to send {len(failures)} email(s): {failures[-1]}"
            )

        return sent

    def _build_client(self):
        if not settings.AWS_SES_REGION:
            raise ImproperlyConfigured("AWS_SES_REGION must be configured")

        client_kwargs = {"region_name": settings.AWS_SES_REGION}

        if settings.AWS_SES_ACCESS_KEY_ID and settings.AWS_SES_SECRET_ACCESS_KEY:
            client_kwargs.update(
                aws_access_key_id=settings.AWS_SES_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SES_SECRET_ACCESS_KEY,
            )

        return boto3.client("sesv2", **client_kwargs)

    def _list_management_options(self):
        contact_list = settings.AWS_MAILMANAGER_ADDRESS_LIST
        if not contact_list:
            logger.warning(
                "AWS_MAILMANAGER_ADDRESS_LIST not configured; unsubscribe links will be omitted"
            )
            return None

        self._ensure_contact_list(contact_list)
        return {"ContactListName": contact_list}

    def _ensure_contact_list(self, contact_list):
        if contact_list in self._ensured_contact_lists:
            return

        try:
            self.client.get_contact_list(ContactListName=contact_list)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "NotFoundException":
                raise EmailServiceError(
                    f"Unable to ensure contact list '{contact_list}': {exc}"
                ) from exc
            try:
                self.client.create_contact_list(ContactListName=contact_list)
            except ClientError as inner_exc:
                error_code = inner_exc.response.get("Error", {}).get("Code")
                if error_code != "AlreadyExistsException":
                    raise EmailServiceError(
                        f"Unable to ensure contact list '{contact_list}': {inner_exc}"
                    ) from inner_exc
        except BotoCoreError as exc:
            raise EmailServiceError(
                f"Unable to ensure contact list '{contact_list}': {exc}"
            ) from exc

        self._ensured_contact_lists.add(contact_list)

    def _build_send_kwargs(self, request, list_management=None, configuration_set=None):
        body = {
            "Text": {
                "Data": request.text_body,
                "Charset": "UTF-8",
            },
        }
        if request.html_body:
            body["Html"] = {
                "Data": request.html_body,
                "Charset": "UTF-8",
            }

        destination = {"ToAddresses": [request.to_address]}
        reply_to = list(request.reply_to) if request.reply_to else []

        if not reply_to and settings.EMAIL_REPLY_TO:
            reply_to = [settings.EMAIL_REPLY_TO]

        kwargs = {
            "FromEmailAddress": request.from_address or settings.DEFAULT_FROM_EMAIL,
            "Destination": destination,
            "ReplyToAddresses": reply_to,
            "Content": {
                "Simple": {
                    "Subject": {
                        "Data": request.subject,
                        "Charset": "UTF-8",
                    },
                    "Body": body,
                },
            },
        }

        if list_management:
            kwargs["ListManagementOptions"] = list_management

        if configuration_set:
            kwargs["ConfigurationSetName"] = configuration_set

        return kwargs
