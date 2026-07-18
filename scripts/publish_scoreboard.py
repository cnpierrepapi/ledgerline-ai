"""Publish the ledger to the public scoreboard (Supabase).

Reads the ledger SQLite database, recomputes the skill report, and upserts
three projections through PostgREST: per-agent records, calibration bins,
and the full claim history. Idempotent: re-running replaces prior rows for
the same agents and claims.

Env:
  LEDGER_DB              path to the ledger database
  SUPABASE_URL           https://<project>.supabase.co
  SUPABASE_SERVICE_KEY   service-role key (writes bypass RLS; never ship it
                         to the browser)

Run:  python scripts/publish_scoreboard.py
"""

from __future__ import annotations

import os
import sys

import httpx

from ledgerline.claims import ClaimStore
from ledgerline.skill import skill_report

DB_PATH = os.environ.get("LEDGER_DB", os.path.expanduser("~/ledgerline-demo.db"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def upsert(client: httpx.Client, table: str, rows: list[dict], conflict: str) -> None:
    if not rows:
        return
    resp = client.post(
        f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={conflict}",
        json=rows,
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Prefer": "resolution=merge-duplicates",
            "Content-Type": "application/json",
        },
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"{table} upsert failed: {resp.status_code} {resp.text[:400]}")
    print(f"  {table}: {len(rows)} rows")


def main() -> None:
    if not SUPABASE_URL or not SERVICE_KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_SERVICE_KEY")

    with ClaimStore(DB_PATH) as store:
        report = skill_report(store)
        claims = store.claims()

        model_by_agent = {}
        for c in claims:
            model_by_agent.setdefault(c.agent_id, c.model_id)

        agent_rows = []
        calibration_rows = []
        for agent_id, rec in report.items():
            agent_rows.append(
                {
                    "agent_id": agent_id,
                    "model_id": model_by_agent.get(agent_id),
                    "trust": rec["trust"],
                    "verdict": rec["verdict"],
                    "n_total": rec["n_total"],
                    "n_settled": rec["n_settled"],
                    "wins": rec.get("wins"),
                    "win_rate": rec.get("win_rate"),
                    "brier_mean": rec.get("brier_mean"),
                    "ece": rec.get("ece"),
                    "p_value": rec.get("p_value"),
                    "q_value": rec.get("q_value"),
                    "expected_null_wins": rec.get("expected_null_wins"),
                }
            )
            for b in rec.get("calibration") or []:
                calibration_rows.append(
                    {
                        "agent_id": agent_id,
                        "bin_low": b["bin_low"],
                        "bin_high": b["bin_high"],
                        "n": b["n"],
                        "mean_confidence": b["mean_confidence"],
                        "frac_true": b["frac_true"],
                    }
                )

        claim_rows = [
            {
                "claim_id": c.claim_id,
                "agent_id": c.agent_id,
                "model_id": c.model_id,
                "claim_type": c.claim_type,
                "entity_urn": c.entity_urn,
                "prediction": c.prediction,
                "confidence": c.confidence,
                "created_ts": c.created_ts,
                "settled_ts": c.settled_ts,
                "outcome": c.outcome,
                "correct": c.correct,
            }
            for c in claims
        ]

    print(f"publishing {len(agent_rows)} agents, {len(claim_rows)} claims -> {SUPABASE_URL}")
    with httpx.Client(timeout=30) as client:
        upsert(client, "ll_agents", agent_rows, "agent_id")
        upsert(client, "ll_calibration", calibration_rows, "agent_id,bin_low")
        upsert(client, "ll_claims", claim_rows, "claim_id")
    print("done")


if __name__ == "__main__":
    main()
