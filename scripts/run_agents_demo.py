"""Run the sentinel, enricher, and triage agents against live DataHub.

All three agents read the catalog through the MCP server only. Outcomes come
from the simulator timeline (the source of truth for what happens to the
lineworld graph), so every claim settles and prints in one run:

  sentinel  - forecasts each raw feed's SLA for days 1-3 from arrival history
  enricher  - documents undocumented columns it discovers itself, judged by a
              keyword steward; accepted proposals are applied to DataHub via
              update_description, verified, then reverted to keep re-runs
              identical (permanent writeback is the writeback layer's job)
  triage    - names the root cause of the two scripted incidents from lineage
              plus load telemetry

Run on the box:
  OPENROUTER_API_KEY=... ~/dh/bin/python scripts/run_agents_demo.py
"""

from __future__ import annotations

import os
import time

from ledgerline.agents import EnricherAgent, FreshnessSentinelAgent, TriageAgent
from ledgerline.agents.enricher import undocumented_columns
from ledgerline.claims import ClaimStore
from ledgerline.llm import LLMClient
from ledgerline.mcp_client import DataHubMCP
from ledgerline.settle import (
    INCIDENT_RESOLVED,
    SLA_OUTCOME,
    STEWARD_REVIEW,
    GroundTruthEvent,
    SettlementEngine,
    agent_stats,
)
from ledgerline.simulator.runner import _steward_verdict_fn
from ledgerline.simulator.timeline import INCIDENT_OPEN, build_default_timeline
from ledgerline.simulator.world import build_default_world
from ledgerline.skill import skill_report, trust_score

DB_PATH = os.environ.get("LEDGER_DB", os.path.expanduser("~/ledgerline-demo.db"))


def scorecard(store: ClaimStore, engine_agent_id: str, world, label: str) -> None:
    settled = store.claims(agent_id=engine_agent_id, settled=True)
    wins = sum(1 for c in settled if c.correct)
    stats = agent_stats(store).get(engine_agent_id, {})
    print(
        f"\n{label}: {wins}/{len(settled)} correct | "
        f"brier={stats.get('brier_mean'):.3f} | trust={trust_score(settled)}"
    )


def main() -> None:
    world = build_default_world()
    tl = build_default_timeline(world)
    llm = LLMClient()
    raw = sorted(
        (d for d in world.datasets.values() if d.landing_hour is not None),
        key=lambda d: d.name,
    )

    with ClaimStore(DB_PATH) as store, DataHubMCP() as mcp:
        engine = SettlementEngine(store)

        # ---- freshness sentinel -------------------------------------------
        sentinel = FreshnessSentinelAgent(mcp, llm, agent_id="freshness-sentinel-live")
        print("=== freshness sentinel: forecast days 1-3 for each raw feed ===")
        for day in (1, 2, 3):
            for d in raw:
                history = [tl.lateness[(d.name, prior)] for prior in range(day)]
                claim = sentinel.forecast(d.urn, history, day=day)
                store.record(claim)
                truth = tl.lateness[(d.name, day)]
                engine.process_event(
                    GroundTruthEvent(
                        event_type=SLA_OUTCOME,
                        entity_urn=d.urn,
                        payload={"missed": truth},
                        ts=time.time() + 1,
                    )
                )
                settled = store.get(claim.claim_id)
                verdict = "RIGHT" if settled.correct else "WRONG"
                print(
                    f"  {verdict:5s} day {day} {d.name:15s} predicted "
                    f"{'miss' if claim.prediction['will_miss_sla'] else 'on-time':7s} "
                    f"p={claim.confidence:.2f} (was {'LATE' if truth else 'ON_TIME'}) "
                    f"| {claim.prediction['reason'][:60]}"
                )
        scorecard(store, sentinel.agent_id, world, "sentinel")

        # ---- enricher ------------------------------------------------------
        enricher = EnricherAgent(mcp, llm, agent_id="enricher-live")
        steward = _steward_verdict_fn(world)
        print("\n=== enricher: document every undocumented column it can find ===")
        accepted: list = []
        for d in sorted(world.datasets.values(), key=lambda d: d.name):
            claims = enricher.propose(d.urn)
            for claim in claims:
                store.record(claim)
                col = claim.prediction["column"]
                verdict = steward(d.name, col, claim.prediction["description"])
                engine.process_event(
                    GroundTruthEvent(
                        event_type=STEWARD_REVIEW,
                        entity_urn=d.urn,
                        payload={
                            "column": col,
                            "verdict": verdict,
                            "claim_id": claim.claim_id,
                        },
                        ts=time.time() + 1,
                    )
                )
                settled = store.get(claim.claim_id)
                mark = "ACCEPT" if settled.correct else "REVERT"
                print(
                    f"  {mark:6s} {d.name}.{col:16s} p={claim.confidence:.2f} "
                    f"| {claim.prediction['description'][:70]}"
                )
                if settled.correct:
                    accepted.append(settled)
        scorecard(store, enricher.agent_id, world, "enricher")

        # prove the mutation path: apply accepted docs, verify one, revert all
        if accepted:
            for claim in accepted:
                enricher.apply(claim)
            probe = accepted[0]
            schema = mcp.list_schema_fields(probe.entity_urn)
            live = {
                f["fieldPath"]: f.get("description")
                for f in schema.get("fields", [])
            }.get(probe.prediction["column"])
            print(
                f"\n  applied {len(accepted)} accepted descriptions via update_description"
            )
            print(
                f"  verify {world.by_urn(probe.entity_urn).name}."
                f"{probe.prediction['column']} now reads: {str(live)[:70]}"
            )
            for claim in accepted:
                enricher.unapply(claim)
            still = undocumented_columns(mcp.list_schema_fields(probe.entity_urn))
            print(
                f"  reverted for idempotent re-runs "
                f"({probe.prediction['column']} undocumented again: "
                f"{probe.prediction['column'] in still})"
            )

        # ---- incident triage ----------------------------------------------
        triage = TriageAgent(mcp, llm, agent_id="incident-triage-live")
        print("\n=== incident triage: the two scripted incidents ===")
        for h in [h for h in tl.happenings if h.kind == INCIDENT_OPEN]:
            day = h.tick // 24
            affected = world.datasets[h.payload["dataset"]]
            telemetry = [
                f"{d.name}: day {day} load "
                + ("LATE (landed after SLA)" if tl.lateness[(d.name, day)] else "on time")
                for d in raw
            ]
            claim = triage.diagnose(
                h.payload["incident_urn"],
                affected.urn,
                f"{affected.name} is stale; downstream consumers report outdated numbers",
                telemetry,
            )
            if claim is None:
                print("  !! no upstream candidates found via MCP lineage")
                continue
            store.record(claim)
            true_root = world.datasets[h.payload["root_cause"]]
            engine.process_event(
                GroundTruthEvent(
                    event_type=INCIDENT_RESOLVED,
                    entity_urn=h.payload["incident_urn"],
                    payload={"root_cause_urn": true_root.urn},
                    ts=time.time() + 1,
                )
            )
            settled = store.get(claim.claim_id)
            verdict = "RIGHT" if settled.correct else "WRONG"
            picked = world.by_urn(claim.prediction["root_cause_urn"]).name
            print(
                f"  {verdict:5s} {affected.name}: picked {picked} "
                f"of {claim.prediction['n_candidates']} candidates "
                f"p={claim.confidence:.2f} (true: {true_root.name}) "
                f"| {claim.prediction['reason'][:60]}"
            )
        scorecard(store, triage.agent_id, world, "triage")

        # ---- the ledger so far --------------------------------------------
        print("\n=== ledger summary (all agents in this database) ===")
        report = skill_report(store, n_sims=10_000)
        for agent_id, entry in sorted(report.items()):
            win = entry["win_rate"]
            brier_mean = entry["brier_mean"]
            print(
                f"  {agent_id:22s} settled={entry['n_settled']:3d} "
                f"win={f'{win:.2f}' if win is not None else '-':5s} "
                f"brier={f'{brier_mean:.3f}' if brier_mean is not None else '-':5s} "
                f"trust={entry['trust']:5.1f} verdict={entry['verdict']}"
            )
        engine.close()
    llm.close()


if __name__ == "__main__":
    main()
