"""Create an administrator from the server's shell.

    docker compose exec surveillance python -m app.cli.create_admin --username admin

The password is read from ARGUS_ADMIN_PASSWORD, or prompted for (twice). This
is how production deployments create their first account: HTTP bootstrap is
refused unless DEBUG or AUTH_BOOTSTRAP_TOKEN is used.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from pydantic import ValidationError
from sqlalchemy import select

from app.database import get_session_factory
from app.models import User, UserRole
from app.schemas import UserCreate
from app.services.auth import get_password_hash


class AdminCreationError(Exception):
    pass


async def create_admin(username: str, password: str, tenant_id: str = "1", session_factory=None) -> User:
    try:
        UserCreate(username=username, password=password, role=UserRole.ADMIN.value)
    except ValidationError as exc:
        raise AdminCreationError("; ".join(err["msg"] for err in exc.errors())) from exc
    factory = session_factory or get_session_factory()
    async with factory() as session:
        if (await session.execute(select(User.id).where(User.username == username))).scalar_one_or_none():
            raise AdminCreationError(f"user {username!r} already exists")
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN.value,
            tenant_id=tenant_id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user


def _password() -> str:
    from_env = os.environ.get("ARGUS_ADMIN_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass("Password (12+ characters): ")
    if first != getpass.getpass("Repeat password: "):
        raise AdminCreationError("passwords do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", required=True)
    parser.add_argument("--tenant", default="1", help="tenant id (default 1)")
    args = parser.parse_args(argv)
    try:
        user = asyncio.run(create_admin(args.username, _password(), args.tenant))
    except AdminCreationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created administrator {user.username!r} in tenant {user.tenant_id!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
