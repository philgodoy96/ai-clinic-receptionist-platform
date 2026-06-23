import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="border-b border-zinc-200 bg-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="group">
          <p className="text-sm font-medium uppercase tracking-[0.2em] text-teal-700">
            Public demo
          </p>
          <h1 className="text-lg font-semibold text-zinc-900 group-hover:text-teal-800">
            AI Clinic Receptionist
          </h1>
        </Link>
        <p className="hidden text-sm text-zinc-500 sm:block">
          Portfolio frontend shell
        </p>
      </div>
    </header>
  );
}
