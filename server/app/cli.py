"""Admin bootstrap. There is deliberately no public sign-up endpoint.

    python -m app.cli create-admin you@example.com
    python -m app.cli reset-totp you@example.com
"""

import argparse
import getpass
import sys

from sqlalchemy import func, select, update

from .db import SessionLocal
from .models import AdminSession, AdminUser, RememberedDevice
from .security import MIN_PASSWORD_LENGTH, hash_password


def _read_password() -> str:
    pw = getpass.getpass("Password: ")
    if len(pw) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass.getpass("Repeat: ") != pw:
        sys.exit("Passwords do not match.")
    return pw


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-admin").add_argument("email")
    sub.add_parser("reset-totp", help="disable 2FA for an admin who lost their authenticator").add_argument("email")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(func.lower(AdminUser.email) == args.email.lower()))
        if args.cmd == "create-admin":
            if user is not None:
                sys.exit("An admin with that email already exists.")
            db.add(AdminUser(email=args.email, password_hash=hash_password(_read_password())))
            db.commit()
            print(f"Created admin {args.email}. Sign in and enable two-factor authentication.")
        elif args.cmd == "reset-totp":
            if user is None:
                sys.exit("No such admin.")
            user.totp_enabled = False
            user.totp_secret = None
            user.totp_last_counter = None
            db.execute(update(AdminSession).where(AdminSession.user_id == user.id).values(revoked_at=func.now()))
            # Remembered devices skip the code, so they must not outlive the old authenticator.
            db.execute(
                update(RememberedDevice)
                .where(RememberedDevice.user_id == user.id, RememberedDevice.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )
            db.commit()
            print(f"2FA reset for {args.email}; all their sessions were signed out and remembered devices forgotten.")


if __name__ == "__main__":
    main()
