"""Synthetic warehouse world: a column-level lineage graph.

The world is deliberately small enough to read but rich enough that blast
radius is non-trivial: column-level derivations mean a downstream asset can
sit below a changed table and still be unaffected, so "everything downstream
breaks" is a losing strategy and real lineage reasoning wins.

The same world can be ingested into a live DataHub instance (datasets +
upstream lineage + column docs) so agents see it through the MCP server; the
simulator is the source of truth for what actually happens to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PLATFORM = "postgres"
DB = "lineworld"


def dataset_urn(name: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{PLATFORM},{DB}.{name},PROD)"


@dataclass(frozen=True)
class Column:
    name: str
    description: Optional[str] = None  # None = undocumented (enricher target)
    gold_keywords: tuple[str, ...] = ()  # steward accepts descriptions containing one


@dataclass
class Dataset:
    name: str
    columns: list[Column]
    # column name -> (upstream dataset name, upstream column name)
    derived_from: dict[str, tuple[str, str]] = field(default_factory=dict)
    landing_hour: Optional[int] = None  # hour-of-day the load lands (raw layer)
    sla_hour: Optional[int] = None  # hour-of-day the load must land by

    @property
    def urn(self) -> str:
        return dataset_urn(self.name)


class World:
    def __init__(self, datasets: list[Dataset]):
        self.datasets = {d.name: d for d in datasets}
        if len(self.datasets) != len(datasets):
            raise ValueError("duplicate dataset names")
        for d in datasets:
            for col, (up, up_col) in d.derived_from.items():
                if up not in self.datasets:
                    raise ValueError(f"{d.name}.{col} derives from unknown {up}")
                if up_col not in {c.name for c in self.datasets[up].columns}:
                    raise ValueError(f"{d.name}.{col} derives from {up}.{up_col} (missing)")

    def by_urn(self, urn: str) -> Dataset:
        for d in self.datasets.values():
            if d.urn == urn:
                return d
        raise KeyError(urn)

    def upstreams(self, name: str) -> set[str]:
        return {up for up, _ in self.datasets[name].derived_from.values()}

    def downstreams(self, name: str) -> set[str]:
        return {
            d.name
            for d in self.datasets.values()
            if name in self.upstreams(d.name)
        }

    def descendants(self, name: str) -> set[str]:
        out: set[str] = set()
        frontier = [name]
        while frontier:
            cur = frontier.pop()
            for down in self.downstreams(cur):
                if down not in out:
                    out.add(down)
                    frontier.append(down)
        return out

    def ancestors(self, name: str) -> set[str]:
        out: set[str] = set()
        frontier = [name]
        while frontier:
            cur = frontier.pop()
            for up in self.upstreams(cur):
                if up not in out:
                    out.add(up)
                    frontier.append(up)
        return out

    def blast_set(self, dataset: str, column: str) -> set[str]:
        """Datasets whose columns transitively derive from dataset.column."""
        broken_cols: set[tuple[str, str]] = {(dataset, column)}
        changed = True
        while changed:
            changed = False
            for d in self.datasets.values():
                for col, (up, up_col) in d.derived_from.items():
                    if (up, up_col) in broken_cols and (d.name, col) not in broken_cols:
                        broken_cols.add((d.name, col))
                        changed = True
        return {name for name, _ in broken_cols if name != dataset}

    def undocumented(self) -> list[tuple[Dataset, Column]]:
        return [
            (d, c)
            for d in self.datasets.values()
            for c in d.columns
            if c.description is None
        ]


def build_default_world() -> World:
    """Twelve datasets across raw, staging, marts, and reporting layers."""
    return World(
        [
            Dataset(
                "raw_orders",
                columns=[
                    Column("order_id", "Primary key of the order."),
                    Column("customer_id", "Foreign key to the customer."),
                    Column("order_total_usd", None, ("total", "usd", "amount")),
                    Column("discount_code", None, ("discount", "promo", "coupon")),
                    Column("created_at", "Order creation timestamp."),
                ],
                landing_hour=2,
                sla_hour=4,
            ),
            Dataset(
                "raw_customers",
                columns=[
                    Column("customer_id", "Primary key of the customer."),
                    Column("email", None, ("email",)),
                    Column("full_name", "Customer display name."),
                    Column("country_code", None, ("country", "iso")),
                    Column("signup_ts", "Signup timestamp."),
                ],
                landing_hour=3,
                sla_hour=5,
            ),
            Dataset(
                "raw_payments",
                columns=[
                    Column("payment_id", "Primary key of the payment."),
                    Column("order_id", "Order the payment settles."),
                    Column("amount_usd", None, ("amount", "usd", "paid")),
                    Column("method", "Payment method."),
                    Column("paid_at", "Settlement timestamp."),
                ],
                landing_hour=4,
                sla_hour=6,
            ),
            Dataset(
                "raw_web_events",
                columns=[
                    Column("event_id", "Primary key of the event."),
                    Column("customer_id", "Customer who fired the event."),
                    Column("event_type", None, ("event", "type", "action")),
                    Column("occurred_at", "Event timestamp."),
                ],
                landing_hour=1,
                sla_hour=3,
            ),
            Dataset(
                "stg_orders",
                columns=[
                    Column("order_id", "Order key."),
                    Column("customer_id", "Customer key."),
                    Column("total_usd", "Order total in USD."),
                    Column("discount_code", "Applied discount code."),
                    Column("ordered_at", "Order timestamp."),
                ],
                derived_from={
                    "order_id": ("raw_orders", "order_id"),
                    "customer_id": ("raw_orders", "customer_id"),
                    "total_usd": ("raw_orders", "order_total_usd"),
                    "discount_code": ("raw_orders", "discount_code"),
                    "ordered_at": ("raw_orders", "created_at"),
                },
            ),
            Dataset(
                "stg_customers",
                columns=[
                    Column("customer_id", "Customer key."),
                    Column("email", "Customer email."),
                    Column("country", "Customer country."),
                ],
                derived_from={
                    "customer_id": ("raw_customers", "customer_id"),
                    "email": ("raw_customers", "email"),
                    "country": ("raw_customers", "country_code"),
                },
            ),
            Dataset(
                "stg_payments",
                columns=[
                    Column("payment_id", "Payment key."),
                    Column("order_id", "Order key."),
                    Column("amount_usd", None, ("amount", "usd", "paid")),
                ],
                derived_from={
                    "payment_id": ("raw_payments", "payment_id"),
                    "order_id": ("raw_payments", "order_id"),
                    "amount_usd": ("raw_payments", "amount_usd"),
                },
            ),
            Dataset(
                "fct_orders",
                columns=[
                    Column("order_id", "Order key."),
                    Column("customer_id", "Customer key."),
                    Column("total_usd", "Order total in USD."),
                    Column("discount_usd", None, ("discount", "usd")),
                ],
                derived_from={
                    "order_id": ("stg_orders", "order_id"),
                    "customer_id": ("stg_orders", "customer_id"),
                    "total_usd": ("stg_orders", "total_usd"),
                    "discount_usd": ("stg_orders", "discount_code"),
                },
            ),
            Dataset(
                "dim_customers",
                columns=[
                    Column("customer_id", "Customer key."),
                    Column("email", "Customer email."),
                    Column("country", "Customer country."),
                ],
                derived_from={
                    "customer_id": ("stg_customers", "customer_id"),
                    "email": ("stg_customers", "email"),
                    "country": ("stg_customers", "country"),
                },
            ),
            Dataset(
                "fct_revenue",
                columns=[
                    Column("order_id", "Order key."),
                    Column("revenue_usd", "Recognized revenue in USD."),
                    Column("paid_usd", None, ("paid", "usd", "amount")),
                ],
                derived_from={
                    "order_id": ("fct_orders", "order_id"),
                    "revenue_usd": ("fct_orders", "total_usd"),
                    "paid_usd": ("stg_payments", "amount_usd"),
                },
            ),
            Dataset(
                "fct_engagement",
                columns=[
                    Column("customer_id", "Customer key."),
                    Column("events_30d", None, ("events", "30", "count")),
                ],
                derived_from={
                    "customer_id": ("raw_web_events", "customer_id"),
                    "events_30d": ("raw_web_events", "event_type"),
                },
            ),
            Dataset(
                "rpt_daily_kpis",
                columns=[
                    Column("revenue", "Daily revenue."),
                    Column("active_customers", "Daily active customers."),
                ],
                derived_from={
                    "revenue": ("fct_revenue", "revenue_usd"),
                    "active_customers": ("fct_engagement", "events_30d"),
                },
            ),
        ]
    )
