"""Role-based access control and tenant isolation.

The most important property of a multi-tenant platform: users of one organization
can never read or change another organization's data, even if they know its IDs.
"""

import pytest

from app.cli import create_superadmin
from tests.conftest import API, PASSWORD, add_user, bearer, login, make_settings, register_org


@pytest.fixture
async def two_orgs(client):
    acme = await register_org(client, org="Acme Corp", email="admin@acme.com")
    globex = await register_org(client, org="Globex", email="admin@globex.com")
    acme_emp = await add_user(client, acme, "emp@acme.com")
    globex_emp = await add_user(client, globex, "emp@globex.com")
    return {"acme": acme, "globex": globex, "acme_emp": acme_emp, "globex_emp": globex_emp}


# --- tenant isolation -----------------------------------------------------------------


async def test_user_list_only_shows_own_organization(client, two_orgs):
    users = (await client.get(f"{API}/users", headers=bearer(two_orgs["acme"].token))).json()
    emails = {u["email"] for u in users}
    assert emails == {"admin@acme.com", "emp@acme.com"}
    assert all(u["organization_id"] == two_orgs["acme"].organization_id for u in users)


async def test_cannot_read_another_orgs_user_by_id(client, two_orgs):
    response = await client.get(
        f"{API}/users/{two_orgs['globex_emp'].user_id}", headers=bearer(two_orgs["acme"].token)
    )
    # 404, not 403: don't even confirm that the ID exists.
    assert response.status_code == 404


async def test_cannot_modify_another_orgs_user(client, two_orgs):
    target = two_orgs["globex_emp"]
    response = await client.patch(
        f"{API}/users/{target.user_id}",
        headers=bearer(two_orgs["acme"].token),
        json={"role": "org_admin", "is_active": False},
    )
    assert response.status_code == 404
    # The Globex user is untouched and can still sign in.
    assert (await login(client, target.email)).token


async def test_audit_logs_are_scoped_to_the_organization(client, two_orgs):
    for name in ("acme", "globex"):
        account = two_orgs[name]
        logs = (await client.get(f"{API}/audit-logs", headers=bearer(account.token))).json()
        assert logs["total"] > 0
        other = "globex" if name == "acme" else "acme"
        assert all(other not in (item["actor_email"] or "") for item in logs["items"])
        assert all(other not in str(item["details"]) for item in logs["items"])


async def test_current_organization_is_the_callers(client, two_orgs):
    org = (
        await client.get(f"{API}/organizations/current", headers=bearer(two_orgs["globex"].token))
    ).json()
    assert org["id"] == two_orgs["globex"].organization_id and org["name"] == "Globex"


async def test_org_id_in_request_body_is_ignored(client, two_orgs):
    """A user created by Acme's admin always lands in Acme, whatever the body says."""
    response = await client.post(
        f"{API}/users",
        headers=bearer(two_orgs["acme"].token),
        json={
            "email": "sneaky@acme.com",
            "full_name": "Sneaky",
            "password": PASSWORD,
            "role": "employee",
            "organization_id": two_orgs["globex"].organization_id,
        },
    )
    assert response.status_code == 201
    assert response.json()["organization_id"] == two_orgs["acme"].organization_id


# --- role-based access control ----------------------------------------------------------


async def test_employee_permissions(client, two_orgs):
    emp = bearer(two_orgs["acme_emp"].token)
    assert (await client.get(f"{API}/auth/me", headers=emp)).status_code == 200
    assert (await client.get(f"{API}/organizations/current", headers=emp)).status_code == 200
    assert (await client.get(f"{API}/users", headers=emp)).status_code == 403
    assert (await client.get(f"{API}/audit-logs", headers=emp)).status_code == 403
    create = await client.post(
        f"{API}/users",
        headers=emp,
        json={"email": "x@acme.com", "full_name": "X", "password": PASSWORD},
    )
    assert create.status_code == 403


async def test_manager_can_view_but_not_manage_users(client, two_orgs):
    manager = await add_user(client, two_orgs["acme"], "mgr@acme.com", role="manager")
    headers = bearer(manager.token)
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/audit-logs", headers=headers)).status_code == 403
    patch = await client.patch(
        f"{API}/users/{two_orgs['acme_emp'].user_id}", headers=headers, json={"role": "manager"}
    )
    assert patch.status_code == 403


async def test_role_change_takes_effect_immediately(client, two_orgs):
    emp = two_orgs["acme_emp"]
    assert (await client.get(f"{API}/users", headers=bearer(emp.token))).status_code == 403
    await client.patch(
        f"{API}/users/{emp.user_id}",
        headers=bearer(two_orgs["acme"].token),
        json={"role": "manager"},
    )
    # Same access token as before: the role is re-read from the database on every request.
    assert (await client.get(f"{API}/users", headers=bearer(emp.token))).status_code == 200


async def test_deactivated_user_is_locked_out_immediately(client, two_orgs):
    emp = two_orgs["acme_emp"]
    response = await client.patch(
        f"{API}/users/{emp.user_id}",
        headers=bearer(two_orgs["acme"].token),
        json={"is_active": False},
    )
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert (await client.get(f"{API}/auth/me", headers=bearer(emp.token))).status_code == 401
    bad = await client.post(f"{API}/auth/login", json={"email": emp.email, "password": PASSWORD})
    assert bad.status_code == 401


async def test_admin_cannot_demote_or_disable_themselves(client, two_orgs):
    admin = two_orgs["acme"]
    for body in ({"role": "employee"}, {"is_active": False}):
        response = await client.patch(
            f"{API}/users/{admin.user_id}", headers=bearer(admin.token), json=body
        )
        assert response.status_code == 400


async def test_super_admin_role_cannot_be_assigned_via_api(client, two_orgs):
    admin = bearer(two_orgs["acme"].token)
    create = await client.post(
        f"{API}/users",
        headers=admin,
        json={
            "email": "boss@acme.com",
            "full_name": "B",
            "password": PASSWORD,
            "role": "super_admin",
        },
    )
    assert create.status_code == 422
    patch = await client.patch(
        f"{API}/users/{two_orgs['acme_emp'].user_id}", headers=admin, json={"role": "super_admin"}
    )
    assert patch.status_code == 422


async def test_user_changes_are_audited(client, two_orgs):
    admin = two_orgs["acme"]
    await client.patch(
        f"{API}/users/{two_orgs['acme_emp'].user_id}",
        headers=bearer(admin.token),
        json={"role": "manager"},
    )
    logs = (
        await client.get(
            f"{API}/audit-logs", params={"action": "user.updated"}, headers=bearer(admin.token)
        )
    ).json()
    assert logs["total"] == 1
    entry = logs["items"][0]
    assert entry["actor_email"] == "admin@acme.com"
    assert entry["details"] == {"role": {"from": "employee", "to": "manager"}}
    assert entry["request_id"]


# --- super admin ----------------------------------------------------------------------


async def test_super_admin_sees_all_orgs_but_no_tenant_data(client, two_orgs):
    await create_superadmin(make_settings(), "ops@nexusai.io", "Ops", PASSWORD)
    ops = await login(client, "ops@nexusai.io")
    assert ops.organization_id is None

    orgs = (await client.get(f"{API}/admin/organizations", headers=bearer(ops.token))).json()
    assert {o["name"] for o in orgs} == {"Acme Corp", "Globex"}
    # Platform operator, not a member of any tenant: no access to tenant user lists.
    assert (await client.get(f"{API}/users", headers=bearer(ops.token))).status_code == 403


async def test_org_admin_cannot_use_platform_admin_endpoints(client, two_orgs):
    response = await client.get(
        f"{API}/admin/organizations", headers=bearer(two_orgs["acme"].token)
    )
    assert response.status_code == 403


async def test_create_superadmin_rejects_short_password():
    with pytest.raises(ValueError):
        await create_superadmin(make_settings(), "ops2@nexusai.io", "Ops", "short")
