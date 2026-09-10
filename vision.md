# Product Vision

Spoken notes are easy to create and surprisingly hard to recover. A useful detail gets buried in yesterday's update, mixed with an older plan, or phrased differently from how you'd search for it later. I built Kivi to make those moments easier to find and use again.

## The core idea

Kivi is a careful work companion. It remembers enough to restore context, keeps the original record close by, and leaves the final decision with the person using it. The main goal is **continuity** — helping someone return to a topic without reconstructing it from scattered notes and partial memory.

## What matters most

Three activities drive the design:

1. **Recovery** — A person should be able to find an earlier interaction and inspect what was actually said, with the original transcript always one click away.

2. **Evolution tracking** — When plans or details change, Kivi should explain the current state of a topic. It tracks state transitions chronologically, supersedes outdated claims, and detects contradictions proactively rather than silently picking the newest claim.

3. **Grounded drafting** — Kivi should prepare a draft using the relevant history, citing every claim back to its source. These activities should work across Hindi and English references to the same subject.

## What counts as memory

Useful memory includes factual claims, decisions, events, and explicit preferences about how someone wants help. Each memory retains its source, time, speaker, and scope. Temporary details (like "dentist appointment next Tuesday") remain searchable but decay naturally over time through soft exponential curves — they shouldn't rank equally with permanent facts six months later.

Repetition alone should not create a personality profile. Quoted preferences stay attached to the person who expressed them.

## Handling uncertainty

Kivi handles uncertainty conservatively. A dictated message isn't necessarily a sent message. A proposed deadline isn't automatically an accepted deadline. A newer quotation doesn't always replace an earlier fact.

When records conflict, dates are missing, or raw speech recognition differs from formatted text, the uncertainty stays visible. Contradictions surface as disambiguation prompts rather than being hidden behind a confident answer.

## Connected knowledge

Individual facts are more useful when connected. When Kivi extracts entities and relationships from transcripts, it can bridge across conversations — answering questions that require reasoning over multiple separate interactions. The knowledge graph is the connective tissue that turns isolated voice notes into a searchable web of context.

## Trust through control

Trust depends on control. People should be able to open a source from an answer, correct a mistaken memory, limit a preference to the right context, and remove information from future use. Those changes should survive restarts and reprocessing without requiring anyone to understand the underlying database.

## What success looks like

Kivi produces useful, source-backed answers from unfamiliar transcript history while remaining cautious about unsupported conclusions. The system is measurable, inspectable, and clear about its limits. Always-on capture and autonomous external actions can wait until the memory experience itself is dependable.
