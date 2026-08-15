import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from sqlalchemy import delete
from app.database import init_db, get_session_factory
from app.models import User, UserRole
from app.services.auth import get_password_hash

async def main():
    password = os.environ.get("ARGUS_ADMIN_PASSWORD")
    if not password:
        print(
            "ARGUS_ADMIN_PASSWORD must be set before resetting the admin user.",
            file=sys.stderr,
        )
        return 1

    await init_db()

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(delete(User).where(User.username == "admin"))

        hashed_pw = get_password_hash(password)
        admin_user = User(
            username="admin",
            hashed_password=hashed_pw,
            role=UserRole.ADMIN.value,
            tenant_id="1",
            is_active=True
        )
        session.add(admin_user)
        await session.commit()
        print("Successfully reset admin user: admin")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
