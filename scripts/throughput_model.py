"""Corpus growth model (action plan 9.5, throughput risk 8.7).

Models annotated-clip throughput as the minimum of two constraints:
supply (performance-moments that actually occur and are consented/recorded)
and capacity (weekly human annotation budget, including triage of the
candidate review queue, whose size is inflated by detector imprecision).

Stdlib only. Run: python3 scripts/throughput_model.py
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    shifts_per_week: float
    notable_clips_per_shift: float   # true performance-moments worth keeping
    consent_rate: float              # fraction of patients granting research use
    detector_precision: float        # true clips / candidates in review queue
    triage_min_per_candidate: float  # decide keep/discard from the queue
    annotate_min_per_clip: float     # full schema annotation of a kept clip
    budget_hours_per_week: float     # sustainable human time for triage+annotation
    weeks_per_year: float = 46.0     # allow for vacation, missed weeks


def evaluate(s: Scenario) -> dict[str, float]:
    supply = s.shifts_per_week * s.notable_clips_per_shift * s.consent_rate
    # Each kept clip drags (1/precision) candidates through triage.
    min_per_clip = s.annotate_min_per_clip + s.triage_min_per_candidate / s.detector_precision
    capacity = s.budget_hours_per_week * 60.0 / min_per_clip
    weekly = min(supply, capacity)
    hours_to_clear_supply = supply * min_per_clip / 60.0
    return {
        "supply_per_week": supply,
        "capacity_per_week": capacity,
        "annotated_per_week": weekly,
        "hours_needed_for_full_supply": hours_to_clear_supply,
        "year_1": weekly * s.weeks_per_year,
        "year_5": weekly * s.weeks_per_year * 5,
    }


SCENARIOS = [
    Scenario("conservative", shifts_per_week=2, notable_clips_per_shift=1.5,
             consent_rate=0.6, detector_precision=0.3,
             triage_min_per_candidate=0.5, annotate_min_per_clip=4.0,
             budget_hours_per_week=1.5),
    Scenario("moderate", shifts_per_week=3, notable_clips_per_shift=3.0,
             consent_rate=0.7, detector_precision=0.5,
             triage_min_per_candidate=0.5, annotate_min_per_clip=3.0,
             budget_hours_per_week=3.0),
    Scenario("aggressive", shifts_per_week=4, notable_clips_per_shift=5.0,
             consent_rate=0.8, detector_precision=0.6,
             triage_min_per_candidate=0.4, annotate_min_per_clip=2.0,
             budget_hours_per_week=5.0),
]

# Reference points for "is the terminal number worth the build" (plan 8.7).
THRESHOLDS = {
    "LIMA-style adaptation (~1k)": 1_000,
    "plan 8.7 five-year figure": 2_500,
    "comfortable adapter corpus": 5_000,
}


def main() -> None:
    cols = ["scenario", "supply/wk", "capacity/wk", "annotated/wk",
            "hrs/wk to clear supply", "year 1", "year 5"]
    rows = []
    for s in SCENARIOS:
        r = evaluate(s)
        rows.append([
            s.name,
            f"{r['supply_per_week']:.1f}",
            f"{r['capacity_per_week']:.1f}",
            f"{r['annotated_per_week']:.1f}",
            f"{r['hours_needed_for_full_supply']:.1f}",
            f"{r['year_1']:.0f}",
            f"{r['year_5']:.0f}",
        ])

    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    line = " | ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(line)
    print("-|-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))

    print("\nreference thresholds:")
    for label, n in THRESHOLDS.items():
        verdicts = []
        for s in SCENARIOS:
            y5 = evaluate(s)["year_5"]
            verdicts.append(f"{s.name}: {'reached' if y5 >= n else f'missed ({y5:.0f})'}")
        print(f"  {label:<32} {n:>5}  ->  " + "; ".join(verdicts))

    print("\nnotes:")
    print("  - supply assumes plan 4.1 (performance moments are 3-5% of runtime,")
    print("    i.e. a handful of clips per shift, not per encounter).")
    print("  - capacity includes triage of detector false positives: at precision p,")
    print("    each kept clip costs triage_time/p on top of annotation time.")
    print("  - binding constraint per scenario is min(supply, capacity); see which")
    print("    column is smaller before buying hardware to raise the other one.")


if __name__ == "__main__":
    main()
