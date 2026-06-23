import { publicConfig } from "@/lib/config";

function StatusBadge({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "neutral" | "ready" | "disabled";
}) {
  const toneClasses = {
    neutral: "border-zinc-200 bg-white text-zinc-700",
    ready: "border-teal-200 bg-teal-50 text-teal-900",
    disabled: "border-amber-200 bg-amber-50 text-amber-900",
  }[tone];

  return (
    <div className={`rounded-xl border px-4 py-3 ${toneClasses}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        {label}
      </p>
      <p className="mt-1 font-mono text-sm">{value}</p>
    </div>
  );
}

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-10 px-6 py-12">
      <section className="max-w-3xl space-y-4">
        <p className="text-sm font-medium uppercase tracking-[0.18em] text-teal-700">
          Backend-powered receptionist demo
        </p>
        <h2 className="text-3xl font-semibold tracking-tight text-zinc-900 sm:text-4xl">
          Chat-first public demo shell
        </h2>
        <p className="text-lg leading-8 text-zinc-600">
          This frontend is a lightweight portfolio entry point for the FastAPI
          receptionist backend. It exposes public configuration only and does
          not embed provider keys or a full operations dashboard.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <StatusBadge
          label="API base URL"
          value={publicConfig.apiBaseUrl}
          tone="neutral"
        />
        <StatusBadge
          label="Voice demo"
          value={
            publicConfig.voiceDemoEnabled
              ? "Enabled (placeholder)"
              : "Disabled"
          }
          tone={publicConfig.voiceDemoEnabled ? "ready" : "disabled"}
        />
      </section>

      <section className="rounded-2xl border border-dashed border-zinc-300 bg-white p-8">
        <h3 className="text-lg font-semibold text-zinc-900">
          Interactive chat UI
        </h3>
        <p className="mt-2 max-w-2xl leading-7 text-zinc-600">
          The chat experience will connect to{" "}
          <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-sm">
            {publicConfig.apiBaseUrl}
          </code>{" "}
          in a later phase. Retell web voice is intentionally not wired in this
          scaffold.
        </p>
      </section>
    </main>
  );
}
