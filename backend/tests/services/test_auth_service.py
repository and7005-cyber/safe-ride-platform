import datetime as dt
import itertools
import logging
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.core.security import (
    PIN_SESSION_TOKEN_PREFIX,
    hash_password,
    hash_pin_hmac,
    hash_pin_v2,
    hash_session_token,
    verify_password,
)
from app.services import auth_service as auth_module
from app.services.auth_service import (
    FORGOT_MESSAGE,
    PIN_LOCK_MAX_FAILURES,
    PIN_LOCK_WINDOW_MINUTES,
    VERIFY_SIGN_IN_MESSAGE,
    AuthService,
    PinLoginError,
    PinSessionError,
    SchoolPinLockedError,
    VerificationError,
)
from app.services.mail_service import (
    MAIL_CAP_PER_HOUR,
    RESET_TTL_MINUTES,
    VERIFY_TTL_MINUTES,
    MailService,
    SimulatedTransport,
)

PEPPER = get_settings().pin_pepper
SCHOOL_A = "5cae0000-0000-0000-0000-000000000001"
SCHOOL_B = "5cae0000-0000-0000-0000-000000000002"


_ids = itertools.count(1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class FakeAuthDao:
    """In-memory AuthDao: users, sessions, and the emailed-token table with
    its purpose, expiry, use and creation time (the cap counts these)."""

    def __init__(self):
        self.users = {
            "u-admin": {
                "id": "u-admin", "email": "admin@test.com",
                "password_hash": hash_password("test1234."),
                "full_name": "Admin", "phone": None, "pin_hash": None, "role": "admin",
            },
            "u-driver": {
                "id": "u-driver", "email": "drive@test.com",
                "password_hash": hash_password("Test1234"),
                "full_name": "Driver", "phone": None,
                "pin_hash": hash_pin_hmac("1234", PEPPER), "role": "driver",
            },
        }
        # Hardening U10: schools (id → public code, driver code), driver
        # memberships, the failure window and the session's school.
        self.schools = {
            SCHOOL_A: {"id": SCHOOL_A, "code": "GFA-001", "name": "Greenfield", "driver_code": "GFA07K1Q"},
            SCHOOL_B: {"id": SCHOOL_B, "code": "ITS-002", "name": "IT Second", "driver_code": "SEC2ND8B"},
        }
        self.memberships = [
            {"user_id": "u-driver", "school_id": SCHOOL_A, "role": "driver", "removed": False},
        ]
        self.failures: list[dict] = []
        self.session_schools: dict[str, str | None] = {}
        self.upgrades: list[tuple] = []
        self.sessions: dict[str, str] = {}
        self.revoked_all_for: list[str] = []
        self.kept_sessions: list[tuple[str, str]] = []
        self.voided_tokens_for: list[str] = []
        self.tokens: list[dict] = []
        self.push: dict[str, int] = {}

    # users ------------------------------------------------------------------

    @staticmethod
    def _verified(user) -> bool:
        return not user.get("email_verification_required") or user.get("email_verified_at") is not None

    def _view(self, user) -> dict:
        return {**user, "email_verified": self._verified(user)}

    def get_user_by_email(self, email):
        for u in self.users.values():
            if u["email"].lower() == email.lower():
                return self._view(u)
        return None

    def get_user_by_id(self, user_id):
        user = self.users.get(user_id)
        if not user:
            return None
        return {**self._view(user), "is_provider": bool(user.get("provider"))}

    # driver sign-in bound to the school (hardening U10) ------------------------

    def school_by_driver_code(self, driver_code):
        for school in self.schools.values():
            if school["driver_code"] == driver_code:
                return {"id": school["id"], "name": school["name"]}
        return None

    def school_for_pin_login(self, school_id):
        school = self.schools.get(school_id)
        return {"id": school["id"], "code": school["code"]} if school else None

    def pin_failures_since(self, school_id, source, window_minutes, limit):
        cutoff = _now() - dt.timedelta(minutes=window_minutes)
        times = sorted(
            f["at"] for f in self.failures
            if f["school_id"] == school_id and f["source"] == source and f["at"] > cutoff
        )
        retry = None
        if len(times) > limit:
            leaves = times[len(times) - limit - 1] + dt.timedelta(minutes=window_minutes)
            retry = (leaves - _now()).total_seconds()
        return {"failures": len(times), "retry_after_s": retry}

    def record_pin_failure(self, school_id, source, at=None):
        self.failures.append({"school_id": school_id, "source": source, "at": at or _now()})

    def driver_members_for_school(self, school_id):
        out = []
        for m in self.memberships:
            user = self.users.get(m["user_id"])
            if (
                m["school_id"] == school_id and m["role"] == "driver" and not m["removed"]
                and user and user.get("disabled_at") is None and user.get("pin_hash")
            ):
                out.append({"id": user["id"], "pin_hash": user["pin_hash"]})
        return out

    def create_unverified_parent(self, email, password_hash, full_name, token_hash, ttl):
        uid = f"u-{email}"
        self.users[uid] = {
            "id": uid, "email": email, "password_hash": password_hash,
            "full_name": full_name, "phone": None, "pin_hash": None, "role": "parent",
            "email_verification_required": True, "email_verified_at": None,
        }
        self._insert_token(uid, token_hash, "verify", ttl)
        return {"id": uid, "email": email, "full_name": full_name}

    # sessions ----------------------------------------------------------------

    def create_session(self, user_id, token_hash, ttl_hours=16, *, last_school_id=None, pin_upgrade=None):
        if pin_upgrade is not None:
            legacy, salted = pin_upgrade
            if self.users[user_id]["pin_hash"] == legacy:
                self.users[user_id]["pin_hash"] = salted
                self.upgrades.append((user_id, legacy, salted))
        self.sessions[token_hash] = user_id
        self.session_schools[token_hash] = last_school_id

    def get_session_user(self, token_hash):
        uid = self.sessions.get(token_hash)
        return self._view(self.users[uid]) if uid else None

    def revoke_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def revoke_all_sessions(self, user_id):
        self.revoked_all_for.append(user_id)
        self.sessions = {k: v for k, v in self.sessions.items() if v != user_id}

    def revoke_other_sessions(self, user_id, keep_session_id):
        self.kept_sessions.append((user_id, keep_session_id))

    # tokens -------------------------------------------------------------------

    def _insert_token(self, user_id, token_hash, purpose, ttl_minutes):
        self.tokens.append({
            "id": next(_ids), "user_id": user_id, "token_hash": token_hash,
            "purpose": purpose, "ttl_minutes": ttl_minutes, "created_at": _now(),
            "expires_at": _now() + dt.timedelta(minutes=ttl_minutes), "used_at": None,
        })

    def _live(self, token):
        return token["used_at"] is None and token["expires_at"] > _now()

    def create_reset_token(
        self, user_id, token_hash, purpose="reset", ttl_minutes=30, *,
        cap_per_hour=None, void_earlier=False,
    ):
        if cap_per_hour is not None:
            hour_ago = _now() - dt.timedelta(hours=1)
            recent = [t for t in self.tokens if t["user_id"] == user_id and t["created_at"] > hour_ago]
            if len(recent) >= cap_per_hour:
                return False
        if void_earlier:
            for t in self.tokens:
                if t["user_id"] == user_id and t["purpose"] == purpose and t["used_at"] is None:
                    t["used_at"] = _now()
        self._insert_token(user_id, token_hash, purpose, ttl_minutes)
        return True

    def consume_reset_token(self, token_hash, purpose="reset"):
        for t in self.tokens:
            user = self.users.get(t["user_id"])
            if (
                t["token_hash"] == token_hash and t["purpose"] == purpose and self._live(t)
                and user is not None and user.get("disabled_at") is None
            ):
                t["used_at"] = _now()
                return {"user_id": t["user_id"]}
        return None

    def live_verify_token_owner(self, token_hash):
        for t in self.tokens:
            if t["token_hash"] == token_hash and t["purpose"] == "verify" and self._live(t):
                return t["user_id"]
        return None

    def consume_verify_token(self, token_hash, user_id):
        for t in self.tokens:
            if (
                t["token_hash"] == token_hash and t["purpose"] == "verify"
                and t["user_id"] == user_id and self._live(t)
            ):
                t["used_at"] = _now()
                return True
        return False

    def mark_email_verified(self, user_id):
        user = self.users[user_id]
        if user.get("email_verified_at") is not None:
            return None
        user["email_verified_at"] = _now()
        return {"email": user["email"], "was_unverified": bool(user.get("email_verification_required"))}

    def delete_push_registrations(self, user_id):
        return self.push.pop(user_id, 0)

    def get_user_credentials(self, user_id):
        user = self.users.get(user_id)
        if not user:
            return None
        return {
            "id": user_id, "email": user["email"], "password_hash": user["password_hash"],
            "must_change_password": bool(user.get("must_change_password")),
        }

    def set_user_password(self, user_id, password_hash):
        user = self.users[user_id]
        user["password_hash"] = password_hash
        user["password_changed_at"] = _now()
        user["must_change_password"] = False
        user["temporary_password_expires_at"] = None
        self.voided_tokens_for.append(user_id)
        for t in self.tokens:
            if t["user_id"] == user_id and t["used_at"] is None:
                t["used_at"] = _now()


class FakeAccountsDao:
    """Records signup auto-link calls (the linking rules themselves are covered
    in tests/services/test_parent_links.py)."""

    def __init__(self):
        self.linked: list[tuple] = []

    def link_parent_to_matching_students(self, parent_id, email):
        self.linked.append((parent_id, email))
        return 1


@pytest.fixture
def accounts():
    return FakeAccountsDao()


@pytest.fixture
def transport():
    return SimulatedTransport(record_links=True)


@pytest.fixture
def service(accounts, transport):
    return AuthService(dao=FakeAuthDao(), accounts=accounts, mail=MailService(transport))


def test_login_returns_role(service):
    result = service.login("admin@test.com", "test1234.")
    assert result["user"]["role"] == "admin"
    assert result["token"]


def test_login_wrong_password_rejected(service):
    with pytest.raises(UnauthorizedError):
        service.login("admin@test.com", "nope")


def test_signup_rejects_admin_role(service):
    with pytest.raises(BadRequestError):
        service.signup("new@test.com", "secret1", "New", "admin")


def test_signup_rejects_driver_role(service, accounts):
    # U8: driver self-signup is retired — schools create their drivers (R5).
    with pytest.raises(BadRequestError, match="Role must be parent"):
        service.signup("d@test.com", "secret1", "Driver", "driver")
    assert accounts.linked == []


def test_signup_issues_no_session_and_links_nothing(service, accounts, transport):
    result = service.signup("  p@test.com  ", "secret1", "Parent", "parent")
    # DL2 / execution decisions: no session in either branch.
    assert "token" not in result and "user" not in result
    assert result["ok"] is True
    user = service.dao.users["u-p@test.com"]
    assert user["email_verification_required"] is True and user["email_verified_at"] is None
    assert user["role"] == "parent"
    # U8/AE4: nothing linked (and so no incident) until the owner verifies.
    assert accounts.linked == []
    # The verification token was written with the account, then one mail.
    (token,) = service.dao.tokens
    assert token["purpose"] == "verify" and token["ttl_minutes"] == VERIFY_TTL_MINUTES
    (message,) = transport.sent
    assert message.kind == "verify" and message.to == "p@test.com"
    assert hash_session_token(_token_of(message.link)) == token["token_hash"]


def test_pin_login_single_match(service):
    result = service.pin_login("1234", SCHOOL_A)
    assert result["user"]["role"] == "driver"


def test_pin_login_unknown_pin_rejected(service):
    with pytest.raises(UnauthorizedError):
        service.pin_login("9999", SCHOOL_A)


# --- driver sign-in bound to the school (hardening U10) ----------------------------

SOURCE = "198.51.100.7"


def _add_driver(dao, uid, school_id, pin_hash, *, removed=False, disabled=False):
    dao.users[uid] = {
        "id": uid, "email": f"{uid}@saferide.test", "password_hash": hash_password("Test1234"),
        "full_name": uid, "phone": None, "pin_hash": pin_hash, "role": "driver",
        "disabled_at": _now() if disabled else None,
    }
    dao.memberships.append(
        {"user_id": uid, "school_id": school_id, "role": "driver", "removed": removed}
    )


def _session_school(service, result):
    return service.dao.session_schools[hash_session_token(result["token"])]


def test_a_pin_without_a_school_is_refused_with_school_required_and_logged(service, caplog):
    # AE8: no PIN-only fallback, whatever the PIN.
    caplog.set_level(logging.INFO, logger="saferide.auth")
    for school in (None, "", "   "):
        with pytest.raises(PinLoginError) as caught:
            service.pin_login("1234", school, source=SOURCE)
        assert caught.value.code == "school-required" and caught.value.status_code == 401
    lines = [r.getMessage() for r in caplog.records]
    assert len([line for line in lines if line.startswith("school-required")]) == 3
    assert service.dao.failures == [] and service.dao.sessions == {}


def test_with_the_school_the_pin_signs_in_and_the_session_remembers_it(service):
    result = service.pin_login("1234", SCHOOL_A, source=SOURCE)
    assert result["user"]["id"] == "u-driver"
    assert result["token"].startswith(PIN_SESSION_TOKEN_PREFIX)
    assert _session_school(service, result) == SCHOOL_A
    # Any spelling of the school's uuid is the same school.
    assert service.pin_login("1234", SCHOOL_A.upper(), source=SOURCE)["user"]["id"] == "u-driver"


def test_an_unknown_or_malformed_school_is_an_invalid_pin(service, caplog):
    caplog.set_level(logging.INFO, logger="saferide.auth")
    for school in ("5cae0000-0000-0000-0000-0000000000ff", "GFA-001", "not-a-uuid"):
        with pytest.raises(PinLoginError) as caught:
            service.pin_login("1234", school, source=SOURCE)
        assert caught.value.code == "invalid-pin"
    assert service.dao.failures == []  # no school to count against
    assert all(r.getMessage().startswith("pin-failure school=unknown") for r in caplog.records)


def test_a_legacy_pin_resolves_only_among_the_bound_schools_drivers(service):
    # u-driver (school A) holds the legacy global hash of 1234.
    with pytest.raises(PinLoginError) as caught:
        service.pin_login("1234", SCHOOL_B, source=SOURCE)
    assert caught.value.code == "invalid-pin"
    assert service.dao.users["u-driver"]["pin_hash"] == hash_pin_hmac("1234", PEPPER)


def test_a_legacy_sign_in_rewrites_the_row_to_the_salted_form(service):
    legacy = hash_pin_hmac("1234", PEPPER)
    salted = hash_pin_v2(SCHOOL_A, "1234", PEPPER)
    first = service.pin_login("1234", SCHOOL_A, source=SOURCE)
    assert first["user"]["id"] == "u-driver"
    assert service.dao.upgrades == [("u-driver", legacy, salted)]
    assert service.dao.users["u-driver"]["pin_hash"].startswith("v2:k1:")
    # The next sign-in matches the salted hash directly: no second upgrade.
    assert service.pin_login("1234", SCHOOL_A, source=SOURCE)["user"]["id"] == "u-driver"
    assert len(service.dao.upgrades) == 1


def test_two_schools_one_pin_each_driver_signs_in_only_at_their_own_school(service):
    # AE9: school A's driver holds the legacy hash, school B's the salted one.
    _add_driver(service.dao, "u-b", SCHOOL_B, hash_pin_v2(SCHOOL_B, "1234", PEPPER))
    assert service.pin_login("1234", SCHOOL_B, source=SOURCE)["user"]["id"] == "u-b"
    assert service.pin_login("1234", SCHOOL_A, source=SOURCE)["user"]["id"] == "u-driver"
    # After the upgrade both hold salted hashes of the same PIN, still apart.
    assert service.pin_login("1234", SCHOOL_B, source=SOURCE)["user"]["id"] == "u-b"
    assert service.pin_login("1234", SCHOOL_A, source=SOURCE)["user"]["id"] == "u-driver"


def test_a_salted_match_wins_over_a_legacy_one(service):
    _add_driver(service.dao, "u-salted", SCHOOL_A, hash_pin_v2(SCHOOL_A, "1234", PEPPER))
    assert service.pin_login("1234", SCHOOL_A, source=SOURCE)["user"]["id"] == "u-salted"
    assert service.dao.upgrades == []


@pytest.mark.parametrize("state", ["removed", "disabled"])
def test_a_removed_membership_or_a_disabled_driver_never_matches(service, state):
    _add_driver(
        service.dao, "u-gone", SCHOOL_B, hash_pin_v2(SCHOOL_B, "482915", PEPPER),
        removed=state == "removed", disabled=state == "disabled",
    )
    with pytest.raises(PinLoginError):
        service.pin_login("482915", SCHOOL_B, source=SOURCE)


def test_every_failure_writes_a_row_and_a_line_with_the_public_school_code_only(service, caplog):
    caplog.set_level(logging.INFO, logger="saferide.auth")
    for wrong in ("9999", "12ab", "482915"):
        with pytest.raises(PinLoginError):
            service.pin_login(wrong, SCHOOL_A, source=SOURCE)
    assert [(f["school_id"], f["source"]) for f in service.dao.failures] == [(SCHOOL_A, SOURCE)] * 3
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["pin-failure school=GFA-001"] * 3
    blob = " ".join(messages)
    for secret in ("9999", "482915", "GFA07K1Q", SOURCE, SCHOOL_A, "v2:", "hmac_sha256"):
        assert secret not in blob


def _fail(dao, school_id, source, n, minutes_ago):
    at = _now() - dt.timedelta(minutes=minutes_ago)
    for _ in range(n):
        dao.record_pin_failure(school_id, source, at=at)


def test_more_than_twenty_failures_lock_that_source_at_that_school(service, caplog):
    caplog.set_level(logging.INFO, logger="saferide.auth")
    _fail(service.dao, SCHOOL_A, SOURCE, PIN_LOCK_MAX_FAILURES, minutes_ago=10)
    # The twenty-first failure is still an ordinary wrong PIN...
    with pytest.raises(PinLoginError):
        service.pin_login("9999", SCHOOL_A, source=SOURCE)
    # ...and the twenty-second attempt is refused, the right PIN included.
    with pytest.raises(SchoolPinLockedError) as caught:
        service.pin_login("1234", SCHOOL_A, source=SOURCE)
    assert caught.value.status_code == 429 and caught.value.code == "school-pin-locked"
    # The oldest counted failure leaves the window in about five minutes.
    assert 280 < caught.value.retry_after_s <= 300
    assert any(r.getMessage().startswith("pin-locked school=GFA-001") for r in caplog.records)
    # A refused attempt writes no failure row (the lock cannot extend itself).
    assert len(service.dao.failures) == PIN_LOCK_MAX_FAILURES + 1
    # Another source at the same school and another school are unaffected.
    assert service.pin_login("1234", SCHOOL_A, source="203.0.113.9")["user"]["id"] == "u-driver"
    _add_driver(service.dao, "u-b", SCHOOL_B, hash_pin_v2(SCHOOL_B, "482915", PEPPER))
    assert service.pin_login("482915", SCHOOL_B, source=SOURCE)["user"]["id"] == "u-b"


def test_the_lock_lifts_once_the_window_has_passed(service):
    _fail(service.dao, SCHOOL_A, SOURCE, PIN_LOCK_MAX_FAILURES + 1, minutes_ago=PIN_LOCK_WINDOW_MINUTES + 1)
    assert service.pin_login("1234", SCHOOL_A, source=SOURCE)["user"]["id"] == "u-driver"


def test_the_driver_code_lookup_answers_id_and_name_only(service):
    for typed in ("GFA07K1Q", "gfa0-7k1q", " gfaO 7kIq ", "GFAO-7KLQ"):
        assert service.school_for_driver_code(typed) == {"schoolId": SCHOOL_A, "name": "Greenfield"}
    for unknown in ("GFA-001", "ZZZZ-ZZZZ", "", "GFA07K1QX", "GFA07K1U"):
        with pytest.raises(NotFoundError):
            service.school_for_driver_code(unknown)


def test_resolve_session_round_trip(service):
    token = service.login("admin@test.com", "test1234.")["token"]
    resolved = service.resolve_session(token)
    assert resolved["role"] == "admin"


def test_logout_invalidates_session(service):
    token = service.login("admin@test.com", "test1234.")["token"]
    service.logout(token)
    assert service.resolve_session(token) is None


# Temporary passwords (U8: R6) --------------------------------------------------


def _make_temporary(dao: FakeAuthDao, user_id: str, *, expired: bool) -> None:
    delta = dt.timedelta(hours=-1 if expired else 1)
    dao.users[user_id]["must_change_password"] = True
    dao.users[user_id]["temporary_password_expires_at"] = (
        dt.datetime.now(dt.timezone.utc) + delta
    )


def test_login_rejects_an_expired_temporary_password(service):
    _make_temporary(service.dao, "u-admin", expired=True)
    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        service.login("admin@test.com", "test1234.")


def test_login_accepts_an_unexpired_temporary_password(service):
    _make_temporary(service.dao, "u-admin", expired=False)
    result = service.login("admin@test.com", "test1234.")
    assert result["user"]["mustChangePassword"] is True


# Change password (U8: R29) -----------------------------------------------------


def _session_user(user_id: str) -> dict:
    return {"id": user_id, "session_id": "sess-keep"}


def test_change_password_requires_the_correct_current_password(service):
    with pytest.raises(BadRequestError, match="Current password is incorrect"):
        service.change_password(_session_user("u-admin"), "wrong", "NewPass123")
    # The hash is untouched and no sessions were revoked.
    assert verify_password("test1234.", service.dao.users["u-admin"]["password_hash"])
    assert service.dao.kept_sessions == []


def test_change_password_rotates_hash_and_revokes_other_sessions(service):
    _make_temporary(service.dao, "u-admin", expired=False)
    service.change_password(_session_user("u-admin"), "test1234.", "NewPass123")
    user = service.dao.users["u-admin"]
    assert verify_password("NewPass123", user["password_hash"])
    assert not verify_password("test1234.", user["password_hash"])
    # The forced-change flag and expiry are cleared, the change is stamped,
    # outstanding tokens are voided, and only OTHER sessions are revoked.
    assert user["must_change_password"] is False
    assert user["temporary_password_expires_at"] is None
    assert user["password_changed_at"] is not None
    assert service.dao.voided_tokens_for == ["u-admin"]
    assert service.dao.kept_sessions == [("u-admin", "sess-keep")]
    assert service.dao.revoked_all_for == []


def test_reset_password_clears_the_temporary_flag_and_revokes_all(service):
    # The forgot-password reset is a user-set password too: same single path.
    _make_temporary(service.dao, "u-admin", expired=False)
    service.dao.consume_reset_token = lambda token_hash, purpose: {"user_id": "u-admin"}
    service.reset_password("raw-token", "NewPass456")
    user = service.dao.users["u-admin"]
    assert verify_password("NewPass456", user["password_hash"])
    assert user["must_change_password"] is False
    assert service.dao.revoked_all_for == ["u-admin"]


# --- PIN sessions, the self-service change, relayed passwords (hardening U9) ------


def test_a_pin_sign_in_marks_its_session_and_a_password_sign_in_does_not(service):
    by_pin = service.pin_login("1234", SCHOOL_A)
    assert by_pin["token"].startswith(PIN_SESSION_TOKEN_PREFIX)
    assert by_pin["user"]["signedInWithPin"] is True
    assert service.resolve_session(by_pin["token"])["id"] == "u-driver"
    by_password = service.login("drive@test.com", "Test1234")
    assert not by_password["token"].startswith(PIN_SESSION_TOKEN_PREFIX)
    assert by_password["user"]["signedInWithPin"] is False


def test_change_password_is_refused_for_a_pin_session(service):
    pin_user = {"id": "u-driver", "session_id": "sess-keep", "pin_session": True}
    with pytest.raises(PinSessionError) as caught:
        service.change_password(pin_user, "Test1234", "NewPass123")
    assert caught.value.code == "pin-session" and caught.value.status_code == 403
    # Nothing changed: the hash, the sessions.
    assert verify_password("Test1234", service.dao.users["u-driver"]["password_hash"])
    assert service.dao.kept_sessions == []


def test_a_pin_session_held_on_the_change_screen_may_replace_the_temporary_password(service):
    # The forced change still needs the temporary password itself; refusing
    # it would strand a session ProtectedRoute holds on the change screen.
    _make_temporary(service.dao, "u-driver", expired=False)
    pin_user = {
        "id": "u-driver", "session_id": "sess-keep", "pin_session": True,
        "must_change_password": True,
    }
    service.change_password(pin_user, "Test1234", "NewPass123")
    assert verify_password("NewPass123", service.dao.users["u-driver"]["password_hash"])
    assert service.dao.kept_sessions == [("u-driver", "sess-keep")]


def test_a_voluntary_change_keeps_the_session_and_the_push_registrations(service):
    service.dao.push["u-admin"] = 2
    service.change_password(_session_user("u-admin"), "test1234.", "NewPass123")
    assert service.dao.kept_sessions == [("u-admin", "sess-keep")]
    assert service.dao.revoked_all_for == []
    assert service.dao.push == {"u-admin": 2}


RELAYED = "AB01CDEF2345GHJK"


@pytest.mark.parametrize(
    "typed", ["ab01cdef2345ghjk", "AB01 CDEF 2345 GHJK", "ab01-cdef-2345-ghjk", "aBoI cdef 2345 ghjk"]
)
def test_a_relayed_temporary_password_signs_in_however_it_is_typed(service, typed):
    service.dao.users["u-admin"]["password_hash"] = hash_password(RELAYED)
    _make_temporary(service.dao, "u-admin", expired=False)
    assert service.login("admin@test.com", typed)["user"]["mustChangePassword"] is True
    # ...and is accepted as the current password on the forced change.
    service.change_password(
        {**_session_user("u-admin"), "must_change_password": True}, typed, "NewPass123"
    )
    assert verify_password("NewPass123", service.dao.users["u-admin"]["password_hash"])


def test_a_password_the_account_chose_is_compared_exactly(service):
    # No temporary password held: no normalisation, ever.
    service.dao.users["u-admin"]["password_hash"] = hash_password(RELAYED)
    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        service.login("admin@test.com", "ab01-cdef-2345-ghjk")
    with pytest.raises(BadRequestError, match="Current password is incorrect"):
        service.change_password(_session_user("u-admin"), "ab01 cdef 2345 ghjk", "NewPass123")
    assert service.login("admin@test.com", RELAYED)["token"]


# --- emailed tokens (hardening U7) ------------------------------------------------


def _token_of(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def _add_parent(dao: FakeAuthDao, email: str, *, unverified: bool = False, **extra) -> str:
    uid = f"u-{email}"
    dao.users[uid] = {
        "id": uid, "email": email, "password_hash": hash_password("OldPass1"),
        "full_name": "Parent", "phone": None, "pin_hash": None, "role": "parent",
        "email_verification_required": unverified, "email_verified_at": None, **extra,
    }
    return uid


def test_forgot_for_an_existing_account_sends_exactly_one_single_use_reset_link(service, transport):
    # AE1.
    uid = _add_parent(service.dao, "known@test.com")
    result = service.forgot_password("known@test.com")
    assert result == {"ok": True, "message": FORGOT_MESSAGE}
    (message,) = transport.sent
    assert message.kind == "reset" and message.to == "known@test.com"
    (token,) = service.dao.tokens
    assert token["purpose"] == "reset" and token["ttl_minutes"] == RESET_TTL_MINUTES == 30
    raw = _token_of(message.link)
    service.reset_password(raw, "NewPass789")
    assert verify_password("NewPass789", service.dao.users[uid]["password_hash"])
    with pytest.raises(BadRequestError, match="invalid or has expired"):
        service.reset_password(raw, "AgainPass1")


def test_a_reset_link_is_dead_after_thirty_minutes(service, transport):
    _add_parent(service.dao, "late@test.com")
    service.forgot_password("late@test.com")
    service.dao.tokens[0]["expires_at"] = _now() - dt.timedelta(seconds=1)
    with pytest.raises(BadRequestError):
        service.reset_password(_token_of(transport.sent[0].link), "NewPass789")


def test_forgot_for_an_unknown_address_sends_nothing_and_answers_the_same(service, transport):
    # AE2.
    assert service.forgot_password("nobody@test.com") == {"ok": True, "message": FORGOT_MESSAGE}
    assert list(transport.sent) == []
    assert service.dao.tokens == []


def test_forgot_for_a_disabled_account_sends_nothing(service, transport):
    _add_parent(service.dao, "gone@test.com", disabled_at=_now())
    assert service.forgot_password("gone@test.com")["message"] == FORGOT_MESSAGE
    assert list(transport.sent) == [] and service.dao.tokens == []


def test_a_verify_token_cannot_reset_a_password_and_stays_usable(service, transport):
    service.signup("v@test.com", "secret1", "Parent", "parent")
    raw = _token_of(transport.sent[0].link)
    with pytest.raises(BadRequestError):
        service.reset_password(raw, "Hijack123")
    assert service.dao.tokens[0]["used_at"] is None
    session_user = {"id": "u-v@test.com", "email": "v@test.com", "email_verified": False}
    assert service.verify_email(session_user, raw)["emailVerified"] is True


def test_a_reset_token_cannot_verify_and_stays_usable(service, transport):
    uid = _add_parent(service.dao, "r@test.com", unverified=True)
    service.forgot_password("r@test.com")
    raw = _token_of(transport.sent[0].link)
    session_user = {"id": uid, "email": "r@test.com", "email_verified": False}
    with pytest.raises(VerificationError) as refused:
        service.verify_email(session_user, raw)
    assert refused.value.code == "link-invalid"
    assert service.dao.tokens[0]["used_at"] is None
    assert service.reset_password(raw, "NewPass789")["ok"] is True


def test_reset_success_revokes_sessions_drops_push_verifies_and_signs_in(service, accounts, transport):
    uid = _add_parent(service.dao, "lost-phone@test.com", unverified=True)
    service.dao.push[uid] = 2
    old_session = service.login("lost-phone@test.com", "OldPass1")["token"]
    service.forgot_password("lost-phone@test.com")
    result = service.reset_password(_token_of(transport.sent[-1].link), "NewPass789")
    assert service.resolve_session(old_session) is None
    assert uid in service.dao.revoked_all_for
    assert service.dao.push == {}
    assert service.dao.users[uid]["email_verified_at"] is not None
    # The account was waiting for verification: the inbox proved it, so the
    # held-back linking rule runs now.
    assert accounts.linked == [(uid, "lost-phone@test.com")]
    assert result["ok"] is True and result["user"]["id"] == uid
    assert result["user"]["emailVerified"] is True
    assert service.resolve_session(result["token"])["id"] == uid


def test_reset_of_a_grandfathered_account_stamps_verified_but_links_nothing_new(service, accounts, transport):
    uid = _add_parent(service.dao, "old@test.com")
    service.forgot_password("old@test.com")
    service.reset_password(_token_of(transport.sent[0].link), "NewPass789")
    assert service.dao.users[uid]["email_verified_at"] is not None
    assert accounts.linked == []


def test_reset_of_a_provider_account_returns_no_session(service, transport):
    uid = _add_parent(service.dao, "provider@test.com", provider=True)
    service.forgot_password("provider@test.com")
    result = service.reset_password(_token_of(transport.sent[0].link), "NewPass789")
    # The second factor still stands between a mailbox and a session.
    assert result == {"ok": True}
    assert verify_password("NewPass789", service.dao.users[uid]["password_hash"])


def test_a_transport_failure_keeps_the_token_logs_and_answers_ok(accounts, caplog):
    class Broken:
        def send(self, message):
            raise RuntimeError(f"SES down for {message.to}")

    service = AuthService(dao=FakeAuthDao(), accounts=accounts, mail=MailService(Broken()))
    _add_parent(service.dao, "unlucky@test.com")
    with caplog.at_level(logging.ERROR, logger="saferide.mail"):
        result = service.forgot_password("unlucky@test.com")
    assert result == {"ok": True, "message": FORGOT_MESSAGE}
    assert len(service.dao.tokens) == 1 and service.dao.tokens[0]["used_at"] is None
    (record,) = [r for r in caplog.records if r.name == "saferide.mail"]
    assert record.getMessage().startswith("mail-send-failed kind=reset request_id=")
    assert "unlucky@test.com" not in record.getMessage()
    assert record.exc_info is None


def test_the_per_recipient_cap_creates_and_sends_nothing_over_three_an_hour(service, transport):
    service.signup("cap@test.com", "secret1", "Parent", "parent")  # verify row 1
    service.forgot_password("cap@test.com")  # reset row 2
    service.forgot_password("cap@test.com")  # reset row 3
    assert len(service.dao.tokens) == MAIL_CAP_PER_HOUR == 3
    assert len(transport.sent) == 3
    assert service.forgot_password("cap@test.com") == {"ok": True, "message": FORGOT_MESSAGE}
    assert len(service.dao.tokens) == 3
    assert len(transport.sent) == 3
    # Rows older than an hour no longer count.
    for token in service.dao.tokens:
        token["created_at"] -= dt.timedelta(minutes=61)
    service.forgot_password("cap@test.com")
    assert len(transport.sent) == 4


# --- sign-up on an existing address (hardening U7) ---------------------------------


def test_signup_on_an_existing_address_creates_nothing_and_mails_the_account(service, transport, monkeypatch):
    hashes = []
    real_hash = auth_module.hash_password
    monkeypatch.setattr(auth_module, "hash_password", lambda pw: hashes.append(pw) or real_hash(pw))
    uid = _add_parent(service.dao, "taken@test.com")
    before = dict(service.dao.users)
    fresh = service.signup("fresh@test.com", "secret1", "Parent", "parent")
    existing = service.signup("Taken@test.com", "secret1", "Someone", "parent")
    # The identical answer, no session either way, and both branches hashed.
    assert existing == fresh
    assert "token" not in existing
    assert len(hashes) == 2
    assert set(service.dao.users) == set(before) | {"u-fresh@test.com"}
    kinds = [(m.kind, m.to) for m in transport.sent]
    assert kinds == [("verify", "fresh@test.com"), ("existing-account", "taken@test.com")]
    # The existing-account mail carries a working reset link.
    service.reset_password(_token_of(transport.sent[-1].link), "NewPass789")
    assert verify_password("NewPass789", service.dao.users[uid]["password_hash"])


# --- verification (hardening U8) -----------------------------------------------------


def _signup_and_link(service, transport, email):
    service.signup(email, "secret1", "Parent", "parent")
    return f"u-{email}", _token_of(transport.sent[-1].link)


def test_verify_without_a_session_is_refused_and_links_nothing(service, accounts, transport):
    _, raw = _signup_and_link(service, transport, "a@test.com")
    with pytest.raises(VerificationError) as refused:
        service.verify_email(None, raw)
    assert refused.value.code == "sign-in-required"
    assert str(refused.value) == VERIFY_SIGN_IN_MESSAGE
    assert accounts.linked == []
    assert service.dao.tokens[0]["used_at"] is None


def test_verify_from_another_account_is_refused_and_leaves_the_token(service, accounts, transport):
    uid, raw = _signup_and_link(service, transport, "owner@test.com")
    other_id, _ = _signup_and_link(service, transport, "other@test.com")
    intruder = {"id": other_id, "email": "other@test.com", "email_verified": False}
    with pytest.raises(VerificationError) as refused:
        service.verify_email(intruder, raw)
    assert refused.value.code == "wrong-account"
    assert str(refused.value) == VERIFY_SIGN_IN_MESSAGE
    assert accounts.linked == []
    assert service.dao.users[uid]["email_verified_at"] is None
    # Still works for its owner.
    owner = {"id": uid, "email": "owner@test.com", "email_verified": False}
    service.verify_email(owner, raw)
    assert accounts.linked == [(uid, "owner@test.com")]


def test_verify_from_the_owners_session_verifies_and_links(service, accounts, transport):
    uid, raw = _signup_and_link(service, transport, "mine@test.com")
    owner = {"id": uid, "email": "mine@test.com", "email_verified": False}
    assert service.verify_email(owner, raw) == {"ok": True, "emailVerified": True}
    assert service.dao.users[uid]["email_verified_at"] is not None
    assert accounts.linked == [(uid, "mine@test.com")]
    # A second click on the used link: the caller is verified now.
    owner["email_verified"] = True
    assert service.verify_email(owner, raw)["emailVerified"] is True
    assert accounts.linked == [(uid, "mine@test.com")]


def test_an_expired_verify_link_is_link_invalid(service, transport):
    uid, raw = _signup_and_link(service, transport, "slow@test.com")
    service.dao.tokens[0]["expires_at"] = _now() - dt.timedelta(seconds=1)
    with pytest.raises(VerificationError) as refused:
        service.verify_email({"id": uid, "email": "slow@test.com", "email_verified": False}, raw)
    assert refused.value.code == "link-invalid"


def test_resend_invalidates_the_previous_link(service, accounts, transport):
    uid, first = _signup_and_link(service, transport, "again@test.com")
    session_user = {"id": uid, "email": "again@test.com", "email_verified": False}
    assert service.resend_verification(session_user) == {"ok": True, "emailVerified": False}
    second = _token_of(transport.sent[-1].link)
    assert transport.sent[-1].kind == "verify"
    with pytest.raises(VerificationError):
        service.verify_email(session_user, first)
    service.verify_email(session_user, second)
    assert accounts.linked == [(uid, "again@test.com")]


def test_resend_over_the_cap_sends_nothing_and_keeps_the_last_link(service, transport):
    uid, first = _signup_and_link(service, transport, "many@test.com")
    session_user = {"id": uid, "email": "many@test.com", "email_verified": False}
    service.resend_verification(session_user)
    service.resend_verification(session_user)
    assert len(transport.sent) == 3
    last = _token_of(transport.sent[-1].link)
    assert service.resend_verification(session_user) == {"ok": True, "emailVerified": False}
    assert len(transport.sent) == 3
    # Over the cap nothing was voided: the newest link still works.
    service.verify_email(session_user, last)


def test_resend_for_a_verified_account_sends_nothing(service, transport):
    assert service.resend_verification(
        {"id": "u-admin", "email": "admin@test.com", "email_verified": True}
    ) == {"ok": True, "emailVerified": True}
    assert list(transport.sent) == []


def test_login_reports_the_verification_state(service, transport):
    service.signup("state@test.com", "secret1", "Parent", "parent")
    assert service.login("state@test.com", "secret1")["user"]["emailVerified"] is False
    # Grandfathered accounts (required false) read verified.
    assert service.login("admin@test.com", "test1234.")["user"]["emailVerified"] is True
