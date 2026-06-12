from pydantic import BaseModel, ConfigDict, Field


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class SignupRequest(_CamelModel):
    email: str
    password: str = Field(min_length=6, max_length=72)
    full_name: str = Field(alias="fullName", min_length=1)
    role: str  # validated in the service to driver|parent only


class LoginRequest(_CamelModel):
    email: str
    password: str


class PinLoginRequest(_CamelModel):
    pin: str


class ForgotPasswordRequest(_CamelModel):
    email: str


class ResetPasswordRequest(_CamelModel):
    token: str
    password: str = Field(min_length=6, max_length=72)


class AuthUser(_CamelModel):
    id: str
    email: str
    full_name: str | None = Field(default=None, alias="fullName")
    role: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class SessionResponse(_CamelModel):
    token: str
    user: AuthUser
