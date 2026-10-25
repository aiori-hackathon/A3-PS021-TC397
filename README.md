# AIORI-3 — DialogSpan (PS-021)

Team repository for the AIORI-3 hackathon. Dialog correlation for multi-agent conversations.

## Team Introduction Video

YouTube: https://youtu.be/Sif5reYGPS4
website: https://dialogspan.vercel.app/

## What this is

DialogSpan takes turns from several agents, compares them with published rules, and shows one of three results: **correlated**, **contradiction**, or **inconclusive**. Turns that do not clear a link stay **unlinked**. Nothing is merged unless a person confirms it.

## How correlation works

The running rules are in `src/lib/dialogspan/engine.ts` and on the in-app Method page.

1. Split each agent transcript into turns on blank lines.
2. Compare every pair of turns that come from different agents.
3. Score Jaccard overlap of content words (fixed stop list, small synonym list).
4. Record anchors: ids, person names, dates, and measurements that match.
5. Record conflicts: a negation window, a different number in the same unit, or a listed antonym.
6. Label the pair.
   - Conflict plus enough shared talk → contradiction.
   - Overlap at or above 0.34, or a lower bar when a strong anchor is present, and no conflict → correlated.
   - A trace that misses those bars → inconclusive.
   - Below 0.12 with no anchor → no link.
7. Build spans from correlated and contradiction pairs only. An inconclusive pair never pulls a turn into those spans.

Evidence strength is how hard a rule fired. It is not a probability that a claim is true.

## What this system does not do

- It does not merge turns on its own.
- It does not invent a relationship when the evidence is thin.
- It does not treat a shared order id as the same claim.
- It does not pick a winner in a contradiction.
- It does not rewrite text into a blended answer.
- It does not call a language model.
- It does not understand anything outside its word lists, synonym list, and antonym list.
- Negation is a three-word window, not full semantic scope.

## Lab

The workspace ships five transcripts: a Pune shipment, a refund window, a Thursday clinic, a payments incident, and booking HK-204. Paste supports two to four agents. Separate turns with a blank line.

## Run

```bash
npm install
npm run dev
```

The app listens on port 8080.
