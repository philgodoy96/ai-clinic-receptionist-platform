import { publicConfig } from "@/lib/config";

export function SiteFooter() {
  return (
    <footer className="border-t border-zinc-200 bg-white">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-6 text-sm text-zinc-600 sm:flex-row sm:items-center sm:justify-between">
        <p>Fictional clinic scenario. No real patient data.</p>
        <nav className="flex flex-wrap gap-4">
          {publicConfig.githubUrl ? (
            <a
              href={publicConfig.githubUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-teal-700 hover:text-teal-900"
            >
              GitHub
            </a>
          ) : null}
          {publicConfig.architectureDocUrl ? (
            <a
              href={publicConfig.architectureDocUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-teal-700 hover:text-teal-900"
            >
              Architecture docs
            </a>
          ) : null}
        </nav>
      </div>
    </footer>
  );
}
