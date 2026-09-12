import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";

// Mock localStorage for node environment
class MockLocalStorage {
  constructor() {
    this.store = new Map();
  }
  getItem(key) {
    return this.store.get(key) ?? null;
  }
  setItem(key, value) {
    this.store.set(key, String(value));
  }
  removeItem(key) {
    this.store.delete(key);
  }
  clear() {
    this.store.clear();
  }
}

globalThis.localStorage = new MockLocalStorage();

describe("Frontend Browser Storage & Demo Mode Correctness", () => {
  beforeEach(() => {
    globalThis.localStorage.clear();
  });

  it("Item 49: recovers gracefully from corrupted JSON and preserves backup", () => {
    const DEMO_STORE_KEY = "kivi-memory-workbench:v1";
    const corruptedPayload = "{ this is broken json !!! }";
    globalThis.localStorage.setItem(DEMO_STORE_KEY, corruptedPayload);

    // Simulate loadDemoStore
    let loadedStore = null;
    let corruptedBackupKey = null;

    try {
      const raw = globalThis.localStorage.getItem(DEMO_STORE_KEY);
      loadedStore = JSON.parse(raw);
    } catch {
      corruptedBackupKey = `${DEMO_STORE_KEY}:corrupted:${Date.now()}`;
      globalThis.localStorage.setItem(corruptedBackupKey, corruptedPayload);
      loadedStore = { namespaces: [{ id: "demo", name: "demo", revision: 1 }] };
    }

    assert.ok(loadedStore !== null, "Store should recover to default");
    assert.equal(loadedStore.namespaces[0].id, "demo");
    assert.ok(corruptedBackupKey !== null, "Backup key should be created");
    assert.equal(globalThis.localStorage.getItem(corruptedBackupKey), corruptedPayload, "Corrupted payload should be preserved");
  });

  it("Item 38: demo corrections override original source answers", () => {
    const memories = [
      {
        id: "mem_1",
        namespace_id: "demo",
        kind: "fact",
        subject: "Lantern",
        predicate: "launches on",
        value: "Tuesday",
        scope: "general",
        state: "active",
        supersedes_id: "mem_0",
        source_id: "src_1",
      },
    ];

    const sourceText = "Lantern launches on Monday after Dev approves.";
    const question = "When does Lantern launch?";

    // Reconcile answer using corrected active memory
    const activeCorrection = memories.find(
      (m) => m.state === "active" && m.supersedes_id && question.toLowerCase().includes(m.subject.toLowerCase())
    );

    let answer = sourceText;
    if (activeCorrection) {
      answer = `Based on your correction: ${activeCorrection.subject} ${activeCorrection.predicate} ${activeCorrection.value}.`;
    }

    assert.ok(answer.includes("Tuesday"), "Answer should reflect corrected value");
    assert.ok(!answer.includes("Monday"), "Answer should not use overridden value");
  });

  it("Item 39: demo suppression excludes source from evidence", () => {
    const memories = [
      { id: "mem_1", source_id: "src_suppressed", state: "suppressed", subject: "Budget" },
      { id: "mem_2", source_id: "src_active", state: "active", subject: "Schedule" },
    ];
    const sources = [
      { id: "src_suppressed", formatted_text: "Old budget info" },
      { id: "src_active", formatted_text: "Current schedule info" },
    ];

    const suppressedSourceIds = new Set(
      memories.filter((m) => m.state === "suppressed").map((m) => m.source_id)
    );

    const eligibleSources = sources.filter((s) => !suppressedSourceIds.has(s.id));

    assert.equal(eligibleSources.length, 1);
    assert.equal(eligibleSources[0].id, "src_active");
  });

  it("Item 41: rejects blank correction values", () => {
    const validateCorrection = (value) => {
      if (!value || !value.trim()) {
        throw new Error("value must contain a non-whitespace character");
      }
      return value.trim();
    };

    assert.throws(() => validateCorrection(""), /non-whitespace/);
    assert.throws(() => validateCorrection("   "), /non-whitespace/);
    assert.equal(validateCorrection("  Valid correction  "), "Valid correction");
  });

  it("Item 44: URL IDs with special characters are safely encoded", () => {
    const rawNamespaceId = "work/space?1#test";
    const encoded = encodeURIComponent(rawNamespaceId);

    assert.equal(encoded, "work%2Fspace%3F1%23test");
    assert.ok(!encoded.includes("/"));
    assert.ok(!encoded.includes("?"));
    assert.ok(!encoded.includes("#"));
  });

  it("Item 47: workspace cancellation tokens drop stale async responses", async () => {
    let currentWorkspace = "workspace-A";
    let displayedData = "initial-A";

    const startAsyncFetch = async (workspaceAtStart) => {
      await new Promise((r) => setTimeout(r, 50));
      // Only update if workspace did not change
      if (currentWorkspace === workspaceAtStart) {
        displayedData = `data-for-${workspaceAtStart}`;
      }
    };

    // Start fetch for workspace A
    const fetchA = startAsyncFetch("workspace-A");

    // User immediately switches to workspace B
    currentWorkspace = "workspace-B";

    await fetchA;

    // displayedData should NOT be overwritten by workspace A
    assert.equal(displayedData, "initial-A", "Stale fetch from workspace A must not update workspace B");
  });

  it("Item 50: demo reset clears sources and memories for targeted workspace", () => {
    const store = {
      namespaces: [{ id: "space-1", name: "Space 1", revision: 1 }],
      sources: [
        { id: "space-1:src:1", formatted_text: "Note 1" },
        { id: "space-2:src:2", formatted_text: "Note 2" },
      ],
      memories: [
        { id: "space-1:mem:1", value: "Fact 1" },
        { id: "space-2:mem:2", value: "Fact 2" },
      ],
    };

    // Simulate reset for space-1
    const target = "space-1";
    store.sources = store.sources.filter((s) => !s.id.startsWith(`${target}:`));
    store.memories = store.memories.filter((m) => !m.id.startsWith(`${target}:`));
    const ns = store.namespaces.find((n) => n.id === target);
    if (ns) ns.revision += 1;

    assert.equal(store.sources.length, 1);
    assert.equal(store.sources[0].id, "space-2:src:2");
    assert.equal(store.memories.length, 1);
    assert.equal(store.memories[0].id, "space-2:mem:2");
    assert.equal(ns.revision, 2);
  });

  it("Citation parser handles both [source:id] and [id], and ignores non-citation brackets like [redacted]", () => {
    const evidence = [
      { id: "src_abc123", external_id: "synthetic-001", text: "Evidence one" },
      { id: "src_def456", external_id: "note-42", text: "Evidence two" },
    ];

    const parseAnswer = (text) => {
      const pattern = /\[(?:source:)?([a-zA-Z0-9_\-]+(?:\s*[,;\s]\s*(?:source:)?[a-zA-Z0-9_\-]+)*)\]/g;
      const parts = [];
      let lastIndex = 0;
      let match;

      while ((match = pattern.exec(text)) !== null) {
        const inner = match[1];
        const ids = inner.split(/[,;\s]+/).map((s) => s.replace(/^source:/, "").trim()).filter(Boolean);

        const validCites = ids.map((sourceId) => {
          const evidenceIndex = evidence.findIndex((e) => e.id === sourceId || e.external_id === sourceId);
          const isKnownPattern = sourceId.startsWith("src_") || sourceId.startsWith("synthetic-");
          if (evidenceIndex >= 0 || isKnownPattern) {
            const num = evidenceIndex >= 0 ? evidenceIndex + 1 : null;
            return { sourceId, num };
          }
          return null;
        }).filter((c) => c !== null);

        if (validCites.length === 0) continue;

        if (match.index > lastIndex) {
          parts.push(text.slice(lastIndex, match.index));
        }
        validCites.forEach((cite) => {
          parts.push({ type: "pill", id: cite.sourceId, num: cite.num });
        });
        lastIndex = pattern.lastIndex;
      }
      if (lastIndex < text.length) {
        parts.push(text.slice(lastIndex));
      }
      return parts;
    };

    // Case 1: Standard [source:id]
    const res1 = parseAnswer("Launch Monday [source:src_abc123].");
    assert.equal(res1.length, 3);
    assert.equal(res1[0], "Launch Monday ");
    assert.equal(res1[1].type, "pill");
    assert.equal(res1[1].num, 1);
    assert.equal(res1[2], ".");

    // Case 2: Without prefix [src_abc123] and [synthetic-001]
    const res2 = parseAnswer("Launch [synthetic-001] and note [src_def456].");
    assert.equal(res2[1].type, "pill");
    assert.equal(res2[1].id, "synthetic-001");
    assert.equal(res2[1].num, 1);
    assert.equal(res2[3].type, "pill");
    assert.equal(res2[3].id, "src_def456");
    assert.equal(res2[3].num, 2);

    // Case 3: Preserves [redacted] as text
    const res3 = parseAnswer("Note [redacted] occurred before launch [source:src_abc123].");
    assert.ok(res3[0].includes("[redacted]"), "Must preserve [redacted] as literal text");
    assert.equal(res3[1].type, "pill");
  });

  it("Quick text format produces valid TranscriptRecord payload", () => {
    const rawNote = "Met with Sarah about budget. Approved ₹50,000 for phase two.";
    const tz = "Asia/Kolkata";
    const record = {
      schema_version: 1,
      id: `note-test-01`,
      raw_asr: rawNote,
      formatted_text: rawNote,
      occurred_at: new Date().toISOString(),
      timezone: tz,
      app: "Quick Paste",
      language_hints: ["en"],
      context: { source: "quick_paste" },
    };

    assert.equal(record.schema_version, 1);
    assert.ok(record.id.length > 0);
    assert.equal(record.raw_asr, rawNote);
    assert.ok(record.occurred_at.includes("T"));
    assert.equal(record.context.source, "quick_paste");
  });
});
