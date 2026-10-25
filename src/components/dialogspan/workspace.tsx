import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  Download,
  GitMerge,
  Plus,
  Trash2,
  Unlink,
  Upload,
} from "lucide-react";
import {
  analyze,
  decisionLabel,
  evidenceBand,
  marksForTurn,
  pairsTouching,
  splitTurns,
  turnById,
} from "@/lib/dialogspan/engine";
import { scenarioById, scenarios } from "@/lib/dialogspan/scenarios";
import { countKinds, formatSummary, labelKind, parseUpload } from "@/lib/dialogspan/summary";
import type { AgentDraft, Analysis, DecisionState, RelationKind, Span } from "@/lib/dialogspan/types";
import { Frame, Marked } from "./frame";

const STORAGE_KEY = "dialogspan-review-v1";

type Tab = "spans" | "compare" | "summary";
type Mode = "lab" | "paste";

type Persisted = {
  analysis: Analysis;
  decisions: Record<string, DecisionState>;
  tab: Tab;
  mode: Mode;
  scenarioId: string;
  agents: AgentDraft[];
  customTitle: string;
};

const focus =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sage";
const btn = `inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${focus}`;
const btnPrimary = `${btn} bg-sage text-bg hover:bg-sage/90`;
const btnQuiet = `${btn} border border-line bg-raised text-fg hover:border-sage`;
const btnOn = `${btn} border border-sage bg-sage-dim text-sage`;
const field =
  "w-full rounded-lg border border-line bg-raised px-3 py-2 text-sm text-fg outline-none placeholder:text-muted focus:border-sage";

const kindTone: Record<RelationKind, string> = {
  correlated: "bg-sage-dim text-sage",
  contradiction: "bg-clay-dim text-clay",
  inconclusive: "bg-ochre-dim text-ochre",
};

const kindLine: Record<RelationKind, string> = {
  correlated: "border-sage",
  contradiction: "border-clay",
  inconclusive: "border-ochre",
};

function draftsFrom(agents: { name: string; turns: string[] }[]): AgentDraft[] {
  return agents.map((agent, index) => ({
    id: `a${index + 1}`,
    name: agent.name,
    text: agent.turns.join("\n\n"),
  }));
}

const opening = scenarioById("refund-window");

export function Workspace() {
  const [mode, setMode] = useState<Mode>("lab");
  const [scenarioId, setScenarioId] = useState(opening.id);
  const [agents, setAgents] = useState<AgentDraft[]>(() => draftsFrom(opening.agents));
  const [customTitle, setCustomTitle] = useState("Custom transcripts");
  const [analysis, setAnalysis] = useState<Analysis | null>(() =>
    analyze({ title: opening.title, agents: opening.agents }),
  );
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({});
  const [tab, setTab] = useState<Tab>("spans");
  const [selectedId, setSelectedId] = useState("S1");
  const [mergeSpan, setMergeSpan] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState<Persisted | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Persisted;
      if (parsed?.analysis?.spans && parsed.analysis.turns) setSaved(parsed);
    } catch {
      /* ignore broken storage */
    }
  }, []);

  function snapshot(next: Partial<Persisted> & { analysis: Analysis }) {
    const payload: Persisted = {
      analysis: next.analysis,
      decisions: next.decisions ?? decisions,
      tab: next.tab ?? tab,
      mode: next.mode ?? mode,
      scenarioId: next.scenarioId ?? scenarioId,
      agents: next.agents ?? agents,
      customTitle: next.customTitle ?? customTitle,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      /* ignore quota */
    }
    setSaved(null);
  }

  function openLab(id: string) {
    const scenario = scenarioById(id);
    const nextAgents = draftsFrom(scenario.agents);
    const next = analyze({ title: scenario.title, agents: scenario.agents });
    setScenarioId(id);
    setMode("lab");
    setAgents(nextAgents);
    setAnalysis(next);
    setDecisions({});
    setSelectedId(next.spans[0]?.id ?? "");
    setTab("spans");
    setMergeSpan(null);
    setPicked([]);
    setError(null);
    snapshot({
      analysis: next,
      decisions: {},
      tab: "spans",
      mode: "lab",
      scenarioId: id,
      agents: nextAgents,
    });
  }

  function runPaste() {
    const filled = agents
      .map((agent) => ({
        name: agent.name.trim() || "Unnamed agent",
        turns: splitTurns(agent.text),
      }))
      .filter((agent) => agent.turns.length > 0);
    if (filled.length < 2) {
      setError("Add text for at least two agents. Separate turns with a blank line.");
      return;
    }
    const next = analyze({ title: customTitle.trim() || "Custom transcripts", agents: filled });
    setAnalysis(next);
    setDecisions({});
    setSelectedId(next.spans[0]?.id ?? "");
    setTab("spans");
    setMergeSpan(null);
    setPicked([]);
    setError(null);
    setMode("paste");
    snapshot({
      analysis: next,
      decisions: {},
      tab: "spans",
      mode: "paste",
      agents,
      customTitle,
    });
  }

  function onUpload(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      const parsed = parseUpload(file.name, String(reader.result ?? ""));
      if ("error" in parsed) {
        setError(parsed.error);
        return;
      }
      const nextAgents = draftsFrom(
        parsed.agents.map((agent) => ({ name: agent.name, turns: splitTurns(agent.text) })),
      );
      setAgents(nextAgents);
      setCustomTitle(parsed.title);
      setMode("paste");
      const filled = nextAgents
        .map((agent) => ({ name: agent.name, turns: splitTurns(agent.text) }))
        .filter((agent) => agent.turns.length > 0);
      const next = analyze({ title: parsed.title, agents: filled });
      setAnalysis(next);
      setDecisions({});
      setSelectedId(next.spans[0]?.id ?? "");
      setTab("spans");
      setError(null);
      snapshot({
        analysis: next,
        decisions: {},
        tab: "spans",
        mode: "paste",
        agents: nextAgents,
        customTitle: parsed.title,
      });
    };
    reader.readAsText(file);
  }

  function setDecision(spanId: string, decision: DecisionState) {
    const next = { ...decisions, [spanId]: decision };
    setDecisions(next);
    if (analysis) snapshot({ analysis, decisions: next });
  }

  function restore() {
    if (!saved) return;
    setAnalysis(saved.analysis);
    setDecisions(saved.decisions ?? {});
    setTab(saved.tab ?? "spans");
    setMode(saved.mode ?? "paste");
    setScenarioId(saved.scenarioId ?? opening.id);
    if (saved.agents?.length) setAgents(saved.agents);
    if (saved.customTitle) setCustomTitle(saved.customTitle);
    setSelectedId(saved.analysis.spans[0]?.id ?? "");
    setMergeSpan(null);
    setSaved(null);
  }

  const scenario = scenarioById(scenarioId);

  return (
    <Frame>
      <div className="w-full px-4 py-6 sm:px-6 lg:px-8">
        <p className="max-w-3xl font-serif text-2xl leading-snug text-fg sm:text-3xl">
          Collect turns. Correlate with evidence. Show the relationship. You decide.
        </p>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-muted">
          DialogSpan never merges a conversation by itself. If the rules cannot support a link, the
          result stays inconclusive or unlinked. A shared id is not a relationship.
        </p>

        {saved ? (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3">
            <p className="text-sm text-fg">A previous review is saved in this browser.</p>
            <button type="button" className={btnQuiet} onClick={restore}>
              Restore it
            </button>
            <button
              type="button"
              className={btnQuiet}
              onClick={() => {
                localStorage.removeItem(STORAGE_KEY);
                setSaved(null);
              }}
            >
              Discard
            </button>
          </div>
        ) : null}

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(17rem,22rem)_minmax(0,1fr)]">
          <section className="order-2 rounded-xl border border-line bg-surface lg:order-1">
            <div className="flex border-b border-line" role="tablist" aria-label="Input">
              <button
                type="button"
                role="tab"
                aria-selected={mode === "lab"}
                className={`min-h-11 flex-1 px-3 text-sm ${mode === "lab" ? "text-fg" : "text-muted"} ${focus}`}
                onClick={() => setMode("lab")}
              >
                Lab
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === "paste"}
                className={`min-h-11 flex-1 px-3 text-sm ${mode === "paste" ? "text-fg" : "text-muted"} ${focus}`}
                onClick={() => setMode("paste")}
              >
                Paste
              </button>
            </div>

            {mode === "lab" ? (
              <div className="flex flex-col gap-2 p-3">
                <p className="px-1 text-xs leading-relaxed text-muted">
                  Five fixed transcripts. The same rules run on these as on anything you paste.
                </p>
                {scenarios.map((item) => {
                  const on = item.id === scenarioId && analysis?.title === item.title;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => openLab(item.id)}
                      className={`rounded-lg border px-3 py-3 text-left ${focus} ${
                        on ? "border-sage bg-sage-dim" : "border-line bg-raised hover:border-sage"
                      }`}
                    >
                      <span className="block text-sm text-fg">{item.title}</span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted">{item.kicker}</span>
                    </button>
                  );
                })}
                <div className="rounded-lg border border-line px-3 py-3">
                  <p className="text-xs leading-relaxed text-muted">{scenario.situation}</p>
                  <p className="mt-2 text-xs leading-relaxed text-fg">{scenario.shows}</p>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-4 p-3">
                <label className="block">
                  <span className="mb-1 block text-xs text-muted">Review title</span>
                  <input
                    className={field}
                    value={customTitle}
                    onChange={(event) => setCustomTitle(event.target.value)}
                  />
                </label>
                {agents.map((agent, index) => (
                  <div key={agent.id} className="rounded-lg border border-line p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <label className="block flex-1">
                        <span className="sr-only">Agent name</span>
                        <input
                          className={field}
                          value={agent.name}
                          aria-label={`Agent ${index + 1} name`}
                          onChange={(event) =>
                            setAgents((current) =>
                              current.map((item) =>
                                item.id === agent.id ? { ...item, name: event.target.value } : item,
                              ),
                            )
                          }
                        />
                      </label>
                      {agents.length > 2 ? (
                        <button
                          type="button"
                          className={`${btnQuiet} px-3`}
                          aria-label={`Remove ${agent.name || "agent"}`}
                          onClick={() => setAgents((current) => current.filter((item) => item.id !== agent.id))}
                        >
                          <Trash2 className="size-4" aria-hidden="true" />
                        </button>
                      ) : null}
                    </div>
                    <textarea
                      className={`${field} min-h-28 leading-relaxed`}
                      value={agent.text}
                      placeholder="One turn. A blank line starts the next turn."
                      aria-label={`${agent.name || "Agent"} transcript`}
                      onChange={(event) =>
                        setAgents((current) =>
                          current.map((item) =>
                            item.id === agent.id ? { ...item, text: event.target.value } : item,
                          ),
                        )
                      }
                    />
                  </div>
                ))}
                <div className="flex flex-wrap gap-2">
                  {agents.length < 4 ? (
                    <button
                      type="button"
                      className={btnQuiet}
                      onClick={() =>
                        setAgents((current) => [
                          ...current,
                          {
                            id: `agent-${current.length + 1}-${Date.now()}`,
                            name: `Agent ${String.fromCharCode(65 + current.length)}`,
                            text: "",
                          },
                        ])
                      }
                    >
                      <Plus className="size-4" aria-hidden="true" />
                      Add agent
                    </button>
                  ) : (
                    <p className="text-xs text-muted">Four agents is the paste limit.</p>
                  )}
                  <button type="button" className={btnQuiet} onClick={() => fileRef.current?.click()}>
                    <Upload className="size-4" aria-hidden="true" />
                    Upload
                  </button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".json,.txt,.md,application/json,text/plain"
                    className="sr-only"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) onUpload(file);
                      event.target.value = "";
                    }}
                  />
                </div>
                <button type="button" className={btnPrimary} onClick={runPaste}>
                  Correlate these turns
                </button>
                <p className="text-xs leading-relaxed text-muted">
                  JSON: {`{ "agents": [ { "name": "Aria", "turns": ["..."] } ] }`}. Text: one ## heading per agent.
                </p>
              </div>
            )}
            {error ? (
              <p className="px-4 pb-4 text-sm text-clay" role="alert">
                {error}
              </p>
            ) : null}
          </section>

          <section className="order-1 min-w-0 lg:order-2">
            {analysis ? (
              <Results
                analysis={analysis}
                decisions={decisions}
                tab={tab}
                setTab={(next) => {
                  setTab(next);
                  snapshot({ analysis, tab: next });
                }}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                mergeSpan={mergeSpan}
                setMergeSpan={setMergeSpan}
                picked={picked}
                setPicked={setPicked}
                setDecision={setDecision}
                copied={copied}
                setCopied={setCopied}
                onClear={() => {
                  setAnalysis(null);
                  setDecisions({});
                  setMergeSpan(null);
                }}
              />
            ) : (
              <Intro onOpen={() => openLab("refund-window")} />
            )}
          </section>
        </div>
      </div>
    </Frame>
  );
}

function Intro({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <h2 className="font-serif text-2xl text-fg">Three outcomes, and no fourth guess</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Outcome
          kind="correlated"
          title="Correlated"
          body="The overlap rules fired and no conflict rule did. That is evidence of a shared topic, not proof the text is true."
        />
        <Outcome
          kind="contradiction"
          title="Contradiction"
          body="A negation, a different number, or a listed antonym fired on overlapping talk. Agreement elsewhere does not cancel it."
        />
        <Outcome
          kind="inconclusive"
          title="Inconclusive"
          body="Something overlaps — an id, a name, a few words — but not enough to call it the same claim or a conflict."
        />
      </div>
      <p className="mt-4 text-sm leading-relaxed text-muted">
        Turns with nothing to link stay unlinked. They are not silently folded into a nearby span.
      </p>
      <button type="button" className={`${btnPrimary} mt-4`} onClick={onOpen}>
        Open the refund-window lab
      </button>
    </div>
  );
}

function Outcome({ kind, title, body }: { kind: RelationKind; title: string; body: string }) {
  return (
    <article className="rounded-lg border border-line bg-raised p-3">
      <KindBadge kind={kind} />
      <h3 className="mt-3 text-sm font-medium text-fg">{title}</h3>
      <p className="mt-2 text-xs leading-relaxed text-muted">{body}</p>
    </article>
  );
}

function Results({
  analysis,
  decisions,
  tab,
  setTab,
  selectedId,
  setSelectedId,
  mergeSpan,
  setMergeSpan,
  picked,
  setPicked,
  setDecision,
  copied,
  setCopied,
  onClear,
}: {
  analysis: Analysis;
  decisions: Record<string, DecisionState>;
  tab: Tab;
  setTab: (tab: Tab) => void;
  selectedId: string;
  setSelectedId: (id: string) => void;
  mergeSpan: string | null;
  setMergeSpan: (id: string | null) => void;
  picked: string[];
  setPicked: (ids: string[]) => void;
  setDecision: (spanId: string, decision: DecisionState) => void;
  copied: boolean;
  setCopied: (value: boolean) => void;
  onClear: () => void;
}) {
  const counts = countKinds(analysis);
  const selected = analysis.spans.find((span) => span.id === selectedId) ?? analysis.spans[0] ?? null;
  const tabs: { id: Tab; label: string }[] = [
    { id: "spans", label: "Spans" },
    { id: "compare", label: "Compare" },
    { id: "summary", label: "Summary" },
  ];

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-xs text-muted">Review</p>
          <h2 className="font-serif text-2xl text-fg">{analysis.title}</h2>
        </div>
        <button type="button" className={btnQuiet} onClick={onClear}>
          Clear review
        </button>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Turns" value={String(analysis.turns.length)} />
        <Stat label="Correlated" value={String(counts.correlated)} tone="text-sage" />
        <Stat label="Contradiction" value={String(counts.contradiction)} tone="text-clay" />
        <Stat label="Inconclusive" value={String(counts.inconclusive)} tone="text-ochre" />
      </dl>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        {analysis.unlinked.length
          ? `${analysis.unlinked.length} turn${analysis.unlinked.length === 1 ? "" : "s"} left unlinked.`
          : "Every turn sits in a span."}{" "}
        Evidence strength is how hard a rule fired. It is not a probability that the claim is true.
      </p>

      <div className="mt-4 flex gap-1 border-b border-line" role="tablist" aria-label="Review views">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`min-h-11 px-3 text-sm ${tab === item.id ? "border-b border-sage text-fg" : "text-muted"} ${focus}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="mt-4" role="tabpanel">
        {tab === "spans" ? (
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="flex flex-col gap-4 lg:col-span-2">
              <Legend />
              {analysis.spans.length === 0 ? (
                <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">
                  No span. Nothing crossed the link threshold. The turns below stay separate.
                </p>
              ) : null}
              {analysis.spans.map((span) => (
                <SpanCard
                  key={span.id}
                  analysis={analysis}
                  span={span}
                  decision={decisions[span.id] ?? { status: "pending", mergedTurnIds: [] }}
                  selected={selected?.id === span.id}
                  merging={mergeSpan === span.id}
                  picked={picked}
                  onSelect={() => setSelectedId(span.id)}
                  onKeep={() => {
                    setMergeSpan(null);
                    setDecision(span.id, { status: "kept-separate", mergedTurnIds: [] });
                  }}
                  onReview={() => {
                    setMergeSpan(null);
                    setDecision(span.id, { status: "reviewed", mergedTurnIds: [] });
                  }}
                  onStartMerge={() => {
                    setSelectedId(span.id);
                    setMergeSpan(span.id);
                    setPicked(span.turnIds);
                  }}
                  onToggle={(turnId) =>
                    setPicked(picked.includes(turnId) ? picked.filter((id) => id !== turnId) : [...picked, turnId])
                  }
                  onCancelMerge={() => setMergeSpan(null)}
                  onConfirmMerge={() => {
                    setDecision(span.id, { status: "merged", mergedTurnIds: picked });
                    setMergeSpan(null);
                  }}
                />
              ))}
              {analysis.unlinked.length > 0 ? (
                <div className="rounded-xl border border-line bg-surface">
                  <header className="border-b border-line px-4 py-3">
                    <h3 className="text-sm font-medium text-fg">Unlinked</h3>
                    <p className="mt-1 text-xs leading-relaxed text-muted">
                      No cross-agent relationship was proposed. This is not a quiet agreement.
                    </p>
                  </header>
                  <ul className="divide-y divide-line">
                    {analysis.unlinked.map((turnId) => {
                      const turn = turnById(analysis, turnId);
                      if (!turn) return null;
                      const notes = analysis.notes.filter((note) => note.turnId === turnId);
                      return (
                        <li key={turnId} className="px-4 py-3">
                          <p className="font-mono text-xs text-muted">{turn.agentName}</p>
                          <p className="mt-2 text-sm leading-relaxed text-fg">{turn.text}</p>
                          {notes.map((note) => (
                            <p key={note.text} className="mt-2 text-xs leading-relaxed text-ochre">
                              {note.text}
                            </p>
                          ))}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}
            </div>
            <Evidence analysis={analysis} span={selected} />
          </div>
        ) : null}

        {tab === "compare" ? (
          <Compare
            analysis={analysis}
            onSelect={(id) => {
              setSelectedId(id);
              setTab("spans");
            }}
          />
        ) : null}

        {tab === "summary" ? (
          <Summary
            analysis={analysis}
            decisions={decisions}
            copied={copied}
            setCopied={setCopied}
          />
        ) : null}
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "text-fg" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`font-mono text-lg tabular-nums ${tone}`}>{value}</dd>
    </div>
  );
}

function Legend() {
  return (
    <p className="text-xs leading-relaxed text-muted">
      <span className="text-sage">Sage</span> marks terms behind an agreement.{" "}
      <span className="text-clay">Clay</span> marks a conflict. <span className="text-ochre">Ochre</span> marks
      overlap that was not enough to decide.
    </p>
  );
}

function KindBadge({ kind }: { kind: RelationKind }) {
  return (
    <span className={`inline-flex rounded-md px-2 py-1 font-mono text-xs ${kindTone[kind]}`}>
      {labelKind(kind)}
    </span>
  );
}

function SpanCard({
  analysis,
  span,
  decision,
  selected,
  merging,
  picked,
  onSelect,
  onKeep,
  onReview,
  onStartMerge,
  onToggle,
  onCancelMerge,
  onConfirmMerge,
}: {
  analysis: Analysis;
  span: Span;
  decision: DecisionState;
  selected: boolean;
  merging: boolean;
  picked: string[];
  onSelect: () => void;
  onKeep: () => void;
  onReview: () => void;
  onStartMerge: () => void;
  onToggle: (id: string) => void;
  onCancelMerge: () => void;
  onConfirmMerge: () => void;
}) {
  const band = evidenceBand(span.kind, span.strength);
  return (
    <article
      className={`rounded-xl border bg-surface ${selected ? kindLine[span.kind] : "border-line"}`}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <button type="button" className={`${focus} rounded-md`} onClick={onSelect}>
          <KindBadge kind={span.kind} />
        </button>
        <span className="font-mono text-xs text-muted">{span.id}</span>
        <span className="ml-auto font-mono text-xs tabular-nums text-muted">
          {span.strength.toFixed(2)} · {band}
        </span>
      </header>
      <div className="px-4 py-3">
        <p className="text-sm leading-relaxed text-fg">{span.reasoning}</p>
        <p className="mt-2 font-mono text-xs text-muted">
          {span.agreementCount} agree · {span.contradictionCount} conflict · {span.inconclusiveCount} thin
        </p>
      </div>
      <ul className="divide-y divide-line border-t border-line">
        {span.turnIds.map((turnId) => {
          const turn = turnById(analysis, turnId);
          if (!turn) return null;
          const multiple = analysis.turns.filter((item) => item.agentId === turn.agentId).length > 1;
          return (
            <li key={turnId} className="px-4 py-3">
              <div className="mb-2 flex items-center gap-3">
                {merging ? (
                  <label className="flex min-h-11 items-center gap-2 text-xs text-muted">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={picked.includes(turnId)}
                      onChange={() => onToggle(turnId)}
                    />
                    Include
                  </label>
                ) : null}
                <span className="font-mono text-xs text-muted">
                  {turn.agentName}
                  {multiple ? ` · turn ${turn.index + 1}` : ""}
                </span>
              </div>
              <Marked text={turn.text} marks={marksForTurn(analysis, span, turnId)} />
            </li>
          );
        })}
      </ul>
      <footer className="flex flex-col gap-2 border-t border-line px-4 py-3">
        <p className="text-xs text-muted">{decisionLabel(decision.status)}</p>
        {decision.status === "merged" ? (
          <p className="text-xs leading-relaxed text-muted">
            You grouped {decision.mergedTurnIds.length} turns. The wording is unchanged
            {span.kind === "contradiction" ? ", and the contradiction is still the verdict." : "."}
          </p>
        ) : null}
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <button
            type="button"
            className={decision.status === "kept-separate" ? btnOn : btnQuiet}
            aria-pressed={decision.status === "kept-separate"}
            onClick={onKeep}
          >
            <Unlink className="size-4" aria-hidden="true" />
            Keep separate
          </button>
          <button type="button" className={merging ? btnOn : btnQuiet} onClick={onStartMerge}>
            <GitMerge className="size-4" aria-hidden="true" />
            Merge selected
          </button>
          <button
            type="button"
            className={decision.status === "reviewed" ? btnOn : btnQuiet}
            aria-pressed={decision.status === "reviewed"}
            onClick={onReview}
          >
            <Check className="size-4" aria-hidden="true" />
            Mark reviewed
          </button>
        </div>
        {merging ? (
          <div className="rounded-lg border border-line bg-raised p-3">
            <p className="text-sm leading-relaxed text-fg">
              A merge only groups the turns you tick. DialogSpan will not rewrite them.
            </p>
            {span.kind !== "correlated" ? (
              <p className="mt-2 text-sm leading-relaxed text-clay">
                This span is {labelKind(span.kind).toLowerCase()}. Merging does not decide who is right and
                does not create new evidence.
              </p>
            ) : null}
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className={btnPrimary}
                disabled={picked.length < 2}
                onClick={onConfirmMerge}
              >
                Confirm merge of {picked.length}
              </button>
              <button type="button" className={btnQuiet} onClick={onCancelMerge}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </footer>
    </article>
  );
}

function Evidence({ analysis, span }: { analysis: Analysis; span: Span | null }) {
  return (
    <aside className="h-fit rounded-xl border border-line bg-surface p-4 lg:sticky lg:top-20">
      <h3 className="font-serif text-lg text-fg">Evidence</h3>
      {!span ? (
        <p className="mt-2 text-sm text-muted">No span is selected.</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <KindBadge kind={span.kind} />
            <span className="font-mono text-xs text-muted">{span.id}</span>
          </div>
          <p className="mt-3 text-xs leading-relaxed text-muted">
            Strength {span.strength.toFixed(2)} ({evidenceBand(span.kind, span.strength)}). This number is the
            rule score, not confidence that the world matches the text.
          </p>
          <ul className="mt-4 flex flex-col gap-3">
            {pairsTouching(analysis, span.turnIds).map((pair) => {
              const a = turnById(analysis, pair.turnA);
              const b = turnById(analysis, pair.turnB);
              return (
                <li key={`${pair.turnA}-${pair.turnB}`} className="border-t border-line pt-3">
                  <p className="text-sm text-fg">
                    {a?.agentName} and {b?.agentName}
                  </p>
                  <p className="mt-1 font-mono text-xs text-muted">
                    {pair.kind} · overlap {pair.jaccard.toFixed(2)} · {pair.rules.join(" ") || "—"}
                  </p>
                  {pair.reasons.map((reason) => (
                    <p key={reason} className="mt-2 text-xs leading-relaxed text-muted">
                      {reason}
                    </p>
                  ))}
                  {pair.anchors.length ? (
                    <p className="mt-2 text-xs text-fg">Anchors: {pair.anchors.join("; ")}</p>
                  ) : null}
                  {pair.conflicts.length ? (
                    <p className="mt-1 text-xs text-clay">
                      {pair.conflicts.map((conflict) => conflict.detail).join("; ")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </aside>
  );
}

function Compare({
  analysis,
  onSelect,
}: {
  analysis: Analysis;
  onSelect: (spanId: string) => void;
}) {
  const spanOf = new Map<string, Span>();
  for (const span of analysis.spans) for (const id of span.turnIds) spanOf.set(id, span);
  const agents = new Map<string, typeof analysis.turns>();
  for (const turn of analysis.turns) {
    const list = agents.get(turn.agentName) ?? [];
    list.push(turn);
    agents.set(turn.agentName, list);
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div>
        <h3 className="text-sm font-medium text-fg">Original turns</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          What each agent actually said, in order. The stripe is the span, if any.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {[...agents.entries()].map(([name, turns]) => (
            <section key={name} className="rounded-xl border border-line bg-surface">
              <h4 className="border-b border-line px-3 py-2 font-mono text-xs text-muted">{name}</h4>
              <ul className="flex flex-col gap-3 p-3">
                {turns.map((turn) => {
                  const span = spanOf.get(turn.id);
                  return (
                    <li
                      key={turn.id}
                      className={`border-l-2 pl-3 ${span ? kindLine[span.kind] : "border-line"}`}
                    >
                      <p className="font-mono text-xs text-muted">
                        {span ? span.id : "Unlinked"}
                        {turns.length > 1 ? ` · turn ${turn.index + 1}` : ""}
                      </p>
                      {span ? (
                        <div className="mt-1">
                          <Marked text={turn.text} marks={marksForTurn(analysis, span, turn.id)} />
                        </div>
                      ) : (
                        <p className="mt-1 text-sm leading-relaxed text-fg">{turn.text}</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>
      <div>
        <h3 className="text-sm font-medium text-fg">Proposed spans</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          The same turns, grouped only where a rule fired. Open a span to judge it.
        </p>
        <ul className="mt-3 flex flex-col gap-3">
          {analysis.spans.map((span) => (
            <li key={span.id}>
              <button
                type="button"
                className={`w-full rounded-xl border border-line bg-surface p-4 text-left hover:border-sage ${focus}`}
                onClick={() => onSelect(span.id)}
              >
                <span className="flex flex-wrap items-center gap-2">
                  <KindBadge kind={span.kind} />
                  <span className="font-mono text-xs text-muted">{span.id}</span>
                </span>
                <span className="mt-2 block text-sm leading-relaxed text-fg">{span.reasoning}</span>
              </button>
            </li>
          ))}
          {analysis.unlinked.map((turnId) => {
            const turn = turnById(analysis, turnId);
            if (!turn) return null;
            return (
              <li key={turnId} className="rounded-xl border border-line bg-surface p-4">
                <p className="font-mono text-xs text-muted">Unlinked · {turn.agentName}</p>
                <p className="mt-2 text-sm leading-relaxed text-fg">{turn.text}</p>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function Summary({
  analysis,
  decisions,
  copied,
  setCopied,
}: {
  analysis: Analysis;
  decisions: Record<string, DecisionState>;
  copied: boolean;
  setCopied: (value: boolean) => void;
}) {
  const markdown = formatSummary(analysis, decisions);
  const pending = analysis.spans.filter((span) => (decisions[span.id]?.status ?? "pending") === "pending");

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
    } catch {
      const area = document.createElement("textarea");
      area.value = markdown;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  function download() {
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "dialogspan-review.md";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="rounded-xl border border-line bg-surface">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="font-serif text-xl text-fg">Review summary</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Only groups you confirm are marked merged. Pending spans stay proposals.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btnQuiet} onClick={() => void copy()}>
            <Copy className="size-4" aria-hidden="true" />
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className={btnPrimary} onClick={download}>
            <Download className="size-4" aria-hidden="true" />
            Download
          </button>
        </div>
      </div>
      <div className="px-4 py-4">
        {pending.length > 0 ? (
          <p className="text-sm text-ochre" role="status">
            {pending.length} span{pending.length === 1 ? "" : "s"} still waiting on you.
          </p>
        ) : analysis.spans.length > 0 ? (
          <p className="text-sm text-sage" role="status">
            Every span has a decision. Contradictions you merged are still labeled as contradictions.
          </p>
        ) : null}
        <pre className="mt-4 max-h-screen overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-raised p-4 font-mono text-xs leading-relaxed text-fg">
          {markdown}
        </pre>
        <p className="sr-only" aria-live="polite">
          {copied ? "Summary copied" : ""}
        </p>
      </div>
    </div>
  );
}
