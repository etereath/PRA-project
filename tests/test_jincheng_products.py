from decimal import Decimal

import pytest

from app.adapters.jincheng_products import (
    JinchengProductFrame,
    JinchengProductReadError,
    capture_jincheng_products,
    read_jincheng_products,
)


def _card(
    name,
    grade="B",
    *,
    badge="出售中",
    action="下架",
    qty="0",
    price=("¥", "11", ".50"),
    sold_out=False,
):
    return (
        "\ufffc",
        badge,
        *(("已售罄",) if sold_out else ()),
        "视频已过期",
        f"{name}｜【{grade}级】中熟(3度)、20支/扎、≥65",
        "销量：3",
        *price,
        "第一名价格￥4.50，第二名价格￥7.50",
        qty,
        "库存",
        action,
    )


def _frame(*cards, count=None, tail=True, verified=True, visible=None):
    return JinchengProductFrame(
        (
            "\ufffc",
            "商品管理",
            "请输入花材名称",
            f"全部({len(cards) if count is None else count})",
            "等级",
            "分类",
            "库存",
            "全部下架",
            *(token for card in cards for token in card),
            "已经到底了~" if tail else "上拉显示更多",
            "转入代卖提示",
            "下架",
        ),
        verified,
        tail if visible is None else visible,
    )


def test_cumulative_reader_keeps_sold_out_online_and_pending_activation_separate():
    online = _card(
        "样品甲",
        sold_out=True,
        price=("¥", "11", ".50", "结算价￥6.00", "平台售卖价￥7.00", "平台补贴￥1.00"),
    )
    pending = _card("样品乙", "O", badge="待激活", action="激活", qty="9")
    terminal = _frame(online, pending)

    class Port:
        def __init__(self):
            self.views = iter((_frame(online, count=2, tail=False), terminal, terminal))
            self.calls = []

        def open_all_products(self):
            self.calls.append("open")

        def observe_products(self):
            self.calls.append("observe")
            return next(self.views)

        def scroll_products(self):
            self.calls.append("scroll")

    port = Port()
    result = read_jincheng_products(port)
    assert port.calls == ["open", "observe", "scroll", "observe", "observe"]
    assert result.status == "COMPLETE"
    first, second = result.rows
    assert first.listing_state == "ONLINE" and first.sold_out_badge
    assert first.available_bunches == 0 and first.stems_per_bunch == 20
    assert first.display_price == Decimal("11.50")
    assert dict(first.named_prices) == {
        "平台售卖价": Decimal("7"),
        "平台补贴": Decimal("1"),
        "结算价": Decimal("6"),
    }
    assert (second.listing_state, second.grade, second.available_bunches) == (
        "PENDING_ACTIVATION",
        "O",
        9,
    )


@pytest.mark.parametrize("last_count", [33, 4, 0])
def test_stable_visible_tail_counts_actual_cards_despite_tab_numbers(last_count):
    cards = tuple(_card(f"样品{i}") for i in range(21))
    result = capture_jincheng_products(
        [
            _frame(*cards[:20], count=33, tail=False),
            _frame(*cards, count=33),
            _frame(*cards, count=last_count),
        ]
    )
    assert (result.status, result.reason) == ("COMPLETE", "")
    assert result.end_marker_verified and result.row_count == 21


@pytest.mark.parametrize(
    ("frames", "reason"),
    [
        ([_frame(_card("样品甲"), visible=False)] * 2, "PRODUCT_TAIL_UNCONFIRMED"),
        ([_frame(_card("样品甲"), count=33)], "PRODUCT_TAIL_UNSTABLE"),
        (
            [_frame(_card("样品甲"), _card("样品甲", qty="2"))],
            "PRODUCT_IDENTITY_AMBIGUOUS",
        ),
        (
            [
                _frame(_card("样品甲"), count=2, tail=False),
                _frame(_card("样品甲", qty="2"), _card("样品乙")),
            ],
            "PRODUCT_LIST_CHANGED",
        ),
    ],
)
def test_capture_refuses_unproven_coverage(frames, reason):
    result = capture_jincheng_products(frames)
    assert (result.status, result.reason) == ("PARTIAL", reason)


def test_unverified_scope_and_incomplete_cards_fail_without_producing_rows():
    with pytest.raises(JinchengProductReadError, match="PRODUCT_SCOPE_UNCONFIRMED"):
        capture_jincheng_products([_frame(_card("样品甲"), verified=False)])
    with pytest.raises(JinchengProductReadError, match="PRODUCT_CARD_INCOMPLETE"):
        capture_jincheng_products([_frame(_card("样品甲")[:-1])])
