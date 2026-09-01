"""NAS 管控面:存储探测、共享管理与 HTTP API。

全部用例不碰真实磁盘,外部命令用假 runner 替换 —— 目标是能在开发机上
``pytest tests/appliance/test_nas.py`` 全绿。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.nas import ShareManager, StorageInspector, create_nas_router
from appliance.nas.shares import Share, ShareError

_LSBLK = {
    "blockdevices": [
        {
            "name": "sda",
            "size": "1000204860416",
            "type": "disk",
            "model": "Samsung SSD 870",
            "serial": "S3Z8NX0M123456A",
            "mountpoint": None,
            "fstype": None,
            "uuid": None,
            "rm": "0",
            "children": [
                {
                    "name": "sda1",
                    "size": "536870912",
                    "type": "part",
                    "model": None,
                    "serial": None,
                    "mountpoint": "/boot/efi",
                    "fstype": "vfat",
                    "uuid": "ABCD-1234",
                    "rm": "0",
                }
            ],
        },
        {
            "name": "nvme0n1",
            "size": "512110190592",
            "type": "disk",
            "model": "WD SN550",
            "serial": "",
            "mountpoint": None,
            "fstype": None,
            "uuid": None,
            "rm": "0",
        },
    ]
}

_ZPOOL = (
    "tank\tONLINE\t4000000000000\t1200000000000\t2800000000000\n"
    "backup\tDEGRADED\t2000000000000\t900000000000\t1100000000000\n"
)

_ZFS = (
    "tank\t1000000000000\t2800000000000\t/tank\n"
    "tank/media\t800000000000\t2800000000000\t/tank/media\n"
    "backup\t500000000000\t1100000000000\t/backup\n"
)

_SMART = json.dumps(
    {
        "smart_status": {"passed": True},
        "model_name": "Samsung SSD 870",
        "serial_number": "S3Z8NX0M123456A",
        "temperature": {"current": 34},
        "power_on_time": {"hours": 4127},
    }
)


def _fake_runner(outputs: dict[str, tuple[int, str, str]]):
    def run(cmd: list[str]) -> tuple[int, str, str]:
        return outputs.get(cmd[0], (127, "", f"{cmd[0]}: not installed"))

    return run


class TestStorageInspector:
    def test_list_disks_parses_lsblk(self):
        insp = StorageInspector(_fake_runner({"lsblk": (0, json.dumps(_LSBLK), "")}))
        disks = insp.list_disks()
        assert len(disks) == 2
        sda = disks[0]
        assert sda.name == "sda"
        assert sda.path == "/dev/sda"
        assert sda.size == 1000204860416
        assert sda.model == "Samsung SSD 870"
        assert sda.removable is False
        assert len(sda.partitions) == 1
        assert sda.partitions[0].mountpoint == "/boot/efi"
        # 空序列号要有个兜底值,前端表格才能正常渲染
        assert disks[1].serial == "unknown"

    def test_list_disks_degrades_when_lsblk_missing(self):
        insp = StorageInspector(_fake_runner({}))
        assert insp.list_disks() == []

    def test_list_disks_degrades_on_malformed_json(self):
        insp = StorageInspector(_fake_runner({"lsblk": (0, "{not json", "")}))
        assert insp.list_disks() == []

    def test_list_pools_groups_datasets(self):
        insp = StorageInspector(
            _fake_runner({"zpool": (0, _ZPOOL, ""), "zfs": (0, _ZFS, "")})
        )
        pools = insp.list_pools()
        assert [p.name for p in pools] == ["tank", "backup"]
        assert pools[0].health == "ONLINE"
        assert pools[1].health == "DEGRADED"
        assert [d.name for d in pools[0].datasets] == ["tank", "tank/media"]
        assert pools[0].datasets[1].mountpoint == "/tank/media"

    def test_list_pools_degrades_without_zfs(self):
        insp = StorageInspector(_fake_runner({}))
        assert insp.list_pools() == []

    def test_capabilities_reports_missing_tools(self):
        insp = StorageInspector(_fake_runner({}))
        caps = insp.capabilities()
        assert set(caps) == {"zfs", "mdadm", "smart", "lvm"}

    def test_smart_health_parses_json(self):
        insp = StorageInspector(_fake_runner({"smartctl": (0, _SMART, "")}))
        h = insp.smart_health("sda")
        assert h["available"] is True
        assert h["passed"] is True
        assert h["temperature"] == 34
        assert h["powerOnHours"] == 4127

    def test_smart_health_degrades_when_not_installed(self):
        insp = StorageInspector(_fake_runner({}))
        h = insp.smart_health("sda")
        assert h["available"] is False
        assert "not installed" in h["error"]

    @pytest.mark.parametrize("bad", ["../../etc/passwd", "sda; rm -rf /", "loop0", ""])
    def test_smart_health_rejects_unsafe_device(self, bad):
        """设备名白名单:挡掉路径穿越与命令注入。"""
        insp = StorageInspector(_fake_runner({"smartctl": (0, _SMART, "")}))
        with pytest.raises(ValueError):
            insp.smart_health(bad)

    def test_smart_health_falls_back_to_raw_text(self):
        insp = StorageInspector(_fake_runner({"smartctl": (0, "not json at all", "")}))
        h = insp.smart_health("sda")
        assert h["available"] is True
        assert "raw" in h


class TestShareManager:
    def test_add_list_remove_roundtrip(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="media", path="/data/media"))
        mgr.add_share(Share(name="backup", path="/data/backup", read_only=True))
        assert {s.name for s in mgr.list_shares()} == {"media", "backup"}

        assert mgr.get_share("media").read_only is False
        assert mgr.remove_share("media") is True
        assert mgr.remove_share("media") is False  # 重复删除返回 False
        assert mgr.get_share("media") is None

    def test_upsert_replaces_same_name(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="media", path="/data/media"))
        mgr.add_share(Share(name="media", path="/data/media2", read_only=True))
        shares = mgr.list_shares()
        assert len(shares) == 1
        assert shares[0].path.endswith("media2")
        assert shares[0].read_only is True

    @pytest.mark.parametrize(
        "bad", ["with space", "a/b", "sec]\n[evil", "", "x" * 65]
    )
    def test_rejects_unsafe_share_name(self, tmp_path, bad):
        """共享名非法必须挡住 —— 换行注入可以凭空造一个可写共享。"""
        mgr = ShareManager(tmp_path)
        with pytest.raises(ShareError):
            mgr.add_share(Share(name=bad, path="/data/x"))

    @pytest.mark.parametrize(
        "bad", ["/etc", "/etc/passwd", "/", "/tmp/../etc", "/var/lib"]
    )
    def test_rejects_path_outside_allowed_roots(self, tmp_path, bad):
        mgr = ShareManager(tmp_path)
        with pytest.raises(ShareError):
            mgr.add_share(Share(name="x", path=bad))

    def test_allowed_roots_configurable(self, tmp_path):
        mgr = ShareManager(tmp_path, allowed_roots=("/srv",))
        mgr.add_share(Share(name="s", path="/srv/pub"))  # 不抛
        with pytest.raises(ShareError):
            mgr.add_share(Share(name="d", path="/data/x"))

    def test_render_smb_conf(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(
            Share(name="media", path="/data/media", comment="影音", guest_ok=True)
        )
        mgr.add_share(Share(name="ro", path="/data/ro", read_only=True))
        conf = mgr.render_smb_conf()
        assert "[media]" in conf
        assert "path = /data/media" in conf
        assert "guest ok = yes" in conf
        assert "read only = yes" in conf
        # NFS-only 的共享不该出现在 SMB 配置里
        mgr.add_share(Share(name="n", path="/data/n", protocols=["nfs"]))
        assert "[n]" not in mgr.render_smb_conf()

    def test_render_smb_escapes_newline_injection(self, tmp_path):
        """注入测试:注释里塞换行 + 伪造段头,必须无法凭空造出可写共享。

        不变量用解析器验证 —— 这比"数字符串出现次数"可靠得多。
        """
        import configparser

        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="x", path="/data/x", comment="a\n[evil]\n   path = /"))
        conf = mgr.render_smb_conf()

        # 源码层面就看不到结构字符
        assert "[evil]" not in conf

        # 解析层面:只有合法的 [x] 一个段,且它的 path 仍是 /data/x
        parser = configparser.ConfigParser(strict=True)
        parser.read_string(conf)
        assert parser.sections() == ["x"]
        assert parser["x"]["path"] == "/data/x"

    def test_render_nfs_exports(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="ro", path="/data/ro", protocols=["nfs"], read_only=True))
        mgr.add_share(
            Share(name="pub", path="/data/pub", protocols=["nfs"], guest_ok=True)
        )
        exports = mgr.render_nfs_exports()
        assert "/data/ro *(fsid=0,ro,sync,no_subtree_check)" in exports
        assert "all_squash" in exports

    def test_nfs_escapes_spaces_in_path(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="a", path="/data/my data", protocols=["nfs"]))
        assert "\\040" in mgr.render_nfs_exports()

    def test_apply_writes_atomically_and_keeps_backup(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="media", path="/data/media"))
        result = mgr.apply()

        smb = tmp_path / "smb" / "echo.conf"
        exports = tmp_path / "exports.d" / "echo.exports"
        assert smb.exists() and exports.exists()
        assert result["shares"] == 1
        # 工具不存在时 reload 失败但不抛,错误被记录下来
        assert result["reload"]["smb"]["ok"] is False

        # 第二次 apply 应留下 .bak
        mgr.apply()
        assert smb.with_suffix(".conf.bak").exists()
        # tmp 文件不应残留
        assert not smb.with_suffix(".conf.tmp").exists()

    def test_reload_reports_success(self, tmp_path):
        mgr = ShareManager(
            tmp_path,
            runner=_fake_runner(
                {"smbcontrol": (0, "", ""), "exportfs": (0, "", "")}
            ),
        )
        result = mgr.apply()
        assert result["reload"]["smb"]["ok"] is True
        assert result["reload"]["smb"]["method"] == "smbcontrol"
        assert result["reload"]["nfs"]["ok"] is True

    def test_shares_json_is_single_source_of_truth(self, tmp_path):
        """清单是唯一真源:新 manager 实例读同一目录应得到同样结果。"""
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="media", path="/data/media"))
        assert {s.name for s in ShareManager(tmp_path).list_shares()} == {"media"}

    def test_corrupted_manifest_degrades_gracefully(self, tmp_path):
        (tmp_path / "shares.json").write_text("{not json", encoding="utf-8")
        assert ShareManager(tmp_path).list_shares() == []


def _client(inspector=None, shares=None, secret=None) -> TestClient:
    app = FastAPI()
    app.include_router(create_nas_router(inspector, shares, jwt_secret=secret))
    return TestClient(app)


class TestNasRouter:
    def test_status_reports_capabilities(self, tmp_path):
        r = _client(shares=ShareManager(tmp_path)).get("/api/appliance/nas/status")
        assert r.status_code == 200
        body = r.json()
        assert "zfs" in body["capabilities"]
        assert body["shares"] == 0

    def test_disks_endpoint(self):
        insp = StorageInspector(_fake_runner({"lsblk": (0, json.dumps(_LSBLK), "")}))
        r = _client(inspector=insp).get("/api/appliance/nas/disks")
        assert r.status_code == 200
        assert [d["name"] for d in r.json()["disks"]] == ["sda", "nvme0n1"]

    def test_pools_endpoint(self):
        insp = StorageInspector(
            _fake_runner({"zpool": (0, _ZPOOL, ""), "zfs": (0, _ZFS, "")})
        )
        r = _client(inspector=insp).get("/api/appliance/nas/pools")
        assert r.status_code == 200
        assert r.json()["pools"][0]["health"] == "ONLINE"

    def test_smart_endpoint_rejects_bad_device(self):
        # 注意:URL 里的 ../ 会被 HTTP 客户端/服务端提前规范化,打不到路由上,
        # 因此这里用能通过 URL 的非法设备名(loop 设备不在白名单内)。
        insp = StorageInspector(_fake_runner({"smartctl": (0, _SMART, "")}))
        for bad in ("loop0", "sda;rm", "mmcblk0"):
            r = _client(inspector=insp).get(f"/api/appliance/nas/smart/{bad}")
            assert r.status_code == 422, f"{bad} should be rejected"

    def test_share_crud_over_http(self, tmp_path):
        c = _client(shares=ShareManager(tmp_path))
        assert (
            c.post(
                "/api/appliance/nas/shares",
                json={"name": "media", "path": "/data/media"},
            ).status_code
            == 200
        )
        assert c.get("/api/appliance/nas/shares").json()["shares"][0]["name"] == "media"
        # 非法路径 → 422
        assert (
            c.post(
                "/api/appliance/nas/shares",
                json={"name": "evil", "path": "/etc"},
            ).status_code
            == 422
        )
        assert c.delete("/api/appliance/nas/shares/media").status_code == 200
        assert c.delete("/api/appliance/nas/shares/media").status_code == 404

    def test_apply_endpoint_runs(self, tmp_path):
        mgr = ShareManager(tmp_path)
        mgr.add_share(Share(name="media", path="/data/media"))
        r = _client(shares=mgr).post("/api/appliance/nas/shares/apply")
        assert r.status_code == 200
        assert r.json()["shares"] == 1
        assert (tmp_path / "smb" / "echo.conf").exists()

    def test_jwt_secret_protects_writes(self, tmp_path):
        """带 secret 时未认证请求应被拦下。"""
        c = _client(shares=ShareManager(tmp_path), secret="s3cret")
        assert (
            c.post(
                "/api/appliance/nas/shares",
                json={"name": "media", "path": "/data/media"},
            ).status_code
            == 401
        )
        # 公开端点不受影响
        assert c.get("/api/appliance/nas/status").status_code == 200
