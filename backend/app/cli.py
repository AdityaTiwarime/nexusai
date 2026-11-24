"""Admin command line.

Create the platform super admin (there is deliberately no API for this):

    python -m app.cli create-superadmin --email ops@example.com --name "Platform Ops"

The password is read from the NEXUS_SUPERADMIN_PASSWORD environment variable or
prompted for, so it never appears in shell history.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.auth.schemas import PASSWORD_MIN
from app.modules.auth.security import hash_password


async def create_superadmin(settings: Settings, email: str, name: str, password: str) -> str:
    email = email.strip().lower()
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"Password must be at least {PASSWORD_MIN} characters")
    db = Database(settings)
    try:
        async with db.session_factory() as session:
            if await session.scalar(select(User.id).where(User.email == email)):
                raise ValueError("A user with this email already exists")
            user = User(
                organization_id=None,
                email=email,
                full_name=name,
                password_hash=hash_password(password),
                role=Role.SUPER_ADMIN.value,
            )
            session.add(user)
            await session.commit()
            return str(user.id)
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-superadmin", help="Create the platform super admin")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Platform Admin")
    args = parser.parse_args(argv)

    password = os.environ.get("NEXUS_SUPERADMIN_PASSWORD") or getpass.getpass("Password: ")
    try:
        user_id = asyncio.run(create_superadmin(Settings(), args.email, args.name, password))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Super admin created: {user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
