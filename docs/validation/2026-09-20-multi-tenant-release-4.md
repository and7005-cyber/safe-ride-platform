# Validation record — multi-tenant Release 4 (the scoped application)

- **Date:** 2026-09-20 (morning after the move; the pilot confirmed a real
  driver PIN sign-in first, closing Release 3's last gate) · **Artifact:**
  branch `release/tenancy-r4` = `main` with migration 015 removed in a
  release-only commit (015 is the Release 5 act: constraints, RLS,
  seeded-admin retirement) · **Surfaces:** backend, then frontend.
- **No migration:** migrate output `applied: []` (001–014 skipped), role
  synced, bootstrap correctly skipped (v1 applied). `SCOPE_HEADER_REQUIRED`
  stays false; the API stays on the master role until Release 5.

## Go/No-Go gates (plan §Operational Notes, Release 4)

| Gate | Observed | Verdict |
|---|---|---|
| Isolation manifest + full certification | Green on the branch (2026-08-28 certification; 81-test e2e) — the deployed code is that certified tree minus 015 | **GO** |
| Backend deploy | rc 0; health ok; zero errors and zero 42501 denials in the API logs after the swap | **GO** |
| Compatibility window (old frontend → new API) | `admin@test.com` login 200, `/me` keeps legacy `role: admin` AND carries the membership (`MSB-001`, director); students 200 via explicit `X-School-Id` and via single-membership fallback (32 rows both ways); staff API 200 (1 member) | **GO** |
| Not-found contract live | A non-school id in `X-School-Id` → 404 | **GO** |
| Provider surface live | Password step with the SSM initial password → 200 with **no session**, a pre-auth token, `totpEnrolmentRequired: true` (token left to expire; enrolment is the operator's own act) | **GO** |
| Frontend deploy | rc 0; CloudFront invalidated; https://saferidelive.co.ke → 200 | **GO** |

Operator-fetch note: the provider initial passwords and the TOTP pepper live
in the DEFAULT CLI region (the deploy reads them there and passes them as
CloudFormation parameters); only the runtime-read bootstrap payload is
pinned to af-south-1.

## The remaining Release 4 gate is human, in-app

Per the plan, before Release 5: the provider signs in (initial password from
`/saferide/provider-initial-password/<slug>`), changes the password, enrols
the second factor, creates Msingi's real director (and a coordinator);
the director signs in and sets their password; **one full pilot day runs on
the scoped code**; then the seeded admin's membership is removed in-app from
the Staff page. `docs/user-manual/provider-guide.md` walks every step.

## Rollback position

Redeploy the Release 3 backend (`release/tenancy-r3`) and the previous
frontend build; the seeded admin still signs in under old code. No schema
changed in this release.
