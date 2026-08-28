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


class ChangePasswordRequest(_CamelModel):
    current_password: str = Field(alias="currentPassword")
    # Same 6–72 rule as every other password field (signup/reset).
    new_password: str = Field(alias="newPassword", min_length=6, max_length=72)


class AuthUser(_CamelModel):
    id: str
    email: str
    full_name: str | None = Field(default=None, alias="fullName")
    role: str | None = None
    must_change_password: bool = Field(default=False, alias="mustChangePassword")

    model_config = ConfigDict(populate_by_name=True)


class SessionResponse(_CamelModel):
    token: str
    user: AuthUser


class MembershipOut(_CamelModel):
    """One school membership row on /me (active) or a pending offer."""

    school_id: str = Field(alias="schoolId")
    school_name: str | None = Field(default=None, alias="schoolName")
    school_code: str | None = Field(default=None, alias="schoolCode")
    role: str  # director | coordinator | driver


class OfferOut(MembershipOut):
    """A pending offer on /me (U8): the membership row id plus who offered
    the role and when — enough for the offers screen to render its card."""

    id: str | None = None
    offered_by: str | None = Field(default=None, alias="offeredBy")
    offered_at: str | None = Field(default=None, alias="offeredAt")


class ProviderStateOut(_CamelModel):
    is_provider: bool = Field(default=True, alias="isProvider")
    totp_enrolled: bool = Field(default=False, alias="totpEnrolled")


class MeResponse(_CamelModel):
    """The /api/auth/me contract (U5).

    ``role`` stays the legacy per-user role ('admin' for pre-tenancy staff,
    'driver', 'parent') through the compatibility window; clients built for
    tenancy read ``memberships`` and send X-School-Id instead.
    """

    id: str
    email: str
    full_name: str | None = Field(default=None, alias="fullName")
    role: str | None = None
    memberships: list[MembershipOut] = []
    pending_offers: list[OfferOut] = Field(default=[], alias="pendingOffers")
    active_school_id: str | None = Field(default=None, alias="activeSchoolId")
    provider: ProviderStateOut | None = None
    must_change_password: bool = Field(default=False, alias="mustChangePassword")
