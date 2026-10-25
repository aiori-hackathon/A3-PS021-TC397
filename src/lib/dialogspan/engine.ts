import type {
  Analysis,
  Conflict,
  Mark,
  MarkKind,
  PairVerdict,
  RelationKind,
  Span,
  Turn,
  TurnNote,
} from "./types";

/** Published thresholds. The method page and the evidence panel read these. */
export const THRESHOLDS = {
  noLinkBelow: 0.12,
  correlateJaccard: 0.34,
  correlateWithStrongAnchor: 0.22,
  correlateWithWeakAnchor: 0.28,
  correlateWithMeasurement: 0.18,
  contradictionJaccard: 0.12,
  inconclusiveJaccard: 0.12,
  numberRelativeGap: 0.08,
  negationWindow: 3,
} as const;

export const RULE_CATALOG = [
  {
    id: "R1",
    title: "Content overlap",
    text: "Jaccard overlap of content words, after a fixed stop list and a small synonym list (return/refund, shipped/ship, %/percent).",
  },
  {
    id: "R2",
    title: "Shared anchor",
    text: "A shared id (HK-204, order 4821), person name, or calendar date. Names and weekdays are weak anchors. Ids and matching measurements are strong.",
  },
  {
    id: "R3",
    title: "Same measurement",
    text: "The same number and unit: percent, money, days, hours, minutes, or a clock time.",
  },
  {
    id: "R4",
    title: "Negation conflict",
    text: "A content word is inside a 3-word window after no / not / never / non on only one side. If the same turn both affirms and negates the word, it is mixed and ignored.",
  },
  {
    id: "R5",
    title: "Numeric conflict",
    text: "Same unit, different value. Any difference for day/hour/minute counts. More than 8% relative gap for money and percents.",
  },
  {
    id: "R6",
    title: "Antonym",
    text: "A fixed pair: available/unavailable, degraded/normal, delayed/on-time, open/closed, allow/deny, present/absent.",
  },
  {
    id: "R7",
    title: "Time of day",
    text: "Morning, afternoon, and evening. Recorded as a note. It becomes a contradiction only together with a strong anchor (a shared id or measurement).",
  },
  {
    id: "R8",
    title: "Different topic",
    text: "Topic lists (shipping, money, policy, incident, schedule, lodging) are disjoint and overlap is under 0.45. A shared id is not treated as correlation.",
  },
  {
    id: "R9",
    title: "No link",
    text: "Overlap under 0.12 and no shared anchor. The turns stay unlinked. Silence is not agreement.",
  },
  {
    id: "R10",
    title: "Weak links do not join",
    text: "An inconclusive pair never pulls a turn into a correlated or contradiction span, and never merges two such spans.",
  },
] as const;

const STOP = new Set(
  `a an the of to and or for in on at by with from is are was were be been being it this that these those as if then than into over after before please you your we our they their them he she i my me his her has have had will can could should would may might also just about per via its do does did done when while where who what which there here only every still more other such but so because during between under again further once both all any some each very too up out off own must onto within without across after before again`.split(
    /\s+/,
  ),
);

const WEAK_CONFLICT = new Set([
  "turn",
  "span",
  "item",
  "order",
  "day",
  "time",
  "service",
  "user",
  "room",
  "guest",
  "booking",
  "thing",
  "check",
]);

const SYNONYM: Record<string, string> = {
  automatically: "automatic",
  automatic: "automatic",
  returns: "refund",
  return: "refund",
  refunds: "refund",
  refund: "refund",
  delivery: "deliver",
  delivered: "deliver",
  deliveries: "deliver",
  delivering: "deliver",
  arrival: "arrive",
  arrive: "arrive",
  arrives: "arrive",
  arriving: "arrive",
  percent: "percent",
  percentage: "percent",
  confirmation: "confirm",
  confirmed: "confirm",
  confirms: "confirm",
  confirming: "confirm",
  payments: "payment",
  payment: "payment",
  errors: "error",
  error: "error",
  shipped: "ship",
  shipping: "ship",
  shipment: "ship",
  shipments: "ship",
  days: "day",
  hours: "hour",
  minutes: "minute",
  tracking: "track",
  tracked: "track",
  departure: "depart",
  departed: "depart",
  degraded: "degrade",
  degradation: "degrade",
  unavailable: "unavailable",
  available: "available",
  rooms: "room",
  guests: "guest",
  charged: "charge",
  charges: "charge",
  charging: "charge",
  rates: "rate",
  quoted: "quote",
  quotes: "quote",
  scheduled: "schedule",
  schedule: "schedule",
  booking: "booking",
  bookings: "booking",
  check: "checkin",
  smoking: "smoke",
  nonsmoking: "smoke",
  unopened: "unopened",
  opened: "opened",
};

const ANTONYMS: [string, string][] = [
  ["available", "unavailable"],
  ["degrade", "normal"],
  ["delayed", "ontime"],
  ["present", "absent"],
  ["open", "closed"],
  ["allow", "deny"],
];

const PRED: Record<string, string[]> = {
  shipping: ["ship", "eta", "deliver", "track", "hub", "carrier", "depart", "arrive"],
  money: ["invoice", "payment", "charge", "rate", "paid", "due", "price", "inr", "prepaid", "folio", "quote"],
  policy: ["policy", "refund", "unopened", "window", "available", "unavailable"],
  incident: ["error", "incident", "degrade", "outage", "checkout", "utc"],
  schedule: [
    "morning",
    "afternoon",
    "evening",
    "slot",
    "clinic",
    "appointment",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
  ],
  lodging: ["room", "king", "smoke", "guest", "dinner", "table", "grill", "folio"],
};

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const MONTHS = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];

const NEGATION = new Set([
  "no",
  "not",
  "never",
  "cannot",
  "cant",
  "dont",
  "doesnt",
  "isnt",
  "arent",
  "wont",
  "without",
  "non",
]);

type NumFact = { value: number; unit: string; raw: string };

type Profile = {
  terms: Set<string>;
  negated: Set<string>;
  affirmed: Set<string>;
  surface: Map<string, string>;
  ids: string[];
  names: string[];
  dates: string[];
  numbers: NumFact[];
  preds: Set<string>;
  buckets: Set<string>;
};

function clamp(n: number, min = 0, max = 1) {
  return Math.min(max, Math.max(min, n));
}

function stem(w: string) {
  if (w.length <= 4) return w;
  if (w.endsWith("ies") && w.length > 5) return `${w.slice(0, -3)}y`;
  if (w.endsWith("ing") && w.length > 6) return w.slice(0, -3);
  if (w.endsWith("ed") && w.length > 5) return w.slice(0, -2);
  if (w.endsWith("es") && w.length > 5) return w.slice(0, -2);
  if (w.endsWith("s") && !w.endsWith("ss") && w.length > 4) return w.slice(0, -1);
  return w;
}

function norm(w: string) {
  const x = w.toLowerCase();
  if (SYNONYM[x]) return SYNONYM[x];
  if (/^\d+$/.test(x)) return x;
  const s = stem(x);
  return SYNONYM[s] ?? s;
}

function round1(n: number) {
  return Math.round(n * 100) / 100;
}

export function splitTurns(text: string): string[] {
  const trimmed = text.replace(/\r\n/g, "\n").trim();
  if (!trimmed) return [];
  return trimmed
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function extractIds(text: string) {
  const ids = new Set<string>();
  for (const m of text.matchAll(/\b[A-Z]{2,6}-\d{2,6}\b/g)) ids.add(m[0].toUpperCase());
  for (const m of text.matchAll(
    /\b(?:order|booking|shipment|invoice|ticket|case)\s*#?\s*([A-Za-z0-9-]{3,})/gi,
  )) {
    ids.add(m[1].toUpperCase());
  }
  for (const m of text.matchAll(/#([A-Za-z0-9-]{3,})\b/g)) ids.add(m[1].toUpperCase());
  return [...ids];
}

function extractNames(text: string) {
  const names = new Set<string>();
  for (const m of text.matchAll(/\b(?:Dr|Mr|Ms|Mrs)\.?\s+([A-Z][a-z]+)\b/g)) {
    names.add(m[1].toLowerCase());
  }
  const skip = new Set([
    ...WEEKDAYS.map((d) => d[0].toUpperCase() + d.slice(1)),
    ...MONTHS.map((d) => d[0].toUpperCase() + d.slice(1)),
    "The",
    "This",
    "That",
    "Order",
    "Booking",
    "Guest",
    "DialogSpan",
    "API",
    "UTC",
  ]);
  for (const m of text.matchAll(/\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b/g)) {
    if (skip.has(m[1]) || skip.has(m[2])) continue;
    names.add(`${m[1]} ${m[2]}`.toLowerCase());
  }
  return [...names];
}

function extractDates(text: string) {
  const t = text.toLowerCase();
  const dates = new Set<string>();
  for (const day of WEEKDAYS) if (t.includes(day)) dates.add(day);
  const months = MONTHS.join("|");
  for (const m of t.matchAll(new RegExp(`\\b(\\d{1,2})\\s+(${months})\\b`, "g"))) {
    dates.add(`${m[1]} ${m[2]}`);
  }
  return [...dates];
}

function extractNumbers(text: string): NumFact[] {
  const found: NumFact[] = [];
  const occupied: [number, number][] = [];
  const take = (re: RegExp, unit: string, valueOf: (raw: string) => number) => {
    const flags = re.flags.includes("g") ? re.flags : `${re.flags}g`;
    const r = new RegExp(re.source, flags);
    let m: RegExpExecArray | null;
    while ((m = r.exec(text))) {
      const start = m.index;
      const end = start + m[0].length;
      if (occupied.some(([s, e]) => start < e && end > s)) continue;
      occupied.push([start, end]);
      found.push({ value: valueOf(m[0]), unit, raw: m[0].trim() });
    }
  };
  take(/₹\s*\d[\d,]*(?:\.\d+)?/g, "inr", (raw) => Number(raw.replace(/[₹,\s]/g, "")));
  take(/\d+(?:\.\d+)?\s*(?:%|percent)/gi, "percent", (raw) => parseFloat(raw));
  take(/\d+(?:\.\d+)?\s*days?/gi, "day", (raw) => parseFloat(raw));
  take(/\d+(?:\.\d+)?\s*hours?/gi, "hour", (raw) => parseFloat(raw));
  take(/\d+(?:\.\d+)?\s*minutes?/gi, "minute", (raw) => parseFloat(raw));
  take(/\b\d{1,2}:\d{2}\b/g, "clock", (raw) => {
    const [h, min] = raw.split(":").map(Number);
    return h * 60 + min;
  });
  return found;
}

function timeBuckets(text: string) {
  const t = text.toLowerCase();
  const buckets = new Set<string>();
  if (/\bmorning\b/.test(t)) buckets.add("morning");
  if (/\bafternoon\b|\bnoon\b/.test(t)) buckets.add("afternoon");
  if (/\bevening\b|\bnight\b/.test(t)) buckets.add("evening");
  for (const m of t.matchAll(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/g)) {
    let hour = Number(m[1]);
    const ap = m[3];
    if (ap === "pm" && hour < 12) hour += 12;
    if (ap === "am" && hour === 12) hour = 0;
    if (hour < 12) buckets.add("morning");
    else if (hour < 17) buckets.add("afternoon");
    else buckets.add("evening");
  }
  return buckets;
}

function profileOf(text: string): Profile {
  const normalized = text.replace(/n['’]t\b/gi, " not ");
  const rawWords = normalized.match(/[A-Za-z0-9]+/g) ?? [];
  const terms = new Set<string>();
  const negated = new Set<string>();
  const affirmed = new Set<string>();
  const surface = new Map<string, string>();
  let window = 0;
  for (const w of rawWords) {
    const low = w.toLowerCase();
    if (low === "non") {
      window = 1;
      continue;
    }
    if (NEGATION.has(low)) {
      window = THRESHOLDS.negationWindow;
      continue;
    }
    if (STOP.has(low)) continue;
    if (/^\d+$/.test(low)) {
      terms.add(low);
      if (!surface.has(low)) surface.set(low, w);
      continue;
    }
    const n = norm(low);
    if (!n || (n.length < 3 && !/^\d+$/.test(n) && n !== "eta")) continue;
    terms.add(n);
    if (!surface.has(n)) surface.set(n, /[A-Z]/.test(w) ? w : low);
    if (window > 0) {
      negated.add(n);
      window -= 1;
    } else if (!negated.has(n)) {
      affirmed.add(n);
    } else {
      affirmed.add(n);
    }
  }
  const preds = new Set<string>();
  for (const [name, words] of Object.entries(PRED)) {
    if (words.some((word) => terms.has(word))) preds.add(name);
  }
  return {
    terms,
    negated,
    affirmed,
    surface,
    ids: extractIds(text),
    names: extractNames(text),
    dates: extractDates(text),
    numbers: extractNumbers(text),
    preds,
    buckets: timeBuckets(text),
  };
}

function intersect<T>(a: Set<T>, b: Set<T>) {
  const out = new Set<T>();
  for (const item of a) if (b.has(item)) out.add(item);
  return out;
}

function numbersClose(a: NumFact, b: NumFact) {
  if (a.unit !== b.unit) return false;
  if (a.unit === "day" || a.unit === "hour" || a.unit === "minute" || a.unit === "clock") {
    return a.value === b.value;
  }
  const denom = Math.max(Math.abs(a.value), Math.abs(b.value), 1);
  return Math.abs(a.value - b.value) / denom <= THRESHOLDS.numberRelativeGap;
}

function unitLabel(unit: string) {
  if (unit === "inr") return "amount";
  if (unit === "percent") return "percent";
  if (unit === "clock") return "clock time";
  return unit;
}

function strengthFor(kind: RelationKind, jaccard: number, strong: number, weight: number) {
  if (kind === "correlated") return clamp(0.38 + jaccard * 0.62 + Math.min(strong, 3) * 0.06);
  if (kind === "contradiction") return clamp(0.48 + Math.min(weight, 4) * 0.08 + jaccard * 0.2);
  return clamp(0.16 + jaccard * 0.4, 0, 0.44);
}

export function evidenceBand(kind: RelationKind, strength: number) {
  if (kind === "inconclusive") return "limited";
  if (strength >= 0.75) return "strong";
  if (strength >= 0.55) return "moderate";
  return "limited";
}

function judge(a: Profile, b: Profile): Omit<PairVerdict, "turnA" | "turnB"> {
  const shared = intersect(a.terms, b.terms);
  const unionSize = new Set([...a.terms, ...b.terms]).size;
  const jaccard = unionSize === 0 ? 0 : shared.size / unionSize;
  const sharedIds = a.ids.filter((id) => b.ids.includes(id));
  const sharedNames = a.names.filter((name) => b.names.includes(name));
  const sharedDates = a.dates.filter((date) => b.dates.includes(date));

  const sameNumbers: string[] = [];
  const conflicts: Conflict[] = [];
  const usedB = new Set<number>();
  a.numbers.forEach((na) => {
    let closest = -1;
    let closestGap = Infinity;
    b.numbers.forEach((nb, index) => {
      if (na.unit !== nb.unit || usedB.has(index)) return;
      const gap = Math.abs(na.value - nb.value);
      if (gap < closestGap) {
        closestGap = gap;
        closest = index;
      }
    });
    if (closest === -1) return;
    const nb = b.numbers[closest];
    usedB.add(closest);
    if (numbersClose(na, nb)) {
      sameNumbers.push(`same ${unitLabel(na.unit)} ${na.raw}`);
    } else {
      conflicts.push({
        type: "number",
        weight: 2,
        detail: `${na.raw} vs ${nb.raw}`,
        surfaces: [na.raw, nb.raw],
      });
    }
  });

  for (const term of new Set([...a.negated, ...b.negated, ...a.affirmed, ...b.affirmed])) {
    if (WEAK_CONFLICT.has(term)) continue;
    const aNeg = a.negated.has(term) && !a.affirmed.has(term);
    const bNeg = b.negated.has(term) && !b.affirmed.has(term);
    const aAff = a.affirmed.has(term) && !a.negated.has(term);
    const bAff = b.affirmed.has(term) && !b.negated.has(term);
    if ((aNeg && bAff) || (bNeg && aAff)) {
      const surface = a.surface.get(term) ?? b.surface.get(term) ?? term;
      conflicts.push({
        type: "negation",
        weight: 2,
        detail: `"${surface}" is negated in one turn and affirmed in the other`,
        surfaces: [surface],
      });
    }
  }

  for (const [left, right] of ANTONYMS) {
    const aHasL = a.terms.has(left) && !a.negated.has(left);
    const aHasR = a.terms.has(right) && !a.negated.has(right);
    const bHasL = b.terms.has(left) && !b.negated.has(left);
    const bHasR = b.terms.has(right) && !b.negated.has(right);
    if ((aHasL && bHasR) || (aHasR && bHasL)) {
      conflicts.push({
        type: "antonym",
        weight: 2,
        detail: `"${left}" vs "${right}"`,
        surfaces: [left, right],
      });
    }
  }

  const timeNote =
    a.buckets.size > 0 &&
    b.buckets.size > 0 &&
    [...a.buckets].every((bucket) => !b.buckets.has(bucket)) &&
    sharedDates.length > 0;
  if (timeNote) {
    conflicts.push({
      type: "time",
      weight: sharedIds.length > 0 || sameNumbers.length > 0 ? 2 : 1,
      detail: `${[...a.buckets].join("/")} vs ${[...b.buckets].join("/")}`,
      surfaces: [...a.buckets, ...b.buckets],
    });
  }

  const anchors = [
    ...sharedIds.map((id) => `id ${id}`),
    ...sharedNames.map((name) => `name ${name}`),
    ...sharedDates.map((date) => `date ${date}`),
    ...sameNumbers,
  ];
  const strong = sharedIds.length + sameNumbers.length;
  const weak = sharedNames.length + sharedDates.length;
  const weight = conflicts.filter((c) => c.weight >= 2).reduce((sum, c) => sum + c.weight, 0);
  const predsA = a.preds;
  const predsB = b.preds;
  const predOverlap = intersect(predsA, predsB);
  const predicatesDiffer = predsA.size > 0 && predsB.size > 0 && predOverlap.size === 0;

  const sharedTerms = [...shared].filter((term) => !/^\d+$/.test(term) || term.length >= 3).slice(0, 12);
  const reasons: string[] = [];
  const rules: string[] = [];
  let kind: RelationKind | "none" = "none";

  const hardConflict = weight >= 2 && (jaccard >= THRESHOLDS.contradictionJaccard || strong >= 1 || shared.size >= 3);

  if (hardConflict) {
    kind = "contradiction";
    if (conflicts.some((c) => c.type === "negation" && c.weight >= 2)) rules.push("R4");
    if (conflicts.some((c) => c.type === "number")) rules.push("R5");
    if (conflicts.some((c) => c.type === "antonym")) rules.push("R6");
    if (conflicts.some((c) => c.type === "time" && c.weight >= 2)) rules.push("R7");
    reasons.push(
      `Conflict: ${conflicts
        .filter((c) => c.weight >= 2)
        .map((c) => c.detail)
        .slice(0, 3)
        .join("; ")}.`,
    );
    reasons.push(`Overlap is ${round1(jaccard)}. A conflict on overlapping talk stays a contradiction.`);
  } else if (
    !predicatesDiffer &&
    (jaccard >= THRESHOLDS.correlateJaccard ||
      (jaccard >= THRESHOLDS.correlateWithStrongAnchor && strong >= 1) ||
      (jaccard >= THRESHOLDS.correlateWithWeakAnchor && weak >= 1) ||
      (sameNumbers.length >= 1 && jaccard >= THRESHOLDS.correlateWithMeasurement))
  ) {
    kind = "correlated";
    rules.push("R1");
    if (strong >= 1 || weak >= 1) rules.push("R2");
    if (sameNumbers.length) rules.push("R3");
    reasons.push(
      `Overlap is ${round1(jaccard)}${sharedTerms.length ? ` on ${sharedTerms.slice(0, 6).join(", ")}` : ""}.`,
    );
    if (anchors.length) reasons.push(`Shared anchors: ${anchors.slice(0, 4).join("; ")}.`);
    reasons.push("No negation, number, or antonym conflict fired.");
  } else if (
    predicatesDiffer &&
    jaccard < 0.45 &&
    (strong >= 1 || weak >= 1 || jaccard >= THRESHOLDS.inconclusiveJaccard || shared.size >= 3)
  ) {
    kind = "inconclusive";
    rules.push("R8");
    const left = [...predsA].join(", ");
    const right = [...predsB].join(", ");
    reasons.push(
      `Topics differ (${left} vs ${right}). Overlap is ${round1(jaccard)}${
        anchors.length ? `, with ${anchors.slice(0, 3).join("; ")}` : ""
      }. Not enough to correlate, and no conflict rule fired.`,
    );
  } else if (jaccard >= THRESHOLDS.inconclusiveJaccard || strong >= 1 || weak >= 1 || shared.size >= 3 || timeNote) {
    kind = "inconclusive";
    rules.push("R1");
    if (timeNote) rules.push("R7");
    reasons.push(
      `Overlap is ${round1(jaccard)}${anchors.length ? `. Anchors: ${anchors.slice(0, 3).join("; ")}` : ""}. Below the correlation bar (${THRESHOLDS.correlateJaccard}${strong ? `, or ${THRESHOLDS.correlateWithStrongAnchor} with a strong anchor` : ""}).`,
    );
    if (timeNote && weight < 2) {
      reasons.push(`Times differ (${conflicts.find((c) => c.type === "time")?.detail ?? "time of day"}), but nothing strong ties them to one event.`);
    }
    reasons.push("Left inconclusive. Not called a match, and not called a conflict.");
  } else {
    kind = "none";
    rules.push("R9");
    reasons.push(
      `Overlap ${round1(jaccard)} is under ${THRESHOLDS.noLinkBelow} and there is no shared anchor. Not linked.`,
    );
  }

  if (predicatesDiffer && kind === "correlated" && jaccard < 0.45) {
    kind = "inconclusive";
    rules.push("R8");
  }

  return {
    kind,
    jaccard: round1(jaccard),
    anchors,
    sharedTerms,
    conflicts,
    reasons,
    rules,
    strength: kind === "none" ? 0 : round1(strengthFor(kind, jaccard, strong, weight)),
  };
}

function agentIdFor(index: number) {
  return `a${index + 1}`;
}

export function analyze(input: {
  title: string;
  agents: { name: string; turns: string[] }[];
}): Analysis {
  const turns: Turn[] = [];
  input.agents.forEach((agent, agentIndex) => {
    const name = agent.name.trim() || `Agent ${agentIndex + 1}`;
    agent.turns.forEach((text, index) => {
      const clean = text.trim();
      if (!clean) return;
      turns.push({
        id: `t${agentIndex + 1}-${index + 1}`,
        agentId: agentIdFor(agentIndex),
        agentName: name,
        text: clean,
        index,
      });
    });
  });

  const profiles = new Map(turns.map((turn) => [turn.id, profileOf(turn.text)]));
  const pairs: PairVerdict[] = [];
  for (let i = 0; i < turns.length; i += 1) {
    for (let j = i + 1; j < turns.length; j += 1) {
      if (turns[i].agentId === turns[j].agentId) continue;
      const verdict = judge(profiles.get(turns[i].id)!, profiles.get(turns[j].id)!);
      pairs.push({ turnA: turns[i].id, turnB: turns[j].id, ...verdict });
    }
  }

  const hard = pairs.filter((pair) => pair.kind === "correlated" || pair.kind === "contradiction");
  const soft = pairs.filter((pair) => pair.kind === "inconclusive");
  const parent = new Map(turns.map((turn) => [turn.id, turn.id]));
  const find = (id: string): string => {
    const p = parent.get(id)!;
    if (p === id) return id;
    const root = find(p);
    parent.set(id, root);
    return root;
  };
  const unite = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };
  for (const pair of hard) unite(pair.turnA, pair.turnB);

  const hardGroups = new Map<string, string[]>();
  for (const turn of turns) {
    const root = find(turn.id);
    const involved = hard.some((pair) => pair.turnA === turn.id || pair.turnB === turn.id);
    if (!involved) continue;
    const list = hardGroups.get(root) ?? [];
    list.push(turn.id);
    hardGroups.set(root, list);
  }

  const inHard = new Set<string>([...hardGroups.values()].flat());
  const freeParent = new Map(turns.filter((t) => !inHard.has(t.id)).map((t) => [t.id, t.id]));
  const findFree = (id: string): string => {
    const p = freeParent.get(id);
    if (!p || p === id) return id;
    const root = findFree(p);
    freeParent.set(id, root);
    return root;
  };
  for (const pair of soft) {
    if (inHard.has(pair.turnA) || inHard.has(pair.turnB)) continue;
    const ra = findFree(pair.turnA);
    const rb = findFree(pair.turnB);
    if (ra !== rb) freeParent.set(ra, rb);
  }
  const softGroups = new Map<string, string[]>();
  for (const turn of turns) {
    if (inHard.has(turn.id)) continue;
    const touched = soft.some(
      (pair) =>
        (pair.turnA === turn.id || pair.turnB === turn.id) &&
        !inHard.has(pair.turnA) &&
        !inHard.has(pair.turnB),
    );
    if (!touched) continue;
    const root = findFree(turn.id);
    const list = softGroups.get(root) ?? [];
    list.push(turn.id);
    softGroups.set(root, list);
  }

  const order = new Map(turns.map((turn, index) => [turn.id, index]));
  const sortIds = (ids: string[]) => [...ids].sort((a, b) => order.get(a)! - order.get(b)!);

  const spans: Span[] = [];
  const pushSpan = (ids: string[], forced?: RelationKind) => {
    const set = new Set(ids);
    const inside = pairs.filter((pair) => set.has(pair.turnA) && set.has(pair.turnB) && pair.kind !== "none");
    const contradictions = inside.filter((pair) => pair.kind === "contradiction");
    const agreements = inside.filter((pair) => pair.kind === "correlated");
    const inconclusives = inside.filter((pair) => pair.kind === "inconclusive");
    const kind: RelationKind =
      forced ?? (contradictions.length > 0 ? "contradiction" : agreements.length > 0 ? "correlated" : "inconclusive");
    const pool =
      kind === "contradiction" ? contradictions : kind === "correlated" ? agreements : inconclusives.length ? inconclusives : inside;
    const strength = pool.length
      ? round1(pool.reduce((sum, pair) => sum + pair.strength, 0) / pool.length)
      : 0.2;
    const lead = [...pool].sort((a, b) => b.strength - a.strength)[0];
    let reasoning = lead?.reasons[0] ?? "No pair inside this span produced a reason.";
    if (kind === "contradiction" && agreements.length > 0) {
      const names = pairNames(agreements[0], turns);
      reasoning += ` ${names} still agree on their own pair. That does not cancel the disagreement.`;
    }
    if (kind === "correlated") {
      reasoning += " This is overlapping evidence, not a claim that either turn is true.";
    }
    if (kind === "inconclusive") {
      reasoning += " Nothing here is promoted to correlated or contradiction.";
    }
    spans.push({
      id: `S${spans.length + 1}`,
      kind,
      turnIds: sortIds(ids),
      strength,
      reasoning,
      agreementCount: agreements.length,
      contradictionCount: contradictions.length,
      inconclusiveCount: inconclusives.length,
    });
  };

  for (const ids of hardGroups.values()) {
    if (ids.length >= 2) pushSpan(ids);
  }
  for (const ids of softGroups.values()) {
    if (ids.length >= 2) pushSpan(ids, "inconclusive");
  }
  spans.sort((a, b) => order.get(a.turnIds[0])! - order.get(b.turnIds[0])!);
  spans.forEach((span, index) => {
    span.id = `S${index + 1}`;
  });

  const spanned = new Set(spans.flatMap((span) => span.turnIds));
  const unlinked = turns.filter((turn) => !spanned.has(turn.id)).map((turn) => turn.id);
  const notes: TurnNote[] = [];
  const spanOf = new Map<string, Span>();
  for (const span of spans) for (const id of span.turnIds) spanOf.set(id, span);

  for (const pair of soft) {
    const spanA = spanOf.get(pair.turnA);
    const spanB = spanOf.get(pair.turnB);
    if (spanA && spanB && spanA.id === spanB.id) continue;
    if (spanA && !spanB) {
      notes.push({
        turnId: pair.turnB,
        text: `Not joined to ${spanA.id}. ${pair.reasons[0]} Rule R10: a weak link does not pull this turn into that span.`,
      });
    } else if (spanB && !spanA) {
      notes.push({
        turnId: pair.turnA,
        text: `Not joined to ${spanB.id}. ${pair.reasons[0]} Rule R10: a weak link does not pull this turn into that span.`,
      });
    } else if (spanA && spanB && spanA.id !== spanB.id) {
      notes.push({
        turnId: pair.turnA,
        text: `Weak link between ${spanA.id} and ${spanB.id} was not used to merge them. ${pair.reasons[0]}`,
      });
    }
  }

  const seen = new Set<string>();
  const uniqueNotes = notes.filter((note) => {
    const key = `${note.turnId}:${note.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return {
    title: input.title,
    turns,
    pairs,
    spans,
    unlinked,
    notes: uniqueNotes,
  };
}

function pairNames(pair: PairVerdict, turns: Turn[]) {
  const name = (id: string) => turns.find((turn) => turn.id === id)?.agentName ?? id;
  return `${name(pair.turnA)} and ${name(pair.turnB)}`;
}

export function pairsTouching(analysis: Analysis, turnIds: string[]) {
  const set = new Set(turnIds);
  return analysis.pairs.filter((pair) => set.has(pair.turnA) && set.has(pair.turnB) && pair.kind !== "none");
}

export function marksForTurn(analysis: Analysis, span: Span, turnId: string): Mark[] {
  const marks: Mark[] = [];
  const seen = new Set<string>();
  const add = (phrase: string, kind: MarkKind) => {
    const clean = phrase.trim();
    if (clean.length < 2) return;
    const key = `${kind}:${clean.toLowerCase()}`;
    if (seen.has(key)) return;
    seen.add(key);
    marks.push({ phrase: clean, kind });
  };
  for (const pair of pairsTouching(analysis, span.turnIds)) {
    if (pair.turnA !== turnId && pair.turnB !== turnId) continue;
    if (pair.kind === "contradiction") {
      for (const conflict of pair.conflicts) {
        if (conflict.weight < 2 && conflict.type === "time") {
          for (const surface of conflict.surfaces) add(surface, "shared");
        } else {
          for (const surface of conflict.surfaces) add(surface, "conflict");
        }
      }
    }
    const kind: MarkKind = pair.kind === "correlated" ? "agree" : "shared";
    for (const term of pair.sharedTerms) add(term, kind);
    for (const anchor of pair.anchors) {
      const raw = anchor.replace(/^(id|name|date|same [a-z ]+)\s+/i, "");
      if (raw && raw !== anchor) add(raw, kind);
      else add(anchor, kind);
    }
  }
  const rank: Record<MarkKind, number> = { conflict: 3, agree: 2, shared: 1 };
  const best = new Map<string, Mark>();
  for (const mark of marks) {
    const key = mark.phrase.toLowerCase();
    const prev = best.get(key);
    if (!prev || rank[mark.kind] > rank[prev.kind]) best.set(key, mark);
  }
  return [...best.values()].sort((a, b) => b.phrase.length - a.phrase.length);
}

export type Segment = { text: string; kind: MarkKind | "plain" };

export function highlightText(text: string, marks: Mark[]): Segment[] {
  const ranges: { start: number; end: number; kind: MarkKind }[] = [];
  const lower = text.toLowerCase();
  for (const mark of marks) {
    const needle = mark.phrase.toLowerCase();
    if (needle.length < 2) continue;
    let from = 0;
    while (from < lower.length) {
      const at = lower.indexOf(needle, from);
      if (at === -1) break;
      const before = at === 0 || !/[a-z0-9]/i.test(lower[at - 1] ?? "");
      const after = at + needle.length >= lower.length || !/[a-z0-9]/i.test(lower[at + needle.length] ?? "");
      if (before && after) ranges.push({ start: at, end: at + needle.length, kind: mark.kind });
      from = at + needle.length;
    }
  }
  ranges.sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const kept: typeof ranges = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start < cursor) continue;
    kept.push(range);
    cursor = range.end;
  }
  if (!kept.length) return [{ text, kind: "plain" }];
  const segments: Segment[] = [];
  let at = 0;
  for (const range of kept) {
    if (range.start > at) segments.push({ text: text.slice(at, range.start), kind: "plain" });
    segments.push({ text: text.slice(range.start, range.end), kind: range.kind });
    at = range.end;
  }
  if (at < text.length) segments.push({ text: text.slice(at), kind: "plain" });
  return segments;
}

export function turnById(analysis: Analysis, id: string) {
  return analysis.turns.find((turn) => turn.id === id);
}

export function decisionLabel(status: string) {
  if (status === "kept-separate") return "Kept separate";
  if (status === "merged") return "Merged by you";
  if (status === "reviewed") return "Reviewed";
  return "Pending your decision";
}
