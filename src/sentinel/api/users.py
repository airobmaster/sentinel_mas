"""Test users in the Cognito user pool, one per role. Created with no welcome email (the users have
example.com addresses) and a permanent password; the passwords are written only to .env."""

import json
import re
import secrets
import string

from sentinel.api.auth import TEST_USERS
from sentinel.config import REPO_ROOT, settings

ENV_FILE = REPO_ROOT / ".env"


def new_password() -> str:
    """20 characters meeting the pool policy (upper, lower, digit, symbol)."""
    symbols = "!#%*+-=?@^_"
    required = [secrets.choice(s) for s in (string.ascii_uppercase, string.ascii_lowercase, string.digits, symbols)]
    rest = [secrets.choice(string.ascii_letters + string.digits + symbols) for _ in range(16)]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def set_env(values: dict[str, str]) -> None:
    """Add or replace KEY=value lines in .env (keeps every other line)."""
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
            text = re.sub(rf"^{key}=.*$", lambda _, line=line: line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def bootstrap(reset: bool = False) -> list[str]:
    """Create (or, with reset, re-password) the test users and put them in their role groups."""
    import boto3

    if not settings.cognito_user_pool_id:
        raise RuntimeError("set SENTINEL_COGNITO_USER_POOL_ID (terraform output env in infra/terraform/identity)")
    idp = boto3.client("cognito-idp", region_name=settings.aws_region)
    pool = settings.cognito_user_pool_id
    passwords = dict(settings.cognito_test_users)
    report = []
    for role, username in TEST_USERS.items():
        try:
            idp.admin_get_user(UserPoolId=pool, Username=username)
            exists = True
        except idp.exceptions.UserNotFoundException:
            idp.admin_create_user(UserPoolId=pool, Username=username, MessageAction="SUPPRESS",
                                  UserAttributes=[{"Name": "email", "Value": f"{username}@example.com"},
                                                  {"Name": "email_verified", "Value": "true"}])
            exists = False
        if reset or not exists or username not in passwords:
            passwords[username] = new_password()
            idp.admin_set_user_password(UserPoolId=pool, Username=username, Password=passwords[username],
                                        Permanent=True)
        idp.admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=role)
        report.append(f"{username:18} role {role:6} {'existing' if exists else 'created'}")
    set_env({"SENTINEL_COGNITO_TEST_USERS": json.dumps(passwords)})
    return report
