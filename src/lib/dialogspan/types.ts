export type RelationKind = "correlated" | "contradiction" | "inconclusive";

export type AgentDraft = {
  id: string;
  name: string;
  text: string;
};

export type Turn = {
  id: string;
  agentId: string;
  agentName: string;
  text: string;
  index: number;
};

export type Conflict = {
  type: "negation" | "number" | "antonym" | "time";
  weight: number;
  detail: string;
  surfaces: string[];
};

export type PairVerdict = {
  turnA: string;
  turnB: string;
  kind: RelationKind | "none";
  jaccard: number;
  anchors: string[];
  sharedTerms: string[];
  conflicts: Conflict[];
  reasons: string[];
  rules: string[];
  strength: number;
};

export type Span = {
  id: string;
  kind: RelationKind;
  turnIds: string[];
  strength: number;
  reasoning: string;
  agreementCount: number;
  contradictionCount: number;
  inconclusiveCount: number;
};

export type TurnNote = {
  turnId: string;
  text: string;
};

export type Analysis = {
  title: string;
  turns: Turn[];
  pairs: PairVerdict[];
  spans: Span[];
  unlinked: string[];
  notes: TurnNote[];
};

export type DecisionStatus = "pending" | "kept-separate" | "merged" | "reviewed";

export type DecisionState = {
  status: DecisionStatus;
  mergedTurnIds: string[];
};

export type MarkKind = "agree" | "conflict" | "shared";

export type Mark = {
  phrase: string;
  kind: MarkKind;
};
