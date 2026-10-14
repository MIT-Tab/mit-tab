import pytest
from botocore.exceptions import ClientError

from mittab.libs.email_service import EmailRequest, EmailService, EmailServiceError


class DummySESClient:
    def __init__(
        self,
        should_fail=False,
        fail_create=False,
        existing_lists=None,
        fail_on_calls=None,
    ):
        self.should_fail = should_fail
        self.fail_create = fail_create
        self.fail_on_calls = set(fail_on_calls or [])
        self.calls = []
        self.send_attempts = 0
        self.created_lists = []
        self.contact_lists = set(existing_lists or [])

    def send_email(self, **kwargs):
        self.send_attempts += 1
        call_number = self.send_attempts
        if self.should_fail or call_number in self.fail_on_calls:
            raise ClientError(
                {"Error": {"Code": "Boom", "Message": "boom"}},
                "SendEmail",
            )
        self.calls.append(kwargs)

    def create_contact_list(self, **kwargs):
        if self.fail_create:
            raise ClientError(
                {"Error": {"Code": "RandomError", "Message": "boom"}},
                "CreateContactList",
            )
        self.created_lists.append(kwargs.get("ContactListName"))
        self.contact_lists.add(kwargs.get("ContactListName"))

    def get_contact_list(self, **kwargs):
        name = kwargs.get("ContactListName")
        if name not in self.contact_lists:
            raise ClientError(
                {"Error": {"Code": "NotFoundException", "Message": "missing"}},
                "GetContactList",
            )


def test_send_bulk_injects_unsubscribe_links(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = "test-contact-list"
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient()
    service = EmailService(ses_client=client)
    request = EmailRequest(
        to_address="judge@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    sent = service.send_bulk([request])

    assert sent == 1
    assert client.created_lists == ["test-contact-list"]
    assert client.calls
    payload = client.calls[0]
    assert payload["ListManagementOptions"] == {
        "ContactListName": "test-contact-list",
    }


def test_send_bulk_raises_on_failure(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = "test-contact-list"
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient(should_fail=True)
    service = EmailService(ses_client=client)
    request = EmailRequest(
        to_address="judge@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    with pytest.raises(EmailServiceError):
        service.send_bulk([request])


def test_send_bulk_reports_partial_successes(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = "test-contact-list"
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient(fail_on_calls={2})
    service = EmailService(ses_client=client)
    first_request = EmailRequest(
        to_address="judge1@example.com",
        subject="Judge Code",
        text_body="Test",
    )
    second_request = EmailRequest(
        to_address="judge2@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    with pytest.raises(EmailServiceError) as exc_info:
        service.send_bulk([first_request, second_request])

    assert exc_info.value.sent_requests == [first_request]


def test_send_bulk_without_contact_list(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = ""
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient()
    service = EmailService(ses_client=client)
    request = EmailRequest(
        to_address="judge@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    sent = service.send_bulk([request])

    assert sent == 1
    assert client.calls
    assert "ListManagementOptions" not in client.calls[0]


def test_send_bulk_existing_contact_list(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = "test-contact-list"
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient(existing_lists={"test-contact-list"})
    service = EmailService(ses_client=client)
    request = EmailRequest(
        to_address="judge@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    sent = service.send_bulk([request])

    assert sent == 1
    assert not client.created_lists


def test_send_bulk_contact_list_creation_failure(settings):
    settings.AWS_MAILMANAGER_ADDRESS_LIST = "test-contact-list"
    settings.DEFAULT_FROM_EMAIL = "MIT-TAB <no-reply@example.com>"
    settings.EMAIL_REPLY_TO = "no-reply@example.com"

    client = DummySESClient(fail_create=True)
    service = EmailService(ses_client=client)
    request = EmailRequest(
        to_address="judge@example.com",
        subject="Judge Code",
        text_body="Test",
    )

    with pytest.raises(EmailServiceError):
        service.send_bulk([request])
