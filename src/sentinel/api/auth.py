"""API authentication and roles (TDD §12, Functional Spec BR-08, UC-03, UC-05, FR-109).

Every request carries a bearer access token. Roles come from the `cognito:groups` claim:
- "cognito": tokens issued by the Cognito user pool, checked against its public signing keys (JWKS);
  no AWS credentials are needed to verify them.
- "dev": HS256 tokens with the same claims, signed with a local secret (offline tests, CI).

Test users (one per role) are created by `sentinel auth bootstrap`; `token_for` signs them in.
"""

import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sentinel.config import settings

ROLES = ("l1", "l2", "mlro", "qa", "sme", "admin")
REVIEW_LEVELS = ("l1", "l2", "mlro")  # BR-17: each sees only the cases assigned to its level
TEST_USERS = {"l1": "l1.investigator", "l2": "l2.investigator", "mlro": "mlro.officer", "qa": "qa.reviewer",
              "sme": "sme.reviewer", "admin": "admin.user"}
DEV_ISSUER = "sentinel-dev"
CLOCK_SKEW = 30  # seconds: a laptop clock a little behind AWS must not reject fresh tokens

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    username: str
    roles: frozenset[str]

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    @property
    def sees_pii(self) -> bool:
        return self.has(*settings.pii_roles)


def issuer() -> str:
    return f"https://cognito-idp.{settings.aws_region}.amazonaws.com/{settings.cognito_user_pool_id}"


@lru_cache(maxsize=1)
def jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer()}/.well-known/jwks.json", cache_keys=True)


def verify(token: str) -> Principal:
    """Validate an access token and return who it belongs to; raises jwt.InvalidTokenError."""
    if settings.auth_mode == "cognito":
        key = jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer(), leeway=CLOCK_SKEW,
                            options={"require": ["exp", "iss", "token_use", "client_id"]})
        clients = {settings.cognito_tools_client_id, settings.cognito_workbench_client_id} - {None}
        if claims["token_use"] != "access" or claims["client_id"] not in clients:
            raise jwt.InvalidTokenError("not an access token for a Sentinel app client")
    else:
        claims = jwt.decode(token, settings.api_dev_secret, algorithms=["HS256"], issuer=DEV_ISSUER,
                            options={"require": ["exp", "iss"]})
    return Principal(claims["username"], frozenset(claims.get("cognito:groups") or []) & set(ROLES))


def dev_token(username: str, roles: list[str], ttl: int = 3600) -> str:
    """Local token with the same claims Cognito puts in an access token."""
    now = int(time.time())
    return jwt.encode({"iss": DEV_ISSUER, "username": username, "cognito:groups": roles, "token_use": "access",
                       "client_id": "dev", "iat": now, "exp": now + ttl}, settings.api_dev_secret, algorithm="HS256")


def token_for(role: str) -> str:
    """Access token for the test user of a role (console, CLI, integration tests)."""
    username = TEST_USERS[role]
    if settings.auth_mode == "dev":
        return dev_token(username, [role])
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    password = settings.cognito_test_users.get(username)
    if not password:
        raise RuntimeError(f"no password for {username} in .env; run `sentinel auth bootstrap`")
    # Password sign-in is a public Cognito call: no AWS credentials needed (works in CI and containers)
    idp = boto3.client("cognito-idp", region_name=settings.aws_region, config=Config(signature_version=UNSIGNED))
    resp = idp.initiate_auth(
        ClientId=settings.cognito_tools_client_id, AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password})
    return resp["AuthenticationResult"]["AccessToken"]


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return verify(creds.credentials)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}",
                            headers={"WWW-Authenticate": "Bearer"}) from e


def require(*roles: str):
    """Dependency: the caller must hold one of these roles."""

    def check(user: Principal = Depends(current_user)) -> Principal:
        if not user.has(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {' or '.join(roles)}")
        return user

    return check
