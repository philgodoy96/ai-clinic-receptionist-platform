import { publicConfig } from "@/lib/config";

import { DemoDisclaimer } from "./demo-disclaimer";

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

type DemoLandingProps = {
  onStartChat: () => void;
  onStartVoice: () => void;
};

export function DemoLanding({ onStartChat, onStartVoice }: DemoLandingProps) {
  const voiceEnabled = publicConfig.voiceDemoEnabled;

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-16 sm:py-24">
      <div className="space-y-4">
        <h2 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl sm:leading-tight">
          AI receptionist for a fictional clinic
        </h2>
        <p className="text-lg leading-8 text-white-250">
          See how patients can book and manage appointments through chat and
          voice — backed by a production-style FastAPI receptionist platform.
        </p>
      </div>

      <div className="mt-10 flex flex-col gap-3 sm:flex-row sm:items-center">
        <button
          type="button"
          onClick={onStartChat}
          className="inline-flex h-12 items-center justify-center rounded-full bg-teal-700 px-6 text-sm font-medium text-white transition-colors hover:bg-teal-800"
        >
          Talk to the receptionist
        </button>
        <button
          type="button"
          onClick={onStartVoice}
          className="inline-flex h-12 items-center justify-center rounded-full border border-zinc-300 bg-white px-6 text-sm font-medium text-zinc-900 transition-colors hover:border-zinc-400 hover:bg-zinc-50"
        >
          Call the clinic
        </button>
      </div>

      <p className="mt-4 text-sm text-white-400">
        {voiceEnabled
          ? "Chat connects to the backend API. Voice entry uses a mock call until Retell is configured."
          : "Chat connects to the backend API. Voice opens a configuration preview until the feature flag is enabled."}
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
