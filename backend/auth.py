from fastapi import Header, HTTPException, status

ROLE_MOSPI_REVIEWER = "MoSPI Reviewer"
ROLE_DISTRICT_AUDITOR = "District Authority Auditor"
ROLE_PUBLIC_TIER = "Read-Only Public Tier"

VALID_ROLES = {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR, ROLE_PUBLIC_TIER}

# Fail-closed: any missing/unknown/malformed role header degrades to the
# read-only public tier. Elevated access must be explicitly asserted.
DEFAULT_ROLE = ROLE_PUBLIC_TIER


def get_current_role(
    x_user_role: str = Header(
        default=DEFAULT_ROLE,
        description="Active user role for RBAC simulation: 'MoSPI Reviewer', 'District Authority Auditor', 'Read-Only Public Tier'"
    )
) -> str:
    role = x_user_role.strip() if x_user_role else ""
    if role not in VALID_ROLES:
        return DEFAULT_ROLE
    return role


def require_reviewer_role(
    x_user_role: str = Header(default=DEFAULT_ROLE)
) -> str:
    role = x_user_role.strip() if x_user_role else ""
    if role not in {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Read-Only Public Tier cannot record or alter human review outcomes."
        )
    return role


def require_mospi_admin_role(
    x_user_role: str = Header(default=DEFAULT_ROLE)
) -> str:
    role = x_user_role.strip() if x_user_role else ""
    if role != ROLE_MOSPI_REVIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Only MoSPI Reviewers can perform governance and sync operations."
        )
    return role
