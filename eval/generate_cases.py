"""Generate frozen evidence-retrieval cases from the deterministic corpus."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
records = [json.loads(line) for line in (ROOT / "data" / "synthetic-500.jsonl").read_text(encoding="utf-8").splitlines() if line]


def make_natural_case(item: dict) -> tuple[str, str] | None:
    text = item["formatted_text"]
    family = item.get("context", {}).get("family", "")

    if family == "project_schedule":
        m = re.match(r"(\w+) launches on (\w+) after (\w+) approves the release checklist\.", text)
        if m:
            proj, day, person = m.groups()
            return f"When does {proj} launch after {person} approves the release checklist?", day
    elif family == "customer_budget":
        m = re.match(r"([\w\s]+) approved (₹[\d,]+) for (\w+) phase one\.", text)
        if m:
            client, amt, proj = m.groups()
            return f"How much budget did {client.strip()} approve for {proj} phase one?", amt
    elif family == "meeting_episode":
        m = re.match(r"(\w+) said the (\w+) review needs the security note before sign-off\.", text)
        if m:
            person, proj = m.groups()
            return f"What did {person} say the {proj} review needs before sign-off?", "security note"
    elif family == "preference":
        m = re.match(r"I prefer concise weekly updates with a decision and next step for (\w+)\.", text)
        if m:
            proj = m.group(1)
            return f"What update cadence and format is preferred for {proj}?", "weekly"
    elif family == "travel":
        m = re.match(r"The train for the ([\w\s]+) visit leaves at (\d\d:\d\d) on (\w+)\.", text)
        if m:
            client, time, _ = m.groups()
            return f"When does the train leave for the {client.strip()} visit?", time
    elif family == "study":
        m = re.match(r"Study note: revise ([\w\s]+) before the (\w+) exam\.", text)
        if m:
            topic, day = m.groups()
            return f"What study topic should be revised before the {day} exam?", topic.strip()
    elif family == "household":
        m = re.match(r"For the household list, buy ([\w\s]+) and ([\w\s]+)\.", text)
        if m:
            it1, _ = m.groups()
            return "What grocery items should be bought for the household list?", it1.strip()
    elif family == "project_update":
        m = re.match(r"(\w+) slipped because the API contract changed after the client review\.", text)
        if m:
            proj = m.group(1)
            return f"Why did {proj} slip after the client review?", "API contract"
    elif family == "hypothetical":
        m = re.match(r"(\w+) asked whether (\w+) could launch Friday; no decision was made\.", text)
        if m:
            person, proj = m.groups()
            return f"Was a decision made when {person} asked whether {proj} could launch Friday?", "no decision"
    elif family == "draft":
        m = re.match(r"Draft only: tell ([\w\s]+) that (\w+) is ready for the next discussion\.", text)
        if m:
            client, proj = m.groups()
            return f"What is the draft message for {client.strip()} regarding {proj}?", "next discussion"
    elif family == "asr_disagreement":
        m = re.match(r"(\w+) demo is on Monday, not Sunday\.", text)
        if m:
            proj = m.group(1)
            return f"On what day is the {proj} demo scheduled (Monday or Sunday)?", "Monday"
    elif family == "code_switched":
        m = re.match(r"नोट: (\w+) के लिए (\w+) से अगला अपडेट लेना है।", text)
        if m:
            proj, person = m.groups()
            return f"{proj} के लिए किससे अगला अपडेट लेना है?", person

    return None


NEGATIVE_QUESTIONS = [
    "What is the capital of Peru?",
    "Who won the 1998 football World Cup?",
    "What is the recipe for vanilla ice cream?",
    "How many moons does Jupiter have?",
    "Who wrote the play Romeo and Juliet?",
    "What is the tallest mountain in the world?",
    "When did the first human land on the Moon?",
    "What is the speed of light in vacuum?",
    "How do you calculate compound interest?",
    "What is the currency of Switzerland?",
]

cases = []
seen_texts = set()
case_idx = 1

for item in records:
    formatted = item["formatted_text"]
    if formatted in seen_texts:
        continue
    natural = make_natural_case(item)
    if not natural:
        continue
    seen_texts.add(formatted)
    question, expected_key = natural
    family = item.get("context", {}).get("family", "unspecified")
    cases.append({
        "id": f"case-{case_idx:03d}",
        "category": family,
        "question": question,
        "expected_status": "answered",
        "required_external_id": item["id"],
        "expected_text": expected_key,
    })
    case_idx += 1
    if len(cases) == 110:
        break

for neg_q in NEGATIVE_QUESTIONS:
    cases.append({
        "id": f"case-{case_idx:03d}",
        "category": "negative_unrelated",
        "question": neg_q,
        "expected_status": "insufficient_evidence",
        "required_external_id": None,
        "expected_text": None,
    })
    case_idx += 1

(ROOT / "eval" / "cases.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n", encoding="utf-8")

live_smoke = []
seen_families = set()
for case in cases:
    if case["category"] not in seen_families:
        live_smoke.append(case)
        seen_families.add(case["category"])

(ROOT / "eval" / "live-smoke-cases.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in live_smoke) + "\n", encoding="utf-8")
print(f"Wrote {len(cases)} evidence-labelled cases and {len(live_smoke)} live smoke cases.")
