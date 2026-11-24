"""Building connectors for an organization from its stored integrations."""

import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import SecretBox
from app.modules.integrations.connectors import CONNECTORS, CallRecord, HttpConnector
from app.modules.integrations.models import Integration, IntegrationCall


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# What each kind of integration needs. Secrets are stored encrypted; config isn't secret.
class ServiceNowSecrets(_Strict):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class SalesforceSecrets(_Strict):
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(min_length=1, max_length=500)


class EmailSecrets(_Strict):
    api_key: str = Field(min_length=1, max_length=500)


class EmptyConfig(_Strict):
    pass


class EmailConfig(_Strict):
    sender: EmailStr


SCHEMAS: dict[str, tuple[type[_Strict], type[_Strict]]] = {
    "servicenow": (EmptyConfig, ServiceNowSecrets),
    "salesforce": (EmptyConfig, SalesforceSecrets),
    "email": (EmailConfig, EmailSecrets),
}

KIND_LABELS = {"servicenow": "ServiceNow", "salesforce": "Salesforce", "email": "Email"}


class IntegrationRuntime:
    """App-wide: knows the key, the settings and (in tests) a fake network."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.transport = transport
        self.box = SecretBox(settings.encryption_key.get_secret_value())

    async def record(self, call: CallRecord) -> None:
        async with self.session_factory() as session:
            session.add(IntegrationCall(**call.__dict__))
            await session.commit()

    def connector(self, integration: Integration) -> HttpConnector:
        return CONNECTORS[integration.kind](
            organization_id=integration.organization_id,
            integration_id=integration.id,
            base_url=integration.base_url,
            config=integration.config,
            secrets=self.box.decrypt(integration.secrets_encrypted),
            settings=self.settings,
            transport=self.transport,
            recorder=self.record,
        )

    def for_org(self, session: AsyncSession, organization_id: uuid.UUID) -> "IntegrationHub":
        return IntegrationHub(self, session, organization_id)

    def webhook_url(self, integration_id: uuid.UUID) -> str:
        base = self.settings.public_api_url.rstrip("/")
        return f"{base}{self.settings.api_prefix}/webhooks/servicenow/{integration_id}"


class IntegrationHub:
    """Per request: the active connector of each kind for one organization."""

    def __init__(
        self, runtime: IntegrationRuntime, session: AsyncSession, organization_id: uuid.UUID
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.organization_id = organization_id
        self._cache: dict[str, HttpConnector | None] = {}

    async def get(self, kind: str) -> Any:
        if kind not in self._cache:
            integration = await self.session.scalar(
                select(Integration).where(
                    Integration.organization_id == self.organization_id,
                    Integration.kind == kind,
                    Integration.status == "active",
                )
            )
            self._cache[kind] = self.runtime.connector(integration) if integration else None
        return self._cache[kind]
