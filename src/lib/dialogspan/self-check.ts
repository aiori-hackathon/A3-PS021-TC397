import { analyze } from "./engine.ts";
import { scenarios } from "./scenarios.ts";

const failures: string[] = [];

function fail(id: string, message: string) {
  failures.push(`${id}: ${message}`);
}

for (const scenario of scenarios) {
  const analysis = analyze({
    title: scenario.title,
    agents: scenario.agents.map((agent) => ({ name: agent.name, turns: agent.turns })),
  });
  console.log("\n==", scenario.id, "==");
  console.log(
    "spans:",
    analysis.spans.map((span) => `${span.id} ${span.kind} [${span.turnIds.join(",")}] ${span.strength}`),
  );
  console.log("unlinked:", analysis.unlinked);
  for (const pair of analysis.pairs) {
    if (pair.kind === "none") continue;
    console.log(
      `  ${pair.kind} ${pair.turnA}~${pair.turnB} j=${pair.jaccard} s=${pair.strength} rules=${pair.rules.join(",")} :: ${pair.reasons[0]}`,
    );
  }
  for (const note of analysis.notes) console.log("  note", note.turnId, note.text.slice(0, 140));

  if (scenario.id === "order-4821") {
    const span = analysis.spans.find((item) => item.kind === "correlated");
    if (!span) fail(scenario.id, "missing correlated span");
    else {
      const names = span.turnIds.map((id) => analysis.turns.find((t) => t.id === id)?.agentName);
      if (!names.some((n) => n?.startsWith("Aria")) || !names.some((n) => n?.startsWith("Bram"))) {
        fail(scenario.id, `correlated span agents ${names.join("|")}`);
      }
      if (names.some((n) => n?.startsWith("Cleo"))) fail(scenario.id, "Cleo was joined");
    }
    if (!analysis.unlinked.some((id) => analysis.turns.find((t) => t.id === id)?.agentName.startsWith("Cleo"))) {
      fail(scenario.id, "Cleo should be unlinked");
    }
  }

  if (scenario.id === "refund-window") {
    if (analysis.spans.length !== 1) fail(scenario.id, `span count ${analysis.spans.length}`);
    if (analysis.spans[0]?.kind !== "contradiction") fail(scenario.id, `kind ${analysis.spans[0]?.kind}`);
    if (analysis.spans[0]?.turnIds.length !== 3) fail(scenario.id, "expected 3 turns");
    const agree = analysis.pairs.find(
      (pair) =>
        pair.kind === "correlated" &&
        [pair.turnA, pair.turnB].every((id) =>
          ["North", "East"].some((name) => analysis.turns.find((t) => t.id === id)?.agentName.startsWith(name)),
        ),
    );
    if (!agree) fail(scenario.id, "North-East should correlate");
  }

  if (scenario.id === "clinic-thursday") {
    const span = analysis.spans.find((item) => item.turnIds.length >= 2);
    if (!span || span.kind !== "inconclusive") fail(scenario.id, `span ${span?.kind}`);
    if (span?.turnIds.some((id) => analysis.turns.find((t) => t.id === id)?.agentName.startsWith("Lila"))) {
      fail(scenario.id, "Lila joined");
    }
    if (!analysis.unlinked.some((id) => analysis.turns.find((t) => t.id === id)?.agentName.startsWith("Lila"))) {
      fail(scenario.id, "Lila should be unlinked");
    }
  }

  if (scenario.id === "payments-incident") {
    const span = analysis.spans.find((item) => item.kind === "contradiction");
    if (!span) fail(scenario.id, "missing contradiction");
    const agents = new Set(span?.turnIds.map((id) => analysis.turns.find((t) => t.id === id)?.agentName));
    for (const name of ["Sentinel", "Relay", "Ledger"]) {
      if (![...agents].some((agent) => agent === name)) fail(scenario.id, `missing ${name} in ${[...agents].join(",")}`);
    }
    if (!analysis.pairs.some((pair) => pair.kind === "correlated")) fail(scenario.id, "expected an agreeing pair");
  }

  if (scenario.id === "booking-hk204") {
    const span = analysis.spans.find((item) => item.kind === "contradiction");
    if (!span) fail(scenario.id, "missing contradiction span");
    const names = span?.turnIds.map((id) => analysis.turns.find((t) => t.id === id)?.agentName) ?? [];
    for (const name of ["Neel", "Rhea", "Anya"]) {
      if (!names.some((n) => n?.startsWith(name))) fail(scenario.id, `missing ${name}`);
    }
    if (names.some((n) => n?.startsWith("Om"))) fail(scenario.id, "Om joined the rate span");
  }
}

if (failures.length) {
  console.error("\nFAILURES\n" + failures.join("\n"));
  process.exit(1);
}
console.log("\nAll scenario checks passed.");
