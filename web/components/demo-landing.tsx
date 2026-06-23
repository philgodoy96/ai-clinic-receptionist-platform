import { publicConfig } from "@/lib/config";

import { DemoDisclaimer } from "./demo-disclaimer";

const demoBadges = [
  "Chat demo",
  "Voice-ready",
  "Safe demo mode",
  "Fictional clinic",
] as const;

function DemoBadge({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-zinc-200 bg-white px-3 py-1 text-xs font-medium text-zinc-600">
      {label}
    </span>
  );
}

function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-teal-700 underline-offset-4 hover:text-teal-900 hover:underline"
    >
      {children}
    </a>
  );
}

export function DemoLanding() {
  const voiceAvailable = publicConfig.voiceDemoEnabled;

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-16 sm:py-24">
      <div className="flex flex-wrap gap-2">
        {demoBadges.map((badge) => (
          <DemoBadge key={badge} label={badge} />
        ))}
      </div>

      <div className="mt-8 space-y-4">
        <h2 className="text-3xl font-semibold tracking-tight text-zinc-900 sm:text-4xl sm:leading-tight">
          AI receptionist for a fictional clinic
        </h2>
        <p className="text-lg leading-8 text-zinc-600">
          See how patients can book and manage appointments through chat and
          voice — backed by a production-style FastAPI receptionist platform.
        </p>
      </div>

      <div className="mt-10 flex flex-col gap-3 sm:flex-row sm:items-center">
        <button
          type="button"
          disabled
          title="Interactive chat UI coming in a later phase"
          className="inline-flex h-12 items-center justify-center rounded-full bg-teal-700 px-6 text-sm font-medium text-white transition-colors enabled:hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Talk to the receptionist
        </button>
        <button
          type="button"
          disabled={!voiceAvailable}
          title={
            voiceAvailable
              ? "Voice demo coming in a later phase"
              : "Voice demo is not enabled for this deployment"
          }
          className="inline-flex h-12 items-center justify-center rounded-full border border-zinc-300 bg-white px-6 text-sm font-medium text-zinc-900 transition-colors enabled:hover:border-zinc-400 enabled:hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Call the clinic
        </button>
      </div>

      <p className="mt-4 text-sm text-zinc-500">
        {voiceAvailable
          ? "Chat and voice entry points will connect to the backend in a later phase."
          : "Chat entry point coming soon. Voice remains disabled for this deployment."}
      </p>

      <div className="mt-10 flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {publicConfig.githubUrl ? (
          <ExternalLink href={publicConfig.githubUrl}>GitHub</ExternalLink>
        ) : null}
        {publicConfig.architectureDocUrl ? (
          <ExternalLink href={publicConfig.architectureDocUrl}>
            Architecture
          </ExternalLink>
        ) : null}
      </div>

      <div className="mt-8 border-t border-zinc-200 pt-8">
        <DemoDisclaimer />
      </div>
    </section>
  );
}
