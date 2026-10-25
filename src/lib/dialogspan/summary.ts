import { decisionLabel, pairsTouching, turnById } from "./engine";
import type { Analysis, DecisionState } from "./types";

const EMPTY: DecisionState = { status: "pending", mergedTurnIds: [] };

export function formatSummary(analysis: Analysis, decisions: Record<string, DecisionState>) {
  const lines: string[] = [];
  lines.push(`# DialogSpan review — ${analysis.title}`);
  lines.push("");
  lines.push(
    "DialogSpan did not merge any turns on its own. A section marked “Merged by you” only groups the turns you selected. Text is not rewritten. A contradiction is not resolved by a merge.",
  );
  lines.push("");
  const counts = countKinds(analysis);
  lines.push("## Counts");
  lines.push("");
  lines.push(`- Turns: ${analysis.turns.length}`);
  lines.push(`- Spans: ${analysis.spans.length}`);
  lines.push(`- Correlated: ${counts.correlated}`);
  lines.push(`- Contradiction: ${counts.contradiction}`);
  lines.push(`- Inconclusive: ${counts.inconclusive}`);
  lines.push(`- Unlinked turns: ${analysis.unlinked.length}`);
  lines.push("");

  if (!analysis.spans.length) {
    lines.push("No span was formed. Overlap stayed under the link rules, or every turn stood alone.");
    lines.push("");
  }

  for (const span of analysis.spans) {
    const decision = decisions[span.id] ?? EMPTY;
    lines.push(`## ${span.id} · ${labelKind(span.kind)} · evidence ${span.strength.toFixed(2)}`);
    lines.push("");
    lines.push(`Decision: ${decisionLabel(decision.status)}`);
    lines.push("");
    lines.push(span.reasoning);
    lines.push("");
    lines.push(
      `Pairs inside this span: ${span.agreementCount} correlated, ${span.contradictionCount} contradiction, ${span.inconclusiveCount} inconclusive.`,
    );
    lines.push("");
    const included =
      decision.status === "merged" && decision.mergedTurnIds.length >= 2
        ? new Set(decision.mergedTurnIds)
        : null;
    if (included) {
      lines.push("Included in your merge:");
      lines.push("");
    }
    for (const turnId of span.turnIds) {
      const turn = turnById(analysis, turnId);
      if (!turn) continue;
      if (included && !included.has(turnId)) continue;
      lines.push(`### ${turn.agentName}`);
      lines.push("");
      lines.push(turn.text);
      lines.push("");
    }
    if (included) {
      const excluded = span.turnIds.filter((id) => !included.has(id));
      if (excluded.length) {
        lines.push("Left out of the merge:");
        lines.push("");
        for (const turnId of excluded) {
          const turn = turnById(analysis, turnId);
          if (!turn) continue;
          lines.push(`- ${turn.agentName}: ${turn.text}`);
        }
        lines.push("");
      }
      if (span.kind === "contradiction") {
        lines.push("The verdict on this span is still contradiction. The merge does not pick a winner.");
        lines.push("");
      }
      if (span.kind === "inconclusive") {
        lines.push("The verdict on this span is still inconclusive. The merge is your grouping, not new evidence.");
        lines.push("");
      }
    }
    lines.push("Evidence:");
    lines.push("");
    for (const pair of pairsTouching(analysis, span.turnIds)) {
      const a = turnById(analysis, pair.turnA);
      const b = turnById(analysis, pair.turnB);
      lines.push(
        `- ${a?.agentName ?? pair.turnA} ↔ ${b?.agentName ?? pair.turnB}: ${pair.kind}, overlap ${pair.jaccard}, rules ${pair.rules.join(", ") || "—"}. ${pair.reasons.join(" ")}`,
      );
    }
    lines.push("");
  }

  if (analysis.unlinked.length) {
    lines.push("## Unlinked turns");
    lines.push("");
    lines.push("No cross-agent link was strong enough to put these in a span.");
    lines.push("");
    for (const turnId of analysis.unlinked) {
      const turn = turnById(analysis, turnId);
      if (!turn) continue;
      lines.push(`### ${turn.agentName}`);
      lines.push("");
      lines.push(turn.text);
      lines.push("");
      const notes = analysis.notes.filter((note) => note.turnId === turnId);
      for (const note of notes) {
        lines.push(`- Note: ${note.text}`);
      }
      if (notes.length) lines.push("");
    }
  }

  lines.push("## What this file is not");
  lines.push("");
  lines.push(
    "This is not a resolved conversation. Correlated means the overlap rules fired. Contradiction means a conflict rule fired. Inconclusive means the evidence was not enough either way. Unlinked means no relationship was proposed.",
  );
  lines.push("");
  return lines.join("\n");
}

export function countKinds(analysis: Analysis) {
  return {
    correlated: analysis.spans.filter((span) => span.kind === "correlated").length,
    contradiction: analysis.spans.filter((span) => span.kind === "contradiction").length,
    inconclusive: analysis.spans.filter((span) => span.kind === "inconclusive").length,
  };
}

export function labelKind(kind: string) {
  if (kind === "correlated") return "Correlated";
  if (kind === "contradiction") return "Contradiction";
  if (kind === "inconclusive") return "Inconclusive";
  return kind;
}

export function parseUpload(
  filename: string,
  raw: string,
): { title: string; agents: { name: string; text: string }[] } | { error: string } {
  const text = raw.trim();
  if (!text) return { error: "That file is empty." };
  if (filename.endsWith(".json") || text.startsWith("{")) {
    try {
      const data = JSON.parse(text) as {
        title?: string;
        agents?: { name?: string; text?: string; turns?: string[] }[];
      };
      if (!data.agents || !Array.isArray(data.agents)) {
        return { error: "JSON needs an agents array. Each agent needs a name and text or turns." };
      }
      const agents = data.agents
        .map((agent, index) => ({
          name: (agent.name || `Agent ${index + 1}`).trim(),
          text: (agent.text || (agent.turns || []).join("\n\n")).trim(),
        }))
        .filter((agent) => agent.text);
      if (agents.length < 2) return { error: "Need at least two agents with text." };
      if (agents.length > 4) return { error: "Paste and upload support up to 4 agents. Split the file." };
      return { title: data.title?.trim() || filename.replace(/\.[^.]+$/, "") || "Uploaded transcripts", agents };
    } catch {
      return { error: "Could not parse that JSON." };
    }
  }
  const chunks = text.split(/^##\s+/m).map((part) => part.trim()).filter(Boolean);
  if (chunks.length < 2 || !text.includes("##")) {
    return {
      error: "Use JSON, or a text file with one ## heading per agent and a blank line between turns.",
    };
  }
  const agents = chunks.slice(0, 4).map((chunk) => {
    const [first, ...rest] = chunk.split("\n");
    return { name: first.trim(), text: rest.join("\n").trim() };
  });
  const filled = agents.filter((agent) => agent.name && agent.text);
  if (filled.length < 2) return { error: "Need at least two ## agent sections with text." };
  return { title: filename.replace(/\.[^.]+$/, "") || "Uploaded transcripts", agents: filled };
}
