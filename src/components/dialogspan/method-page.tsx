import { Link } from "@tanstack/react-router";
import { RULE_CATALOG, THRESHOLDS } from "@/lib/dialogspan/engine";
import { scenarios } from "@/lib/dialogspan/scenarios";
import { Frame } from "./frame";

const focus =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sage";

export function MethodPage() {
  return (
    <Frame>
      <article className="mx-auto max-w-3xl px-4 py-8">
        <p className="font-mono text-xs text-muted">PS-021 · How DialogSpan decides</p>
        <h1 className="mt-2 font-serif text-4xl leading-tight text-fg">Method</h1>
        <p className="mt-4 text-base leading-relaxed text-muted">
          DialogSpan compares turns from different agents and shows the evidence. It does not
          understand a situation the way a person does. It does not call a model. It applies the
          rules on this page, then waits.
        </p>

        <h2 className="mt-10 font-serif text-2xl text-fg">The loop</h2>
        <ol className="mt-4 flex flex-col gap-3 text-sm leading-relaxed text-fg">
          <li>
            <span className="font-medium">1. Collect.</span> Each agent block is split into turns on
            blank lines. Turns from the same agent are never used as evidence that they agree with
            each other.
          </li>
          <li>
            <span className="font-medium">2. Compare.</span> Every pair from different agents gets a
            Jaccard overlap on content words, a list of anchors, and a check for conflicts.
          </li>
          <li>
            <span className="font-medium">3. Label.</span> A pair is correlated, a contradiction, or
            inconclusive. Pairs under the floor are not linked at all.
          </li>
          <li>
            <span className="font-medium">4. Group, carefully.</span> Correlated and contradiction
            pairs form spans. Inconclusive pairs do not drag a turn into those spans and do not glue
            two spans together.
          </li>
          <li>
            <span className="font-medium">5. You decide.</span> Keep separate, mark reviewed, or
            merge the turns you tick. A merge groups text. It does not rewrite it and it does not
            clear a contradiction.
          </li>
        </ol>

        <h2 className="mt-10 font-serif text-2xl text-fg">Thresholds</h2>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          These are the numbers in the running code. Overlap is Jaccard, from 0 to 1.
        </p>
        <dl className="mt-4 divide-y divide-line rounded-xl border border-line">
          <Row term="No link below" value={THRESHOLDS.noLinkBelow} note="Under this, with no anchor, the pair is silence." />
          <Row term="Correlate" value={THRESHOLDS.correlateJaccard} note="Overlap alone is enough when topics are not disjoint." />
          <Row
            term="Correlate with a strong anchor"
            value={THRESHOLDS.correlateWithStrongAnchor}
            note="A shared id or the same measurement, plus this much overlap."
          />
          <Row
            term="Correlate with only a weak anchor"
            value={THRESHOLDS.correlateWithWeakAnchor}
            note="A shared name or date needs more overlap than an id does."
          />
          <Row
            term="Contradiction floor"
            value={THRESHOLDS.contradictionJaccard}
            note="A real conflict still needs some shared talk, a strong anchor, or three shared words."
          />
          <Row
            term="Number gap"
            value={THRESHOLDS.numberRelativeGap}
            note="Money and percents. Day, hour, minute, and clock values must match exactly to count as the same."
          />
          <Row
            term="Negation window"
            value={THRESHOLDS.negationWindow}
            note="Content words after no, not, never. The prefix non- only covers the next word."
          />
        </dl>

        <h2 className="mt-10 font-serif text-2xl text-fg">Rules</h2>
        <ol className="mt-4 flex flex-col gap-4">
          {RULE_CATALOG.map((rule) => (
            <li key={rule.id} className="rounded-xl border border-line bg-surface px-4 py-3">
              <p className="font-mono text-xs text-sage">
                {rule.id} · {rule.title}
              </p>
              <p className="mt-2 text-sm leading-relaxed text-fg">{rule.text}</p>
            </li>
          ))}
        </ol>

        <h2 className="mt-10 font-serif text-2xl text-fg">What a label means</h2>
        <ul className="mt-4 flex flex-col gap-3 text-sm leading-relaxed text-fg">
          <li>
            <span className="text-sage">Correlated.</span> Overlap cleared a bar and no conflict rule
            fired. It means the turns talk about the same things in the text. It does not mean they
            are true.
          </li>
          <li>
            <span className="text-clay">Contradiction.</span> A negation, a different measurement, or
            a listed antonym landed on overlapping talk. If two of three agents agree, the span is
            still a contradiction. The agreeing pair is shown, not hidden.
          </li>
          <li>
            <span className="text-ochre">Inconclusive.</span> There is a trace — a weekday, a name, a
            thin overlap, topics that point different ways — and it is not enough. Time-of-day
            differences without a strong anchor stay here.
          </li>
          <li>
            <span className="text-muted">Unlinked.</span> The pair never became a relationship.
            Unlinked is not agreement.
          </li>
        </ul>

        <h2 className="mt-10 font-serif text-2xl text-fg">What DialogSpan does not do</h2>
        <ul className="mt-4 flex list-disc flex-col gap-2 pl-5 text-sm leading-relaxed text-fg">
          <li>It does not merge turns unless you confirm the merge.</li>
          <li>It does not invent a relationship to fill a gap.</li>
          <li>It does not treat a shared order number as proof of the same claim.</li>
          <li>It does not pick a side in a contradiction.</li>
          <li>It does not rewrite anyone’s words into a blended answer.</li>
          <li>It does not call a language model, and it has no hidden score.</li>
          <li>It does not know the world beyond the word lists, the synonym list, and the antonym list on this page.</li>
          <li>A word that a turn both affirms and negates is mixed, and it is ignored as a conflict.</li>
          <li>Negation is a short window, not real scope. “Outside the refund window” is not read as philosophy. The number and the word “available” have to carry that case.</li>
        </ul>

        <h2 className="mt-10 font-serif text-2xl text-fg">Lab examples</h2>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Each example is written so you can see one rule hold back. They are not labeled in advance
          inside the workspace. The kicker only says what the transcript is built to contain.
        </p>
        <ul className="mt-4 flex flex-col gap-3">
          {scenarios.map((scenario) => (
            <li key={scenario.id} className="rounded-xl border border-line bg-surface px-4 py-3">
              <p className="text-sm text-fg">{scenario.title}</p>
              <p className="mt-1 text-xs text-muted">{scenario.kicker}</p>
              <p className="mt-2 text-sm leading-relaxed text-muted">{scenario.shows}</p>
            </li>
          ))}
        </ul>

        <p className="mt-8 text-sm leading-relaxed text-muted">
          The workspace runs these rules locally in the browser. A review you export is a record of
          the verdicts and of your decisions. It is not a resolved conversation.
        </p>
        <Link
          to="/"
          className={`mt-6 inline-flex min-h-11 items-center rounded-lg bg-sage px-4 text-sm font-medium text-bg ${focus}`}
        >
          Back to the workspace
        </Link>
      </article>
    </Frame>
  );
}

function Row({ term, value, note }: { term: string; value: number; note: string }) {
  return (
    <div className="grid gap-1 px-4 py-3 sm:grid-cols-[12rem_1fr] sm:gap-4">
      <dt className="text-sm text-fg">{term}</dt>
      <dd>
        <span className="font-mono text-sm tabular-nums text-sage">{value}</span>
        <span className="mt-1 block text-xs leading-relaxed text-muted">{note}</span>
      </dd>
    </div>
  );
}
