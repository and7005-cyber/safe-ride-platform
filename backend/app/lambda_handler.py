"""AWS Lambda entrypoint for the SafeRide API.

Wraps the existing FastAPI app with Mangum so it can run behind API Gateway
(HTTP API, payload format v2) without changing any application code. The app's
own bearer-token authentication is preserved exactly as-is.
"""

from mangum import Mangum

from app.main import app

# lifespan="off": Lambda freezes the execution environment between invocations,
# so FastAPI's startup/shutdown events (which close the psycopg pool) must not
# run per request. The connection pool is created lazily and reused across
# warm invocations in the same container.
handler = Mangum(app, lifespan="off")
