"""Read-only capture contract for the Jincheng cumulative product list."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol


_ALL_TAB = re.compile(r"全部(?:\([0-9]+\))?")
_TITLE = re.compile(
    r"(?P<name>[^｜|]+)[｜|]\s*【(?P<grade>[A-Z](?:\+)?)级】\s*(?P<spec>.+)"
)
_PACK = re.compile(r"([1-9][0-9]*)\s*[支枝]/扎")
_MONEY = r"[0-9]+(?:\.[0-9]{1,2})?"
_PRICE = re.compile(r"[¥￥]\s*(" + _MONEY + r")")
_NAMED_PRICE = re.compile(
    r"(平台补贴|平台售卖价|结算价|供货价)\s*[:：]?\s*[¥￥]?\s*("
    + _MONEY
    + r")(?:\s*元?/扎)?"
)
_ACTION_STATE = {"下架": "ONLINE", "上架": "OFFLINE", "激活": "PENDING_ACTIVATION"}
_STATE_BADGE = {"ONLINE": "出售中", "OFFLINE": "已下架", "PENDING_ACTIVATION": "待激活"}
_END = frozenset({"已经到底了~", "已经到底了～", "已经到底了"})
_AFTER_LIST = _END | {"上拉显示更多", "加载中", "加载中...", "转入代卖提示"}


class JinchengProductReadError(ValueError):
    """The frame did not contain a supported, unambiguous product layout."""


@dataclass(frozen=True, slots=True)
class JinchengProduct:
    name: str
    grade: str
    specification: str
    stems_per_bunch: int
    listing_state: str
    sold_out_badge: bool
    available_bunches: int
    display_price: Decimal
    named_prices: tuple[tuple[str, Decimal], ...]

    @property
    def display_key(self) -> tuple[str, str]:
        return self.name, self.grade


@dataclass(frozen=True, slots=True)
class JinchengProductFrame:
    """One active document and scope/visibility checks made by its reader.

    Document text alone cannot prove selection, filters, or footer visibility.
    """

    tokens: tuple[str, ...]
    full_scope_verified: bool
    footer_visible: bool


@dataclass(frozen=True, slots=True)
class JinchengProductCapture:
    status: Literal["COMPLETE", "PARTIAL"]
    reason: str
    end_marker_verified: bool
    rows: tuple[JinchengProduct, ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class JinchengProductPort(Protocol):
    """Runs in the shared, bounded Windows navigation process."""

    def open_all_products(self) -> None:
        """Open the product list with no search, grade or category filter."""
        ...

    def observe_products(self) -> JinchengProductFrame:
        """Read the active scope and document after navigation/loading settle."""
        ...

    def scroll_products(self) -> None:
        """Scroll the confirmed list region only."""
        ...


def _prices(
    tokens: tuple[str, ...],
) -> tuple[Decimal, tuple[tuple[str, Decimal], ...]]:
    display = []
    named = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        # Some providers expose ¥, the integer and the decimal as distinct nodes.
        if token in {"¥", "￥"} and i < len(tokens):
            token += tokens[i]
            i += 1
        if _PRICE.fullmatch(token) and i < len(tokens):
            if re.fullmatch(r"\.[0-9]{1,2}", tokens[i]) and "." not in token:
                token += tokens[i]
                i += 1
        if match := _PRICE.fullmatch(token):
            display.append(Decimal(match[1]))
        elif match := _NAMED_PRICE.fullmatch(token):
            if match[1] in named:
                raise JinchengProductReadError("PRICE_LABEL_AMBIGUOUS")
            named[match[1]] = Decimal(match[2])
        elif token.startswith(("平台补贴", "平台售卖价", "结算价", "供货价")):
            raise JinchengProductReadError("PRICE_LABEL_UNSUPPORTED")
    if len(display) != 1:
        raise JinchengProductReadError("DISPLAY_PRICE_UNCONFIRMED")
    return display[0], tuple(sorted(named.items()))


def _parse_frame(
    frame: JinchengProductFrame,
) -> tuple[bool, tuple[JinchengProduct, ...]]:
    if not frame.full_scope_verified:
        raise JinchengProductReadError("PRODUCT_SCOPE_UNCONFIRMED")
    tokens = tuple(
        token
        for value in frame.tokens
        if (token := value.replace("\ufffc", "").strip())
    )
    if not tokens or tokens[0] != "商品管理":
        raise JinchengProductReadError("PRODUCT_DOCUMENT_UNCONFIRMED")
    if tokens.count("全部下架") != 1:
        raise JinchengProductReadError("PRODUCT_LIST_UNCONFIRMED")
    start = tokens.index("全部下架")
    if sum(bool(_ALL_TAB.fullmatch(token)) for token in tokens[:start]) != 1:
        raise JinchengProductReadError("PRODUCT_LIST_UNCONFIRMED")
    list_tokens = tokens[start + 1 :]
    stop = next(
        (i for i, token in enumerate(list_tokens) if token in _AFTER_LIST),
        len(list_tokens),
    )
    marker = stop < len(list_tokens) and list_tokens[stop] in _END
    list_tokens = list_tokens[:stop]
    titles = [
        (i, m) for i, token in enumerate(list_tokens) if (m := _TITLE.fullmatch(token))
    ]
    rows = []
    prefix_start = 0
    for n, (index, title) in enumerate(titles):
        next_title = titles[n + 1][0] if n + 1 < len(titles) else len(list_tokens)
        card = list_tokens[index + 1 : next_title]
        actions = [
            (i, _ACTION_STATE[token])
            for i, token in enumerate(card)
            if token in _ACTION_STATE
        ]
        stocks = [
            (i, int(card[i - 1]))
            for i, token in enumerate(card)
            if token == "库存" and i and re.fullmatch(r"[0-9]+", card[i - 1])
        ]
        if len(stocks) != 1 or len(actions) != 1 or stocks[0][0] >= actions[0][0]:
            raise JinchengProductReadError("PRODUCT_CARD_INCOMPLETE")
        prefix = list_tokens[prefix_start:index]
        state = actions[0][1]
        badges = set(prefix).intersection(_STATE_BADGE.values())
        if badges != {_STATE_BADGE[state]}:
            raise JinchengProductReadError("LISTING_STATE_UNCONFIRMED")
        spec = title["spec"].strip()
        packs = _PACK.findall(spec)
        if len(packs) != 1:
            raise JinchengProductReadError("PRODUCT_SPEC_UNCONFIRMED")
        price, named = _prices(card[: stocks[0][0] - 1])
        rows.append(
            JinchengProduct(
                title["name"].strip(),
                title["grade"],
                spec,
                int(packs[0]),
                state,
                "已售罄" in prefix,
                stocks[0][1],
                price,
                named,
            )
        )
        prefix_start = index + actions[0][0] + 2
    if not rows:
        # No stable, explicit empty-state layout has been qualified for this page.
        raise JinchengProductReadError("PRODUCT_ROWS_UNCONFIRMED")
    return marker and frame.footer_visible, tuple(rows)


def capture_jincheng_products(
    frames: Iterable[JinchengProductFrame], *, max_frames: int = 40
) -> JinchengProductCapture:
    """Validate a bounded sequence of cumulative document snapshots.

    Screens may overlap because the document includes earlier loaded rows.
    Require append-only progression, then two agreeing reads at the visible tail.
    Tab counts are unreliable; count the cards in the qualified capture.
    The capture is not a source-bound Runtime observation or an atomic snapshot.
    """
    if max_frames < 2:
        raise ValueError("max_frames must allow two terminal reads")
    rows: tuple[JinchengProduct, ...] = ()
    end_seen = False
    for _, frame in zip(range(max_frames), frames):
        end, current = _parse_frame(frame)
        if current[: len(rows)] != rows:
            return JinchengProductCapture(
                "PARTIAL", "PRODUCT_LIST_CHANGED", False, rows
            )
        current_keys = {row.display_key for row in current}
        if len(current_keys) != len(current):
            return JinchengProductCapture(
                "PARTIAL", "PRODUCT_IDENTITY_AMBIGUOUS", end, current
            )
        if end and end_seen and current == rows:
            return JinchengProductCapture("COMPLETE", "", True, current)
        rows, end_seen = current, end
    if not rows:
        raise JinchengProductReadError("PRODUCT_DOCUMENT_UNCONFIRMED")
    reason = "PRODUCT_TAIL_UNSTABLE" if end_seen else "PRODUCT_TAIL_UNCONFIRMED"
    return JinchengProductCapture("PARTIAL", reason, end_seen, rows)


def read_jincheng_products(
    port: JinchengProductPort, *, max_frames: int = 40
) -> JinchengProductCapture:
    """Navigate through the injected desktop port and qualify the capture."""

    def frames() -> Iterable[JinchengProductFrame]:
        port.open_all_products()
        while True:
            frame = port.observe_products()
            yield frame
            if not frame.footer_visible:
                port.scroll_products()

    return capture_jincheng_products(frames(), max_frames=max_frames)
