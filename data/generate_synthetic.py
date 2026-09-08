"""Create a frozen, fictional, varied 500-record transcript corpus.

No external model is used. The deterministic seed makes the committed JSONL the
reproducible evaluation input and keeps it safe to regenerate.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RNG = random.Random(20260908)
START = datetime(2026, 1, 2, 9, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
PROJECTS = ["Lantern", "Orchid", "Atlas", "Harbor", "Cedar", "Mosaic", "Kite", "Ember"]
CLIENTS = ["Northwind", "Acme", "Nila Foods", "Banyan Health", "Koru", "Saffron Labs"]
PEOPLE = ["Aisha", "Rohan", "Meera", "Arjun", "Tara", "Dev", "Nikhil", "Leela"]
APPS = ["Slack", "Notepad", "Google Meet", "Gmail", "Linear", "VS Code"]


def record(index: int, formatted: str, raw: str | None = None, *, context: dict | None = None) -> dict:
    occurred = START + timedelta(hours=index * 9 + RNG.randint(0, 3))
    return {
        "schema_version": 1, "id": f"synthetic-{index + 1:03d}",
        "raw_asr": raw if raw is not None else formatted.casefold().replace(".", ""),
        "formatted_text": formatted, "occurred_at": occurred.isoformat(), "timezone": "Asia/Kolkata",
        "app": RNG.choice(APPS), "language_hints": ["en"],
        "context": {"synthetic": True, **(context or {})},
    }


def make_record(index: int) -> dict:
    project, client, person = RNG.choice(PROJECTS), RNG.choice(CLIENTS), RNG.choice(PEOPLE)
    family = index % 12
    if family == 0:
        return record(index, f"{project} launches on {RNG.choice(['Monday', 'Tuesday', 'Thursday'])} after {person} approves the release checklist.", context={"family": "project_schedule"})
    if family == 1:
        return record(index, f"{client} approved {RNG.choice(['₹45,000', '₹80,000', '₹1,20,000'])} for {project} phase one.", context={"family": "customer_budget"})
    if family == 2:
        return record(index, f"{person} said the {project} review needs the security note before sign-off.", context={"family": "meeting_episode"})
    if family == 3:
        return record(index, f"I prefer concise weekly updates with a decision and next step for {project}.", context={"family": "preference", "scope": project})
    if family == 4:
        return record(index, f"The train for the {client} visit leaves at {RNG.choice(['07:20', '08:30', '18:10'])} on {RNG.choice(['Wednesday', 'Thursday', 'Friday'])}.", context={"family": "travel"})
    if family == 5:
        return record(index, f"Study note: revise {RNG.choice(['database indexing', 'distributed systems', 'probability'])} before the {RNG.choice(['Tuesday', 'Friday'])} exam.", context={"family": "study"})
    if family == 6:
        return record(index, f"For the household list, buy {RNG.choice(['olive oil', 'coffee beans', 'dish soap'])} and {RNG.choice(['rice', 'oranges', 'batteries'])}.", context={"family": "household"})
    if family == 7:
        return record(index, f"{project} slipped because the API contract changed after the client review.", context={"family": "project_update"})
    if family == 8:
        return record(index, f"{person} asked whether {project} could launch Friday; no decision was made.", context={"family": "hypothetical"})
    if family == 9:
        return record(index, f"Draft only: tell {client} that {project} is ready for the next discussion.", context={"family": "draft"})
    if family == 10:
        return record(index, f"{project} demo is on Monday, not Sunday.", f"{project.casefold()} demo is on monday not sunday", context={"family": "asr_disagreement"})
    return record(index, f"नोट: {project} के लिए {person} से अगला अपडेट लेना है।", f"note {project.casefold()} ke liye {person.casefold()} se agla update lena hai", context={"family": "code_switched"})


records = [make_record(index) for index in range(500)]
ROOT.joinpath("synthetic-500.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
print("Wrote 500 varied synthetic records.")
