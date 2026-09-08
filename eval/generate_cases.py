"""Generate frozen evidence-retrieval cases from the deterministic corpus."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
records = [json.loads(line) for line in (ROOT / "data" / "synthetic-500.jsonl").read_text(encoding="utf-8").splitlines() if line]
cases = []
for index, item in enumerate(records[:120]):
    formatted = item["formatted_text"]
    family = item.get("context", {}).get("family", "unspecified")
    cases.append({
        "id": f"case-{index + 1:03d}",
        "category": family,
        "question": f"Find this recorded interaction: {formatted}",
        "expected_status": "answered",
        "required_external_id": item["id"],
        "expected_text": formatted,
    })
(ROOT / "eval" / "cases.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n", encoding="utf-8")
live_smoke = []
seen_families = set()
for case in cases:
    if case["category"] not in seen_families:
        live_smoke.append(case)
        seen_families.add(case["category"])
(ROOT / "eval" / "live-smoke-cases.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in live_smoke) + "\n", encoding="utf-8")
print(f"Wrote {len(cases)} evidence-labelled cases and {len(live_smoke)} live smoke cases.")
