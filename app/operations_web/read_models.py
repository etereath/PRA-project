"""运营 Web 的只读页面合同。

这些对象只携带已经由权威事实查询确定的状态；模板不得再次解释领域状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ReadState(str, Enum):
    READY = "ready"
    TRUSTWORTHY_ZERO = "trustworthy_zero"
    EMPTY = "empty"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class StateReadModel:
    state: ReadState
    title: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MetricReadModel:
    label: str
    value: str
    note: str = ""
    state: ReadState = ReadState.READY


@dataclass(frozen=True, slots=True)
class TableReadModel:
    dataset: str
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    state: StateReadModel
    page: int = 1
    page_size: int = 25
    has_previous: bool = False
    has_next: bool = False
    previous_url: str = ""
    next_url: str = ""
    row_urls: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class NotificationItemReadModel:
    title: str
    detail: str
    severity: str
    url: str


@dataclass(frozen=True, slots=True)
class NotificationDrawerReadModel:
    total: int
    items: tuple[NotificationItemReadModel, ...]
    history_url: str = "/database/project?dataset=notifications"


@dataclass(frozen=True, slots=True)
class TodayReadModel:
    platform_trade_date: str
    observed_at: str
    trade_day_status: str
    phase_label: str
    state: StateReadModel
    metrics: tuple[MetricReadModel, ...]
    products: TableReadModel
    todo_items: tuple[NotificationItemReadModel, ...]
    timeline: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class DatabaseReadModel:
    section: str
    section_title: str
    dataset_options: tuple[tuple[str, str, str], ...]
    selected_dataset: str
    trade_date: str
    platform_name: str
    platform_options: tuple[str, ...]
    filter_action: str
    show_business_filters: bool
    table: TableReadModel
    notice: str = ""


@dataclass(frozen=True, slots=True)
class ComponentReadModel:
    name: str
    state: StateReadModel
    checked_at: str


@dataclass(frozen=True, slots=True)
class SystemReadModel:
    overall: StateReadModel
    components: tuple[ComponentReadModel, ...]


@dataclass(frozen=True, slots=True)
class ManagementReadModel:
    pending_tasks: TableReadModel
    pending_reviews: TableReadModel
    automation_runs: TableReadModel
    inventory_state: StateReadModel
    inventory_options: tuple[tuple[str, str, int, int], ...] = field(
        default_factory=tuple
    )
    inventory_receipt: tuple[str, str, str, str] | None = None
    inventory_idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class DetailFieldReadModel:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class DetailReadModel:
    title: str
    subtitle: str
    state: StateReadModel
    fields: tuple[DetailFieldReadModel, ...]
    related: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    back_url: str = ""
    back_label: str = "返回"


@dataclass(frozen=True, slots=True)
class MobileReviewReadModel:
    state: StateReadModel
    review_title: str
    reason: str
    scope: str
    deadline: str
    allowed_actions: tuple[str, ...]
    http_status: str
