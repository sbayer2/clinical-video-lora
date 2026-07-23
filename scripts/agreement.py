"""Agreement analysis: human blind annotations vs machine records (ADC-012).

Joins the annotator's SQLite records for the blind queue with the frozen
machine records via the blind manifest, and reports per-field agreement:

- moment selection: does the human's clip overlap the machine's? (times
  converted to the source film's session clock; IoU + binary overlap)
- segment_class: exact match
- affect_observed / register_selected: token Jaccard after normalization
  (lowercase, split on commas/slashes — machine output showed multi-value
  drift, so token-level is the fair comparison)
- worked / confidence: agreement and delta
- free text (why, move, discriminating_feature, schema_gap): printed
  side-by-side for qualitative reading — these are the fields predicted to
  carry the privileged human signal, and no automatic metric substitutes
  for reading them.

Run after annotating:  .venv/bin/python scripts/agreement.py
"""

import json
import re
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE_DIR = REPO_ROOT / "scripts" / "out" / "blind_queue"
ANNOT_DIR = REPO_ROOT / "scripts" / "out" / "ai_annotations"
OUT_MD = REPO_ROOT / "scripts" / "out" / "agreement_report.md"


def tokens(value: str) -> set[str]:
    return {t.strip().lower() for t in re.split(r"[,/;]| then ", value) if t.strip()}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def load_machine_records() -> dict[str, dict]:
    by_id = {}
    for jl in ANNOT_DIR.glob("*.jsonl"):
        for line in jl.read_text().splitlines():
            w = json.loads(line)
            by_id[w["record"]["record_id"]] = w
    return by_id


def load_human_records() -> dict[str, list[dict]]:
    db = QUEUE_DIR / "annotations.db"
    if not db.exists():
        raise SystemExit(f"no annotations yet: {db} missing — annotate the blind queue first")
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT clip_file, json FROM records ORDER BY created_at").fetchall()
    by_clip: dict[str, list[dict]] = {}
    for clip_file, payload in rows:
        by_clip.setdefault(Path(clip_file).stem, []).append(json.loads(payload))
    return by_clip


def main() -> None:
    manifest = json.loads((QUEUE_DIR / "manifest.json").read_text())
    machine = load_machine_records()
    human = load_human_records()

    lines = ["# Agreement report: human (blind) vs machine\n"]
    stats = {"n": 0, "overlap": 0, "iou": [], "class_match": 0,
             "affect_j": [], "register_j": [], "worked_match": 0,
             "h_worked0": 0, "m_worked0": 0}

    for blind, meta in manifest.items():
        if blind not in human:
            continue
        m = machine[meta["machine_record_id"]]["record"]
        for h in human[blind]:
            stats["n"] += 1
            # human clip bounds are window-local; convert to session clock
            h0, h1 = meta["t0"] + h["clip"]["t_start"], meta["t0"] + h["clip"]["t_end"]
            m0, m1 = m["clip"]["t_start"], m["clip"]["t_end"]
            inter = max(0.0, min(h1, m1) - max(h0, m0))
            union = max(h1, m1) - min(h0, m0)
            iou = inter / union if union else 0.0
            stats["iou"].append(iou)
            stats["overlap"] += inter > 0
            stats["class_match"] += h["segment_class"] == m["segment_class"]
            stats["affect_j"].append(jaccard(h["read"]["affect_observed"], m["read"]["affect_observed"]))
            stats["register_j"].append(jaccard(h["read"]["register_selected"], m["read"]["register_selected"]))
            stats["worked_match"] += h["self_rating"]["worked"] == m["self_rating"]["worked"]
            stats["h_worked0"] += h["self_rating"]["worked"] == 0
            stats["m_worked0"] += m["self_rating"]["worked"] == 0

            lines.append(f"\n## {blind}  ({meta['source_file']} {meta['t0']:.0f}-{meta['t1']:.0f}s)\n")
            lines.append(f"| field | human | machine |\n|---|---|---|")
            lines.append(f"| clip (session clock) | {h0:.0f}-{h1:.0f}s | {m0:.0f}-{m1:.0f}s (IoU {iou:.2f}) |")
            lines.append(f"| segment_class | {h['segment_class']} | {m['segment_class']} |")
            lines.append(f"| affect_observed | {h['read']['affect_observed']} | {m['read']['affect_observed']} |")
            lines.append(f"| register_selected | {h['read']['register_selected']} | {m['read']['register_selected']} |")
            lines.append(f"| worked / conf | {h['self_rating']['worked']} / {h['self_rating']['confidence']} | {m['self_rating']['worked']} / {m['self_rating']['confidence']} |")
            for field, get in [
                ("why", lambda r: r["read"]["why"]),
                ("move", lambda r: r["move"]["description"]),
                ("discriminating_feature", lambda r: r["move"]["discriminating_feature"]),
                ("schema_gap", lambda r: r.get("schema_gap", "—")),
            ]:
                lines.append(f"\n**{field}**\n- human: {get(h)}\n- machine: {get(m)}")

    if not stats["n"]:
        raise SystemExit("no joined records — annotate some blind clips first")

    n = stats["n"]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    summary = [
        "\n\n# Summary\n",
        f"- joined pairs: {n}",
        f"- clip overlap (any): {stats['overlap']}/{n}; mean IoU {mean(stats['iou']):.2f}",
        f"- segment_class match: {stats['class_match']}/{n}",
        f"- affect_observed token Jaccard (mean): {mean(stats['affect_j']):.2f}",
        f"- register_selected token Jaccard (mean): {mean(stats['register_j']):.2f}",
        f"- worked match: {stats['worked_match']}/{n} (human scored worked=0 on "
        f"{stats['h_worked0']}, machine on {stats['m_worked0']} — the contrast-class check)",
        "\nRead the free-text fields above; no metric substitutes for that.",
    ]
    print("\n".join(summary))
    OUT_MD.write_text("\n".join(lines + summary))
    print(f"\nfull side-by-side report: {OUT_MD.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
