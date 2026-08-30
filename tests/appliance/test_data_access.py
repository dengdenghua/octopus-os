"""Family data-path authorization derived from public OMV projections."""

from __future__ import annotations

import pytest

from appliance.data_access import (
    DataAccessDenied,
    DataAccessUnavailable,
    OmvDataAccessPolicy,
)


class _Accounts:
    def omv_username_for_actor(self, actor: str) -> str | None:
        return {
            "local:alice": "alice",
            "local:bob": "bob",
            "local:carol": "carol",
        }.get(actor)


class _Omv:
    def __init__(self) -> None:
        self.overview_calls = 0
        self.privileges = {
            "family": [
                {"type": "user", "id": 1001, "name": "alice", "permission": "inherit"},
                {"type": "group", "id": 100, "name": "users", "permission": "read"},
            ],
            "private": [
                {"type": "user", "id": 1001, "name": "alice", "permission": "none"},
                {
                    "type": "group",
                    "id": 101,
                    "name": "photographers",
                    "permission": "readWrite",
                },
            ],
            "dropbox": [
                {"type": "user", "id": 1001, "name": "alice", "permission": "readWrite"},
            ],
        }

    def sharing_overview(self) -> dict:
        self.overview_calls += 1
        return {
            "users": [
                {"name": "alice", "groups": ["users", "photographers"]},
                {"name": "bob", "groups": ["users"]},
            ],
            "sharedFolders": [
                {"uuid": "family", "relativePath": "Family/", "status": "OK"},
                {"uuid": "private", "relativePath": "Family/Private/", "status": "OK"},
                {"uuid": "dropbox", "relativePath": "Drop Box/", "status": "OK"},
            ],
        }

    def share_privileges(self, share_uuid: str) -> list[dict]:
        return self.privileges[share_uuid]


def test_admin_is_unrestricted_without_querying_omv() -> None:
    omv = _Omv()
    scope = OmvDataAccessPolicy(accounts=_Accounts(), omv=omv).scope_for_actor("local:admin")

    assert scope.can_read("anything/private.txt")
    assert scope.can_write("anything/private.txt")
    assert scope.operator is True
    assert omv.overview_calls == 0


def test_member_uses_direct_user_precedence_group_inheritance_and_nested_denial() -> None:
    scope = OmvDataAccessPolicy(accounts=_Accounts(), omv=_Omv()).scope_for_actor("local:alice")

    assert scope.can_list("")
    assert scope.visible("Family")
    assert scope.can_read("Family/readme.txt")
    assert not scope.can_write("Family/readme.txt")
    assert not scope.can_read("Family/Private/secret.jpg")
    assert scope.can_write("Drop Box/new.txt")
    assert not scope.visible("Unshared")


def test_unknown_actor_and_removed_omv_member_fail_closed() -> None:
    policy = OmvDataAccessPolicy(accounts=_Accounts(), omv=_Omv())

    with pytest.raises(DataAccessDenied):
        policy.scope_for_actor("local:charlie")

    with pytest.raises(DataAccessDenied):
        policy.scope_for_actor("local:carol")


def test_ambiguous_roots_and_invalid_projection_fail_closed() -> None:
    omv = _Omv()
    omv.sharing_overview = lambda: {
        "users": [{"name": "alice", "groups": ["users"]}],
        "sharedFolders": [
            {"uuid": "family", "relativePath": "Family", "status": "OK"},
            {"uuid": "dropbox", "relativePath": "Family/", "status": "OK"},
        ],
    }

    with pytest.raises(DataAccessUnavailable):
        OmvDataAccessPolicy(accounts=_Accounts(), omv=omv).scope_for_actor("local:alice")


def test_member_scope_is_briefly_cached_but_never_shared_between_actors() -> None:
    omv = _Omv()
    policy = OmvDataAccessPolicy(accounts=_Accounts(), omv=omv, cache_seconds=5)

    first = policy.scope_for_actor("local:alice")
    second = policy.scope_for_actor("local:alice")

    assert first is second
    assert omv.overview_calls == 1


def test_single_mounted_share_becomes_virtual_root_and_keeps_nested_denials(
    tmp_path,
) -> None:
    base_uuid = "11111111-2222-4333-8444-555555555555"
    private_uuid = "22222222-3333-4444-8555-666666666666"
    sibling_uuid = "33333333-4444-4555-8666-777777777777"
    (tmp_path / "Private").mkdir()
    calls: list[str] = []

    class _MountedOmv:
        def sharing_overview(self):
            return {
                "users": [{"name": "alice", "groups": ["users"]}],
                "sharedFolders": [
                    {
                        "uuid": base_uuid,
                        "relativePath": "Pool/Family/",
                        "status": "OK",
                    },
                    {
                        "uuid": private_uuid,
                        "relativePath": "Pool/Family/Private/",
                        "status": "OK",
                    },
                    {
                        "uuid": sibling_uuid,
                        "relativePath": "Pool/Sibling/",
                        "status": "OK",
                    },
                ],
            }

        def share_privileges(self, share_uuid):
            calls.append(share_uuid)
            permission = "none" if share_uuid == private_uuid else "readWrite"
            return [
                {
                    "type": "user",
                    "id": 1001,
                    "name": "alice",
                    "permission": permission,
                }
            ]

    scope = OmvDataAccessPolicy(
        accounts=_Accounts(),
        omv=_MountedOmv(),
        root=tmp_path,
        mounted_share_uuid=base_uuid,
    ).scope_for_actor("local:alice")

    assert scope.can_read("photo.jpg")
    assert scope.can_write("new/photo.jpg")
    assert not scope.can_read("Private/secret.jpg")
    assert calls == [base_uuid, private_uuid]


def test_granted_omv_share_must_exist_under_the_mounted_nas_root(tmp_path) -> None:
    with pytest.raises(DataAccessUnavailable, match="not available"):
        OmvDataAccessPolicy(
            accounts=_Accounts(),
            omv=_Omv(),
            root=tmp_path,
        ).scope_for_actor("local:alice")


def test_production_root_rejects_nested_symlink_alias_and_missing_grant(
    tmp_path,
) -> None:
    root = tmp_path / "nas"
    (root / "Family").mkdir(parents=True)
    (root / "Family" / "Private").mkdir()
    (root / "Drop Box").mkdir()
    (root / "Unshared").mkdir()
    (root / "Unshared" / "secret.txt").write_text("secret")
    (root / "Family" / "linked").symlink_to(root / "Unshared", target_is_directory=True)

    scope = OmvDataAccessPolicy(
        accounts=_Accounts(),
        omv=_Omv(),
        root=root,
    ).scope_for_actor("local:alice")

    assert scope.can_read("Family/readme.txt")
    assert not scope.can_read("Family/linked/secret.txt")
    assert not scope.visible("Unshared")

    (root / "Drop Box").rmdir()
    with pytest.raises(DataAccessUnavailable, match="mounted NAS root"):
        OmvDataAccessPolicy(
            accounts=_Accounts(),
            omv=_Omv(),
            root=root,
        ).scope_for_actor("local:alice")


def test_default_policy_rechecks_omv_on_every_request() -> None:
    omv = _Omv()
    policy = OmvDataAccessPolicy(accounts=_Accounts(), omv=omv)

    first = policy.scope_for_actor("local:alice")
    omv.privileges["dropbox"][0]["permission"] = "none"
    second = policy.scope_for_actor("local:alice")

    assert first.can_write("Drop Box/new.txt")
    assert not second.can_read("Drop Box/new.txt")
    assert omv.overview_calls == 2
