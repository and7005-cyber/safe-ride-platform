"""Provider console + two-step provider login (U10: R19–R22, R24, R25, R27).

The password step never issues a provider session: it mints a five-minute,
single-use pre-auth token, and ``POST /api/auth/totp`` turns that token into
a session only after the second factor (or, for a never-enrolled account,
issues a session locked to the enrolment allowlist — see
``app.core.auth.get_current_user``). Console writes that matter — step-in,
account lifecycle, TOTP reset — additionally require a code verified within
the last fifteen minutes on the CALLING session ("step-up"), else a fresh
one in the request.

Failures a client must tell apart carry a machine ``code`` via
:class:`AuthCodeError`; everything else stays a plain-message SafeRideError.
Secrets discipline: temporary passwords and TOTP secrets appear exactly once
in a response and are never logged or audited.
"""

import datetime as dt
import logging
import re
import secrets
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    SafeRideError,
    UnauthorizedError,
)
from app.core.scope import SchoolScope
from app.core.security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.core.totp import (
    current_step,
    derive_secret,
    pepper_key,
    provisioning_uri,
    secret_b32,
    verify_code,
)
from app.core.validation import clean_email
from app.dao.auth_dao import AuthDao
from app.dao.membership_dao import TEMPORARY_PASSWORD_TTL_HOURS
from app.dao.provider_dao import ProviderDao
from app.services.staff_service import StaffService, generate_temporary_password

logger = logging.getLogger("saferide.provider")

PREAUTH_TTL_MINUTES = 5
MAX_PREAUTH_ATTEMPTS = 5
STEP_UP_FRESH_MINUTES = 15

_PREAUTH_INVALID = "Sign-in token is invalid or has expired. Sign in again."
_INVALID_CODE = "Invalid code"
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AuthCodeError(SafeRideError):
    """A refusal the client must branch on: carries a machine ``code``.

    ``preauth-voided`` (401) — the pre-auth token died on its fifth wrong
    code; the next attempt starts over at the password.
    ``totp-step-up-required`` (401) — the session's last code is stale; the
    request must carry a fresh one.
    """

    status_code = 401

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _uuid_or_not_found(value: str, message: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError) as error:
        raise NotFoundError(message) from error


class ProviderService:
    def __init__(
        self,
        dao: ProviderDao | None = None,
        auth_dao: AuthDao | None = None,
        staff_service: StaffService | None = None,
    ) -> None:
        self.dao = dao or ProviderDao()
        self.auth_dao = auth_dao or AuthDao()
        self.staff = staff_service or StaffService()
        self.pepper = get_settings().totp_pepper

    # --- login: password step ------------------------------------------------

    def begin_provider_login(self, email: str, password: str) -> dict[str, Any] | None:
        """The provider branch of POST /api/auth/login (AE19).

        ``None`` means "not an active provider" — the ordinary login flow
        decides. A provider identity NEVER gets a session here: the same
        password/disabled/expiry checks as the standard login, then a
        pre-auth token and the flag telling the client which second step
        (code, or first-time enrolment) comes next.
        """
        user = self.dao.get_provider_login_user(email)
        if user is None:
            return None
        if not verify_password(password, user["password_hash"]):
            raise UnauthorizedError("Invalid email or password")
        if user.get("disabled_at") is not None:
            raise UnauthorizedError("Invalid email or password")
        expires_at = user.get("temporary_password_expires_at")
        if (
            user.get("must_change_password")
            and expires_at is not None
            and expires_at <= dt.datetime.now(dt.timezone.utc)
        ):
            # An unused temporary password dies after 72 hours (R6): the same
            # generic refusal as the standard login.
            raise UnauthorizedError("Invalid email or password")
        token = create_session_token()
        self.auth_dao.create_preauth_token(
            user["id"], hash_session_token(token), PREAUTH_TTL_MINUTES
        )
        if user["totp_enrolled_at"] is None:
            return {"preauth": token, "totpEnrolmentRequired": True}
        return {"preauth": token, "totpRequired": True}

    # --- login: code step ----------------------------------------------------

    def complete_totp(self, token: str, code: str | None) -> dict[str, Any]:
        """POST /api/auth/totp: pre-auth token (+ code when enrolled) → session.

        Disabled/removed/enrolment state is re-checked NOW, not as of the
        password step. Each wrong code burns an attempt; the fifth voids the
        token and answers the distinct ``preauth-voided`` code (AE19).
        """
        record = self.auth_dao.get_preauth_token(hash_session_token(token or ""))
        if record is None:
            raise UnauthorizedError(_PREAUTH_INVALID)
        if record["used_at"] is not None:
            if record["attempts"] >= MAX_PREAUTH_ATTEMPTS:
                raise AuthCodeError(
                    "preauth-voided", "Too many wrong codes. Sign in again."
                )
            raise UnauthorizedError(_PREAUTH_INVALID)
        if record["expired"]:
            raise UnauthorizedError(_PREAUTH_INVALID)

        state = self.dao.get_provider_verify_state(record["user_id"])
        if (
            state is None
            or state.get("disabled_at") is not None
            or not state.get("provider_active")
        ):
            # Dead identity: burn the token too, so nothing lingers.
            self.auth_dao.consume_preauth_token(record["id"])
            raise UnauthorizedError(_PREAUTH_INVALID)

        if state["totp_enrolled_at"] is None:
            # First sign-in: accepted WITHOUT a code; the issued session is
            # restricted to the enrolment allowlist until confirm succeeds.
            if not self.auth_dao.consume_preauth_token(record["id"]):
                raise UnauthorizedError(_PREAUTH_INVALID)
            return self._issue_provider_session(state, totp_verified=False)

        self._warn_on_pepper_rotation(state)
        secret = derive_secret(self.pepper, state["totp_salt"])
        matched = verify_code(
            secret, code or "", current_step(), state["totp_last_step"]
        )
        if matched is None:
            failure = self.auth_dao.record_preauth_failure(
                record["id"], MAX_PREAUTH_ATTEMPTS
            )
            if failure["voided"]:
                raise AuthCodeError(
                    "preauth-voided", "Too many wrong codes. Sign in again."
                )
            raise UnauthorizedError(_INVALID_CODE)
        self.dao.set_totp_last_step(record["user_id"], matched)
        if not self.auth_dao.consume_preauth_token(record["id"]):
            raise UnauthorizedError(_PREAUTH_INVALID)
        return self._issue_provider_session(state, totp_verified=True)

    def _issue_provider_session(
        self, user: dict[str, Any], *, totp_verified: bool
    ) -> dict[str, Any]:
        """Mirrors ``AuthService._issue_session``'s response contract, with
        ``totp_verified_at`` stamped on the new session after a real code."""
        token = create_session_token()
        if totp_verified:
            self.auth_dao.create_totp_session(user["id"], hash_session_token(token))
        else:
            self.auth_dao.create_session(user["id"], hash_session_token(token))
        return {
            "token": token,
            "user": {
                "id": str(user["id"]),
                "email": user["email"],
                "fullName": user.get("full_name"),
                "role": user.get("role"),
                "mustChangePassword": bool(user.get("must_change_password")),
            },
        }

    def _warn_on_pepper_rotation(self, state: dict[str, Any]) -> None:
        """A stored pepper key that no longer matches the running pepper means
        every code from this enrolment will fail — name the cause in the log
        (the fix is the peer reset path / re-enrolment), never in the answer."""
        stored = state.get("totp_pepper_key")
        if stored and stored != pepper_key(self.pepper):
            logger.warning(
                "TOTP pepper key mismatch for provider %s: the pepper was "
                "rotated after enrolment; re-enrolment (peer reset) required",
                state["id"],
            )

    def end_support_on_logout(self, token: str) -> None:
        """Logout ends the calling session's live step-in with its own cause
        (AE29). Quiet no-op for every non-stepped-in session."""
        if not token:
            return
        info = self.dao.find_support_for_token(hash_session_token(token))
        if info is None:
            return
        actor = {
            "id": str(info["id"]),
            "email": info["email"],
            "full_name": info["full_name"],
            "provider": {"totp_enrolled": True},
        }
        self.dao.end_support_session(
            actor=actor,
            support_session_id=str(info["support_session_id"]),
            cause="logout",
        )

    # --- step-up (fresh-code) gate -------------------------------------------

    def require_step_up(self, actor: dict[str, Any], code: str | None) -> None:
        """Step-in, account create/remove and TOTP reset ride on a code
        verified within the last fifteen minutes on the CALLING session; a
        stale clock demands a fresh ``code``, which also restarts it."""
        verified = actor.get("totp_verified_at")
        if verified is not None:
            age = dt.datetime.now(dt.timezone.utc) - verified
            if age <= dt.timedelta(minutes=STEP_UP_FRESH_MINUTES):
                return
        if not str(code or "").strip():
            raise AuthCodeError(
                "totp-step-up-required",
                "Enter a fresh authenticator code to confirm this action",
            )
        self._verify_step_up_code(actor, code or "")

    def _verify_step_up_code(self, actor: dict[str, Any], code: str) -> None:
        state = self.dao.get_provider_verify_state(actor["id"])
        if (
            state is None
            or not state.get("provider_active")
            or state["totp_enrolled_at"] is None
        ):
            raise UnauthorizedError(_INVALID_CODE)
        self._warn_on_pepper_rotation(state)
        secret = derive_secret(self.pepper, state["totp_salt"])
        matched = verify_code(
            secret, code, current_step(), state["totp_last_step"]
        )
        if matched is None:
            raise UnauthorizedError(_INVALID_CODE)
        self.dao.set_totp_last_step(actor["id"], matched)
        self.auth_dao.refresh_session_totp(actor["session_id"])

    # --- enrolment (the calling provider's own second factor) ----------------

    def enrol(self, actor: dict[str, Any]) -> dict[str, Any]:
        """Regenerate the salt and hand back the derived secret ONCE (manual
        entry + otpauth URI; QR stays out of scope). 409 while enrolled."""
        salt = secrets.token_hex(16)
        self.dao.start_enrolment(actor["id"], salt, pepper_key(self.pepper))
        secret = derive_secret(self.pepper, salt)
        return {
            "secret": secret_b32(secret),
            "uri": provisioning_uri(actor["email"], secret),
        }

    def confirm_enrolment(self, actor: dict[str, Any], code: str) -> dict[str, Any]:
        """The first valid code enables the factor and marks the calling
        session code-verified — lifting the enrolment 409 allowlist."""
        state = self.dao.get_provider_verify_state(actor["id"])
        if state is None or not state.get("provider_active"):
            raise NotFoundError("Provider account not found")
        if state["totp_enrolled_at"] is not None:
            raise ConflictError("Second factor is already enrolled")
        secret = derive_secret(self.pepper, state["totp_salt"])
        matched = verify_code(secret, code or "", current_step(), None)
        if matched is None:
            raise UnauthorizedError(_INVALID_CODE)
        self.dao.confirm_enrolment(actor["id"], matched, actor["session_id"])
        return {"ok": True}

    # --- schools -------------------------------------------------------------

    def list_schools(self) -> list[dict[str, Any]]:
        return [
            {
                "schoolId": str(row["school_id"]),
                "name": row["name"],
                "code": row["code"],
                "setupState": row["setup_state"],
                "students": row["students"],
                "buses": row["buses"],
                "drivers": row["drivers"],
                "runsToday": row["runs_today"],
                "lastStaffWrite": (
                    row["last_staff_write"].isoformat()
                    if row["last_staff_write"]
                    else None
                ),
            }
            for row in self.dao.list_schools_with_health()
        ]

    def create_school(
        self,
        actor: dict[str, Any],
        *,
        name: str,
        lat: float | None,
        lng: float | None,
        morning_bell: str | None,
        afternoon_bell: str | None,
        director_email: str,
        director_name: str | None,
    ) -> dict[str, Any]:
        """The school with its generated, non-editable code, then its first
        director through the U8 seam — the same created/offered contract, the
        temporary password revealed once (AE15 provider branch)."""
        school_name = str(name or "").strip()
        if not school_name:
            raise BadRequestError("A school name is required")
        director_email = clean_email(director_email, required=True)
        for bell in (morning_bell, afternoon_bell):
            if bell is not None and not _HHMM.match(str(bell).strip()):
                raise BadRequestError("Bell times must be HH:MM")

        school = self._create_school_with_code(
            actor, school_name, lat, lng, morning_bell, afternoon_bell
        )
        scope = SchoolScope(
            user_id=str(actor["id"]),
            school_id=str(school["id"]),
            role="director",
            actor_kind="provider",
        )
        director = self.staff.create_staff(
            scope, director_email, director_name, "director", actor=actor
        )
        return {
            "school": {
                "id": str(school["id"]),
                "name": school["name"],
                "code": school["code"],
                "lat": school["lat"],
                "lng": school["lng"],
                "morningBell": school["morning_bell"],
                "afternoonBell": school["afternoon_bell"],
            },
            "director": director,
        }

    def _create_school_with_code(
        self,
        actor: dict[str, Any],
        name: str,
        lat: float | None,
        lng: float | None,
        morning_bell: str | None,
        afternoon_bell: str | None,
    ) -> dict[str, Any]:
        """Code format ``AAA-NNN``: three letters from the name (padded with
        X), then the next zero-padded number for that prefix. The unique
        index is the race backstop; a collision just tries the next number."""
        from psycopg import errors as pg_errors

        prefix = self._code_prefix(name)
        taken = self.dao.list_school_codes(prefix)
        number = 1 + max(
            (
                int(code.rsplit("-", 1)[1])
                for code in taken
                if code.rsplit("-", 1)[-1].isdigit()
            ),
            default=0,
        )
        for attempt in range(8):
            code = f"{prefix}-{number + attempt:03d}"
            try:
                return self.dao.create_school(
                    name=name, lat=lat, lng=lng,
                    morning_bell=morning_bell, afternoon_bell=afternoon_bell,
                    code=code, actor=actor,
                )
            except pg_errors.UniqueViolation:
                continue
        raise ConflictError("Could not allocate a unique school code")

    @staticmethod
    def _code_prefix(name: str) -> str:
        letters = "".join(c for c in name.upper() if "A" <= c <= "Z")[:3]
        return (letters or "SCH").ljust(3, "X")

    # --- step-in / step-out --------------------------------------------------

    def step_in(
        self,
        actor: dict[str, Any],
        *,
        school_id: str,
        reason: str | None,
        code: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        clean_reason = str(reason or "").strip()
        if not 1 <= len(clean_reason) <= 500:
            raise BadRequestError("A reason of 1 to 500 characters is required")
        self.require_step_up(actor, code)
        school_uuid = _uuid_or_not_found(school_id, "School not found")
        session = self.dao.step_in(
            actor=actor,
            session_id=actor["session_id"],
            school_id=school_uuid,
            reason=clean_reason,
            ip=ip,
            user_agent=user_agent,
        )
        return {
            "supportSessionId": session["id"],
            "startedAt": session["started_at"].isoformat(),
            "school": {
                "id": str(session["school"]["id"]),
                "name": session["school"]["name"],
                "code": session["school"]["code"],
            },
        }

    def step_out(self, actor: dict[str, Any]) -> dict[str, Any]:
        """Idempotent: ending nothing is not an error (the banner's Exit may
        race the four-hour janitor or a supersede)."""
        support = actor.get("support_session")
        ended = False
        if support:
            ended = self.dao.end_support_session(
                actor=actor,
                support_session_id=str(support["id"]),
                cause="step-out",
            )
        return {"ok": True, "ended": ended}

    # --- accounts ------------------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "userId": str(row["user_id"]),
                "email": row["email"],
                "fullName": row["full_name"],
                "totpEnrolled": bool(row["totp_enrolled"]),
                "createdAt": row["created_at"].isoformat(),
            }
            for row in self.dao.list_accounts()
        ]

    def create_account(
        self,
        actor: dict[str, Any],
        *,
        email: str,
        full_name: str | None,
        code: str | None,
    ) -> dict[str, Any]:
        self.require_step_up(actor, code)
        email = clean_email(email, required=True)
        name = str(full_name or "").strip() or email.split("@", 1)[0]
        if self.dao.email_taken(email):
            # Providers never piggyback an existing identity (R19) — and this
            # surface is Kuumbai-internal, so the refusal may say why.
            raise ConflictError("An account with this email already exists")
        password = generate_temporary_password()
        row = self.dao.create_account(
            email=email,
            full_name=name,
            password_hash=hash_password(password),
            totp_salt=secrets.token_hex(16),
            temporary_password_ttl_hours=TEMPORARY_PASSWORD_TTL_HOURS,
            actor=actor,
        )
        return {
            "status": "created",
            "userId": str(row["id"]),
            "email": row["email"],
            "fullName": row["full_name"],
            # Revealed once, here only: never logged, never audited.
            "temporaryPassword": password,
        }

    def remove_account(
        self, actor: dict[str, Any], user_id: str, *, code: str | None
    ) -> None:
        self.require_step_up(actor, code)
        target = _uuid_or_not_found(user_id, "Provider account not found")
        self.dao.remove_account(target, actor=actor)

    def reset_totp(
        self, actor: dict[str, Any], user_id: str, *, code: str | None
    ) -> dict[str, Any]:
        self.require_step_up(actor, code)
        target = _uuid_or_not_found(user_id, "Provider account not found")
        self.dao.reset_totp(
            target,
            salt=secrets.token_hex(16),
            pepper_key=pepper_key(self.pepper),
            actor=actor,
        )
        return {"ok": True}

    # --- audit reader --------------------------------------------------------

    def list_audit(
        self, school_id: str | None, support_session_id: str | None
    ) -> list[dict[str, Any]]:
        school = (
            _uuid_or_not_found(school_id, "School not found") if school_id else None
        )
        support = (
            _uuid_or_not_found(support_session_id, "Support session not found")
            if support_session_id
            else None
        )
        return [
            {
                "id": str(row["id"]),
                "action": row["action"],
                "actorId": str(row["actor_id"]) if row["actor_id"] else None,
                "actorName": row["actor_name"],
                "actorEmail": row["actor_email"],
                "actorKind": row["actor_kind"],
                "schoolId": str(row["school_id"]) if row["school_id"] else None,
                "supportSessionId": (
                    str(row["support_session_id"])
                    if row["support_session_id"]
                    else None
                ),
                "resourceType": row["resource_type"],
                "resourceId": row["resource_id"],
                "detail": row["detail"],
                "createdAt": row["created_at"].isoformat(),
            }
            for row in self.dao.list_audit(school, support)
        ]
