import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.modules.auth.roles import Role

PASSWORD_MIN = 12
PASSWORD_MAX = 128


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class _PasswordMixin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return _normalise_email(value)

    @model_validator(mode="after")
    def password_not_email(self):
        local_part = self.email.split("@")[0]
        lowered = self.password.lower()
        # Only meaningful for real names: a 1-3 letter local part would match almost anything.
        if lowered == self.email or (len(local_part) >= 4 and local_part in lowered):
            raise ValueError("Password must not contain your email address")
        return self


class RegisterRequest(_PasswordMixin):
    organization_name: str = Field(min_length=2, max_length=120)
    full_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    # No length rules here: a login must never reveal password policy details.
    password: str = Field(max_length=PASSWORD_MAX)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return _normalise_email(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    email: str
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut | None


class UserCreate(_PasswordMixin):
    full_name: str = Field(min_length=1, max_length=120)
    role: Role = Role.EMPLOYEE

    @field_validator("role")
    @classmethod
    def org_role_only(cls, value: Role) -> Role:
        if value == Role.SUPER_ADMIN:
            raise ValueError("super_admin cannot be assigned through the API")
        return value


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def org_role_only(cls, value: Role | None) -> Role | None:
        if value == Role.SUPER_ADMIN:
            raise ValueError("super_admin cannot be assigned through the API")
        return value
