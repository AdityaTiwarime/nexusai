"""Roles and what they are allowed to do.

Roles are kept as a fixed, ordered set rather than a free-form permissions table:
the product has four well-defined personas, and a fixed set is easier to audit
and test. A permissions table can be introduced later without changing callers,
because endpoints depend on `require_roles(...)`, not on role names in SQL.
"""

from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"  # platform operator, belongs to no organization
    ORG_ADMIN = "org_admin"  # manages users and settings of one organization
    MANAGER = "manager"  # can see the organization's users, approves workflows (Phase 5)
    EMPLOYEE = "employee"  # regular user


ORG_ROLES: frozenset[Role] = frozenset({Role.ORG_ADMIN, Role.MANAGER, Role.EMPLOYEE})

# Seniority inside an organization. Used for "who may see this document" checks.
ROLE_RANK: dict[str, int] = {
    Role.EMPLOYEE.value: 1,
    Role.MANAGER.value: 2,
    Role.ORG_ADMIN.value: 3,
}


def rank(role: str) -> int:
    return ROLE_RANK.get(role, 0)
