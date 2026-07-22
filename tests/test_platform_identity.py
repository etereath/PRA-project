from __future__ import annotations

from app.platform_identity import canonical_platform_name, platform_names_match


def test_ant_platform_aliases_share_one_canonical_identity() -> None:
    assert canonical_platform_name("蚂蚁") == "蚂蚁花团供应商"
    assert canonical_platform_name("MAYI_HUATUAN_SUPPLIER") == "蚂蚁花团供应商"
    assert canonical_platform_name("ant_flower_wechat") == "蚂蚁花团供应商"
    assert platform_names_match("蚂蚁", "蚂蚁花团供应商")
