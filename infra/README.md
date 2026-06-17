# SafeRide AWS infrastructure

Production deployment for SafeRide Kenya.

- **Frontend** — `frontend/dist` (Vite build) → private **S3** bucket served by
  **CloudFront** (Origin Access Control) at `https://saferidelive.co.ke` (+ `www`).
- **Backend** — FastAPI wrapped with **Mangum** on **Lambda**, behind an
  **API Gateway HTTP API**, with **RDS PostgreSQL** in an isolated VPC, at
  `https://api.saferidelive.co.ke`. The app's existing bearer-token auth is
  unchanged; API Gateway is a transparent proxy.
- **DNS** — `saferidelive.co.ke` stays registered at **Truehost** (`.co.ke`
  cannot move to Route 53) but is **delegated** to a Route 53 hosted zone.

| Thing | Region | Why |
|-------|--------|-----|
| Route 53 hosted zone `Z0459820URO1AH08IO8T` | global | DNS for the domain |
| CloudFront + its ACM cert | us-east-1 | CloudFront certs must live in us-east-1 |
| API Gateway, Lambda, RDS, its ACM cert | af-south-1 | closest region to Kenya |

## One-time manual step (Truehost)

Set the domain's nameservers at Truehost to the four Route 53 values
(`./scripts/check-delegation.sh` prints them and tells you when it's live).

## Deploy order

```bash
# 0. Confirm the domain is delegated to Route 53
./scripts/check-delegation.sh

# 1. Issue + DNS-validate the ACM certs (needs delegation live)
./scripts/request-certs.sh

# 2. Build + deploy backend, run DB migrations/seeds
./scripts/deploy-backend.sh

# 3. Build frontend against the API, deploy S3+CloudFront, upload, invalidate
./scripts/deploy-frontend.sh
```

`scripts/config.sh` holds the shared config (domain, zone id, regions, stack
names). Secrets (DB password, PIN pepper) are generated once and stored in SSM
SecureString (`/saferide/db-password`, `/saferide/pin-pepper`). Cert ARNs land
in `.state/certs.env` (gitignored).

## Early smoke test (before DNS / certs)

Both stacks deploy without a cert: the backend exposes its
`*.execute-api.af-south-1.amazonaws.com` URL and the frontend its
`*.cloudfront.net` URL, so the infra can be validated before the Truehost
nameserver change propagates.

## Teardown

```bash
aws cloudformation delete-stack --region us-east-1   --stack-name saferide-frontend
aws cloudformation delete-stack --region af-south-1  --stack-name saferide-backend   # RDS snapshots on delete
```
