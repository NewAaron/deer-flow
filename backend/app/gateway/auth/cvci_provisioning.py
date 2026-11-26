"""Provision CVCI users by signed account identity, independently of mailboxes."""

from fastapi import HTTPException, status

from app.gateway.auth.cvci import CVCI_PROVIDER, CvciGrant, cvci_subject
from app.gateway.auth.local_provider import LocalAuthProvider
from app.gateway.auth.models import User
from deerflow.config.auth_config import OIDCProviderConfig


def _require_usable(user: User) -> None:
    if user.disabled or user.needs_setup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled or requires setup. Please contact your administrator.",
        )


async def get_or_provision_cvci_user(
    grant: CvciGrant,
    provider_config: OIDCProviderConfig,
    local_provider: LocalAuthProvider,
) -> dict:
    """Resolve an already-validated CVCI grant without email-based account linking.

    The callback validates the signed audience and subject before passing the
    grant here. The email-shaped value is only a stable storage identifier for
    new rows; it asserts no mailbox ownership and is not an OIDC email claim.
    Existing rows retain their UUID, email, password and role unchanged.
    """
    subject = cvci_subject(grant.tenantId, grant.userId)
    existing = await local_provider.get_user_by_oauth(CVCI_PROVIDER, subject)
    if existing is not None:
        _require_usable(existing)
        return {"user": existing, "created": False}

    if not provider_config.auto_create_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Automatic account creation is disabled. Contact your administrator.",
        )

    # This reserved example.com namespace satisfies the existing EmailStr and
    # unique/non-null column contracts. Never use a claimed contact email or
    # admin-email mapping to identify or elevate a CVCI account.
    email = f"cvci-t{grant.tenantId}-u{grant.userId}@cvci.identity.example.com"
    try:
        user = await local_provider.create_oauth_user(
            email=email,
            oauth_provider=CVCI_PROVIDER,
            oauth_id=subject,
            system_role="user",
        )
    except ValueError:
        # Only the same provider/sub winner is reusable. An internal alias or
        # other uniqueness collision must never attach an unrelated account.
        existing = await local_provider.get_user_by_oauth(CVCI_PROVIDER, subject)
        if existing is not None:
            _require_usable(existing)
            return {"user": existing, "created": False}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The CVCI account could not be created. Please contact your administrator.",
        ) from None
    return {"user": user, "created": True}
