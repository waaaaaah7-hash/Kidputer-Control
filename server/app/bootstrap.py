import logging

from sqlalchemy import func, select

from .audit import log_admin
from .config import Settings
from .db import SessionLocal
from .models import AdminUser
from .security import MIN_PASSWORD_LENGTH, hash_password

log = logging.getLogger(__name__)


def bootstrap_admin(settings: Settings) -> bool:
    """Create the first admin from KC_BOOTSTRAP_ADMIN_* if there are no admins yet. Returns True if created."""
    email, password = settings.bootstrap_admin_email.strip(), settings.bootstrap_admin_password
    if not email or not password:
        return False
    if len(password) < MIN_PASSWORD_LENGTH:
        log.error("KC_BOOTSTRAP_ADMIN_PASSWORD must be at least %d characters; no admin created", MIN_PASSWORD_LENGTH)
        return False
    with SessionLocal() as db:
        # Serialise concurrent workers starting at once.
        db.execute(select(func.pg_advisory_xact_lock(0x534D424F4F54)))
        if db.scalar(select(func.count()).select_from(AdminUser)):
            return False
        user = AdminUser(email=email, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        log_admin(db, None, "admin.bootstrapped", f"First admin {email} created from environment")
        db.commit()
    log.warning("Created first admin %s. Remove KC_BOOTSTRAP_ADMIN_PASSWORD from the environment now.", email)
    return True
