function readOptionalEnv(value: string | undefined): string {
  const trimmed = value?.trim();
  return trimmed ?? "";
}

export const publicConfig = {
  apiBaseUrl:
    readOptionalEnv(process.env.NEXT_PUBLIC_API_BASE_URL) ||
    "http://localhost:8000",
  githubUrl: readOptionalEnv(process.env.NEXT_PUBLIC_GITHUB_URL),
  architectureDocUrl: readOptionalEnv(process.env.NEXT_PUBLIC_ARCHITECTURE_DOC_URL),
  voiceDemoEnabled:
    process.env.NEXT_PUBLIC_VOICE_DEMO_ENABLED === "true" ||
    process.env.NEXT_PUBLIC_VOICE_DEMO_ENABLED === "1",
} as const;

export type PublicConfig = typeof publicConfig;
