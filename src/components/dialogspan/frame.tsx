import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { highlightText } from "@/lib/dialogspan/engine";
import type { Mark } from "@/lib/dialogspan/types";

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sage";

export function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Link
            to="/"
            className="flex min-h-11 items-center gap-3 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sage"
          >
            <span className="flex w-6 flex-col gap-1" aria-hidden="true">
              <span className="h-0.5 w-4 bg-sage" />
              <span className="h-0.5 w-6 bg-fg" />
              <span className="h-0.5 w-3 bg-clay" />
            </span>
            <span>
              <span className="block font-serif text-xl leading-none tracking-tight">DialogSpan</span>
              <span className="mt-1 block font-mono text-xs text-muted">PS-021 · AIORI-3</span>
            </span>
          </Link>
          <nav className="ml-auto flex items-center gap-1" aria-label="Primary">
            <Link
              to="/"
              activeOptions={{ exact: true }}
              className={`inline-flex min-h-11 items-center rounded-lg px-3 text-sm ${focusRing}`}
              activeProps={{ className: "text-fg" }}
              inactiveProps={{ className: "text-muted" }}
            >
              Workspace
            </Link>
            <Link
              to="/method"
              className={`inline-flex min-h-11 items-center rounded-lg px-3 text-sm ${focusRing}`}
              activeProps={{ className: "text-fg" }}
              inactiveProps={{ className: "text-muted" }}
            >
              Method
            </Link>
          </nav>
        </div>
      </header>
      {children}
    </div>
  );
}

const markClass = {
  agree: "rounded bg-sage-dim px-0.5 text-sage",
  conflict: "rounded bg-clay-dim px-0.5 text-clay underline decoration-clay",
  shared: "rounded bg-ochre-dim px-0.5 text-ochre",
} as const;

export function Marked({ text, marks }: { text: string; marks: Mark[] }) {
  const segments = highlightText(text, marks);
  return (
    <p className="text-sm leading-relaxed text-fg">
      {segments.map((segment, index) =>
        segment.kind === "plain" ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <mark key={index} className={markClass[segment.kind]}>
            {segment.text}
          </mark>
        ),
      )}
    </p>
  );
}
