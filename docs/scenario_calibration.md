# Scenario Calibration Notes

How the four built-in scenarios are defined, what lever values they use, and how they were calibrated to external benchmarks.

---

## The four scenarios at a glance

| | Decline | Conservative | Base | Optimistic |
|---|---|---|---|---|
| **2050 anchor** | IEA Low Nuclear ~250 GW | IAEA Low ~561 GW | IEA STEPS ~647 GW | IAEA High ~992 GW |
| **Extension policy** | AcceleratedRetirement | CurrentPolicy | CurrentPolicy | ExtendedOperations |
| **Large pipeline** | UC only | UC only | UC + Planned | UC + Planned + Proposed |
| **SMR pipeline** | UC only | UC + Planned | UC + Planned | UC + Planned + Proposed |
| **Construction delay** | +3 years | +2 years | 0 years | 0 years |
| **Post-2040 global growth** | 10.5 GW/yr | 29.1 GW/yr | 29.7 GW/yr | 55.6 GW/yr |
| **China post-2040** | 2.8 GW/yr | 8.2 GW/yr | 8.0 GW/yr | 15.3 GW/yr |
| **SMR post-2040 share** | 10% | 15% | 20% | 40% |

---

## Extension policies

Three policies govern how long operating reactors run. The policy is applied in `model/retirement.py`.

**AcceleratedRetirement** — Reactors operate to their currently approved design life only. No new extension rounds. Tier 1 declared dates respected; Tier 2 dates frozen at nominal licence expiry (after advancing past 2024 for reactors already operating beyond their nominal date).

**CurrentPolicy** — Each country follows its own stated regulatory practice. Extension grants are probabilistic per reactor, determined by a deterministic hash of the reactor ID (reproducible, not random) against country-specific fractions:

| Country | Extension probability | Rationale |
|---|---|---|
| United States | 95% | NRC approves nearly every renewal application |
| Russia | 85% | Rosatom standard practice |
| China | 85% | Government support; young fleet |
| Canada | 80% | OPG/NB Power refurbishment programmes |
| Finland | 80% | Strong regulator support |
| South Korea | 70% | NRC-K approves; political pressure on older units |
| France | 65% | Grand Carénage; ASN 50-yr reviews underway |
| Ukraine | 70% | Ongoing |
| India | 65% | AERB practice; execution constraints |
| Sweden | 70% | Mixed regulatory environment |
| Belgium | 40% | Only Doel-4 / Tihange-3 extended; 3 units closing 2025 |
| Japan | 40% | Restart recovery underway; local consent barriers |
| United Kingdom | 25% | AGR fleet retiring; only new EPRs extending |
| All others | 55% | Default |

**ExtendedOperations** — All eligible reactors run to the maximum life permitted under current national law: US 80 yr, Japan 80 yr (GX legislation), France 60 yr (ASN pathway), Russia 70 yr. Used in the Optimistic scenario only.

---

## Pipeline realization rates

The "low / medium / high" pipeline presets used by each scenario:

| Preset | Large UC | Large Planned | Large Proposed | SMR UC | SMR Planned | SMR Proposed |
|---|---|---|---|---|---|---|
| **Low** | 100% | 0% | 0% | 100% | 0% | 0% |
| **Medium** | 100% | 100% | 0% | 100% | 100% | 0% |
| **High** | 100% | 100% | 100% | 100% | 100% | 100% |

Note: these are the dashboard-layer presets. The DB-layer scenarios use continuous rates:

| Scenario | Large UC | Large Planned | Large Proposed | Delay adder |
|---|---|---|---|---|
| Decline | 90% | 40% | 5% | 3 yr |
| Conservative | 92% | 55% | 15% | 2 yr |
| Base | 100% | 100% | 0% | 0 yr |
| Optimistic | 95% | 85% | 50% | 0 yr |

The dashboard-layer presets simplify this for user-facing controls; they snap to the nearest continuous rate when writing a custom scenario to the DB.

---

## Post-2040 calibration

The model switches from bottom-up (unit-level) to top-down (parametric) at 2040. The top-down phase adds a fixed `post2040_global_growth_gw_per_year` each year minus continuing unit-level retirements.

The global rate is split by region using proportions from `scenario_regional_params`. Base-case regional split (GW/yr):

| Region | Base |
|---|---|
| China | 8.61 |
| United States | 5.17 |
| South Asia | 4.13 |
| Russia | 3.44 |
| Emerging & Rest | 1.72 |
| Eastern Europe | 1.38 |
| East Asia | 1.38 |
| France | 1.03 |
| United Kingdom | 1.03 |
| Canada & Mexico | 1.03 |
| Rest of Western Europe | 0.69 |
| Southeast Asia | 0.69 |

Other scenarios scale these proportions to their respective global rates.

---

## Why Conservative > Base in post-2040 rate

Conservative's post-2040 rate (29.1 GW/yr) is slightly higher than Base (29.7 GW/yr) and comes close. This is counterintuitive but intentional: Conservative's tighter pre-2040 pipeline (UC only, 2-year delay) means significantly less capacity is added 2024–2040. The post-2040 rate must be higher to still reach the IAEA Low 2050 anchor (~561 GW). The scenarios anchor to *external benchmarks at 2050*, not to internally consistent logic throughout.

---

## Benchmark sources

| Benchmark | Source | 2050 value | Coverage |
|---|---|---|---|
| IEA Low Nuclear Case | IEA WEO 2024 | ~250 GW | Global only |
| IEA STEPS | IEA WEO 2024 | ~647 GW | Global only |
| IEA APS | IEA WEO 2024 | ~874 GW | Global only |
| IAEA Low | IAEA RDS-1 2025 | ~561 GW | Global + regions |
| IAEA High | IAEA RDS-1 2025 | ~992 GW | Global + regions |
| IAEA Observed | IAEA RDS-1 2025 | 2024 actual | 2024 anchor |

**Methodological gap:** IEA/IAEA figures count *total installed capacity* (including Long Term Shutdown reactors). This model tracks *operating capacity* only. This produces a ~4 GW gap at 2024 that is real and methodological, not an error. The gap is documented in the dashboard UI.

---

## Recalibrating for a new dataset or benchmarks

If the company version uses different benchmarks or a different baseline year:

1. Run all four scenarios and note the 2050 global capacity each produces with current `post2040_global_growth_gw_per_year` values.
2. For each scenario, compute the delta to the target benchmark.
3. Adjust `post2040_global_growth_gw_per_year` iteratively — each 1 GW/yr change produces approximately 10 GW of additional 2050 capacity (10-year top-down phase).
4. Re-check the 2040 anchor: if the new dataset produces a significantly different 2040 baseline (due to different fleet or pipeline), the post-2040 rates will need to be re-derived from scratch.
5. Update `PRESET_SCENARIOS` in `model/scenarios.py` and re-run `python ingest.py --project` to recompute and store all scenario outputs.
