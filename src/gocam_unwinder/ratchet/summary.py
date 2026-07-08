"""Roll-up summary across all ratchet stages (Phase A model + Phase B unit).

Writes a single TSV: one row per stage with in / removed / out counts, so the
monotonic shrink is visible end to end. Consumes any object exposing
``phase``, ``index``, ``rule_id``, ``in_count``, ``removed``, ``out_count``
(see cli.StageStat).
"""

from pathlib import Path


def write_summary(stages, path):
    path = Path(path)
    lines = ["Phase\tStage\tRule\tIn\tRemoved\tOut"]
    for s in stages:
        lines.append("\t".join([
            s.phase, f"{s.index:02d}", s.rule_id,
            str(s.in_count), str(s.removed), str(s.out_count),
        ]))
    path.write_text("\n".join(lines) + "\n")
    return path
