# Formal Benchmark Report: Grounded Memory & Retrieval

This report documents the empirical evaluation of **Hey Kivi**, measuring evidence retrieval accuracy, explicit abstention precision, cross-lingual transfer, and end-to-end query latency across 120 frozen test cases.

---

## 1. Executive Summary

The evaluation suite tests the system against realistic speech memory challenges: phonetic ASR errors, code-switched Hindi-English phrasing, multi-hop entity relations, scheduled time decay, and adversarial out-of-domain questions.

| Metric | Result | Target | Status |
|:---|:---|:---|:---|
| **Total Evaluation Cases** | **120** | 100+ | Passed |
| **Cases Passed** | **120 / 120** | 100% | **100%** |
| **Evidence Retrieval Accuracy** | **1.00 (100%)** | >= 0.95 | Passed |
| **Abstention Precision (Refusal)** | **1.00 (100%)** | 1.00 | **Zero Hallucinations** |
| **Median Query Latency (p50)** | **90.13 ms** | < 200 ms | Passed |
| **95th Percentile Latency (p95)** | **112.43 ms** | < 350 ms | Passed |
| **Database Footprint (500 sources)**| **12.57 MB** | < 50 MB | Passed |
| **Embedding Model** | `intfloat/multilingual-e5-small` | CPU-friendly | Verified |

- **Evaluation Suite Hash**: `3562d606dadcee2fd2885c06bec051801374eeeb11b81647d53f283d0d4e8d17`
- **Benchmark Suite File**: `eval/cases.jsonl`
- **Output Artifact**: `eval/results/benchmark_report.json`

---

## 2. Category-by-Category Breakdown

The 120 evaluation cases are divided into 12 functional categories covering personal and work spoken transcripts:

| Category | Cases | Passed | Accuracy | Primary Stressor Tested |
|:---|:---:|:---:|:---:|:---|
| `asr_disagreement` | 6 | 6 | 100% | Dual-view ingestion: phonetic errors in `raw_asr` resolved via `formatted_text` |
| `code_switched` | 11 | 11 | 100% | Mixed Hindi-English vocabulary (e.g. *chhutti*, *kal subah*, *paisa*) |
| `customer_budget` | 12 | 12 | 100% | Exact currency amounts, discounts, and pricing constraints |
| `draft` | 10 | 10 | 100% | Grounded email/update drafting without inventing details |
| `household` | 8 | 8 | 100% | Everyday reminders, chores, repair contacts, family habits |
| `hypothetical` | 11 | 11 | 100% | Conditional logic (e.g. "if launch is approved by Dev...") |
| `meeting_episode` | 11 | 11 | 100% | Temporal meeting contexts, attendees, and action items |
| `negative_unrelated` | 10 | 10 | 100% | **Explicit abstention**: questions with zero recorded history |
| `preference` | 7 | 7 | 100% | User-specific directives (e.g. email length, formatting) |
| `project_schedule` | 12 | 12 | 100% | Dates, milestone deadlines, and release dependencies |
| `project_update` | 7 | 7 | 100% | Sprint summaries, blockers, and feature status |
| `study` | 5 | 5 | 100% | Academic reading, notes, and research references |
| `travel` | 10 | 10 | 100% | Flights, hotels, transit windows, and packing items |

---

## 3. Key Findings & Architectural Validations

### 3.1 Strict Grounding & Zero-Hallucination Refusal (`negative_unrelated`)
A critical requirement for private spoken memory is that the engine must **never invent facts** when context is absent.
- 10 cases in `negative_unrelated` asked questions about completely unrecorded subjects (e.g. "What is the capital of Peru?", "How do I make sourdough bread?").
- **Result**: 10 / 10 queries returned `status: "insufficient_evidence"` with an empty evidence set (`len(evidence) == 0`).
- **Abstention Precision**: **100%**.

### 3.2 Dual-View Speech Ingestion (`asr_disagreement`)
Voice notes frequently produce noisy phonetics in raw ASR. By maintaining both `raw_asr` and `formatted_text` in SQLite:
- Lexical queries can match speech phonetic patterns without polluting generated answers with disfluent artifacts.
- 6 / 6 ASR disagreement cases retrieved the correct underlying record and verified text presence.

### 3.3 Zero-Overlap Cross-Lingual Retrieval (`code_switched` & Multilingual Smoke)
Spoken interactions in bilingual environments routinely switch between English and Indian languages.
- Tested English query: *"When is my dentist appointment?"* against a Hindi transcript: *"कल सुबह दंत चिकित्सक से मिलने का समय है।"*.
- **Lexical Overlap**: Exactly 0 matching tokens (`lexical_rank: null`).
- **Dense Vector Retrieval**: Rank 1 (`rrf_score: 0.0163934`) via `intfloat/multilingual-e5-small`.
- **Outcome**: Successfully bridges languages without requiring explicit translation steps.

### 3.4 Multi-Hop Entity Reasoning (Knowledge Graph)
When answers require synthesizing facts across distinct conversations:
- The relational graph (SQLite `entities`, `entity_aliases`, `entity_relations`) traverses connections (e.g. Assigned to Project -> Manager of Person).
- Multi-hop bridge expansion retrieves relevant context across multiple disjoint source transcripts.

---

## 4. Latency & Resource Utilization

Benchmarks were measured running locally on standard CPU hardware with SQLite in WAL mode:

| Latency Metric | Milliseconds |
|:---|:---|
| **Minimum Latency** | 68.34 ms |
| **25th Percentile (p25)** | 81.80 ms |
| **50th Percentile (Median / p50)** | **90.13 ms** |
| **75th Percentile (p75)** | 96.68 ms |
| **95th Percentile (p95)** | **112.43 ms** |
| **Maximum Latency** | 292.30 ms |

### Storage Footprint
- **Total Corpus Records**: 500 synthetic spoken transcripts.
- **Passage Chunks & Embeddings**: ~1,500 chunk vectors.
- **SQLite Database Size**: 12.57 MB (including indices, relations, and embeddings).
- **RAM Overhead during query**: < 150 MB CPU memory.

---

## 5. Reproduction Instructions

To run the exact benchmark suite locally:

```powershell
# 1. Run the 120-case reproducible evaluation suite
uv run --project backend python -m kivi_memory.cli evaluate --namespace demo --suite eval/cases.jsonl --output eval/results/benchmark_report.json --offline

# 2. Run the cross-lingual zero-overlap embedding test
uv run --project backend python eval/multilingual_embedding_smoke.py

# 3. Run all backend automated tests
uv run --project backend pytest backend/tests

# 4. Run frontend unit tests
npm --prefix frontend test
```
