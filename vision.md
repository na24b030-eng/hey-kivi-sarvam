# Product Vision

## The Core Idea
Kivi turns spoken voice notes into an organized, searchable memory that you can query, verify, and edit.

## The Problem
People dictate notes because speaking is faster than typing. But voice notes are hard to use later:
1. **Search fails**: You often forget the exact words you used, especially when switching between Hindi and English.
2. **Plans change**: A note from Friday might cancel a plan from Monday. Most tools treat all notes as equally true.
3. **AI models guess**: When an AI does not know the answer, it often hallucinates false details instead of checking the evidence.

## What Kivi Does
Kivi acts as a dependable memory layer between your voice notes and your work.

### 1. Grounded Answers
When you ask Kivi a question, it finds the relevant transcripts and writes an answer based only on what you actually said. Every claim links directly to its source transcript so you can verify it in one click. If there is not enough evidence, Kivi says so plainly.

### 2. Connected Knowledge
Instead of treating each voice note as an isolated file, Kivi extracts key people, projects, and dates into a simple knowledge graph. This lets you ask questions that span multiple conversations, like finding who manages a specific project when those details were mentioned on different days.

### 3. Tracking Evolution and Expiry
Work changes constantly. Kivi handles this in two ways:
- **Timeline updates**: Newer decisions supersede older ones.
- **Natural decay**: Temporary details like meeting reminders fade over time so they do not clutter future searches, while standing preferences remain permanent.

### 4. Direct Contradiction Detection
If you record two notes close together that say opposite things, Kivi does not secretly pick one. It flags the conflict and asks you to clarify which one is correct.

### 5. Complete User Control
You always own your data. You can:
- Open the original transcript behind any answer.
- Correct any fact Kivi extracted.
- Permanently delete notes, which immediately removes their text, chunks, and vector embeddings from the database.

## What Success Looks Like
Success is simple: you can dictate notes all week, ask Kivi what happened or what was decided, and get an accurate, verified answer in seconds with zero guesswork.
