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


class TotpRequest(_CamelModel):
    """The provider login's second step (U10): the pre-auth token from the
    password step, plus the authenticator code once enrolled."""

    token: str
    code: str | None = None


class ForgotPasswordRequest(_CamelModel):
    email: str


class ResetPasswordRequest(_CamelModel):
    token: str
    password: str = Field(min_length=6, max_length=72)


class VerifyEmailRequest(_CamelModel):
    """The token from the verification link (plan U8). Session-bound: the
    route also requires the signed-in owner of the token."""

    token: str = Field(min_length=1, max_length=256)


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
    email_verified: bool = Field(default=True, alias="emailVerified")

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
    # When the CALLING session last verified a code (U10): the step-up dialog
    # shows the code input up front once this is stale (>15 minutes) or null.
    totp_verified_at: str | None = Field(default=None, alias="totpVerifiedAt")


class SupportSessionOut(_CamelModel):
    """The calling session's live step-in (U10/AE29): feeds the persistent
    support banner (school, reason, started time)."""

    id: str
    school_id: str = Field(alias="schoolId")
    school_name: str | None = Field(default=None, alias="schoolName")
    school_code: str | None = Field(default=None, alias="schoolCode")
    reason: str | None = None
    started_at: str | None = Field(default=None, alias="startedAt")


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
    support_session: SupportSessionOut | None = Field(
        default=None, alias="supportSession"
    )
    must_change_password: bool = Field(default=False, alias="mustChangePassword")
    # Release 1b (D1): false while a new sign-up (or an account whose email
    # the office changed) waits for its verification click. Grandfathered
    # accounts, made before 1b, always read true.
    email_verified: bool = Field(default=True, alias="emailVerified")
    # Hardening U9: the calling session was created by PIN. The account menus
    # hide Change password for it (the session never held the password).
    signed_in_with_pin: bool = Field(default=False, alias="signedInWithPin")
