function readPublicEnv(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value ? value : undefined;
}

function readBooleanEnv(name: string, defaultValue = false): boolean {
  const value = readPublicEnv(name);
  if (value === undefined) {
    return defaultValue;
  }

  return value === "true" || value === "1";
}

export const publicConfig = {
  apiBaseUrl:
    readPublicEnv("NEXT_PUBLIC_API_BASE_URL") ?? "http://localhost:8000",
  githubUrl: readPublicEnv("NEXT_PUBLIC_GITHUB_URL") ?? "",
  architectureDocUrl: readPublicEnv("NEXT_PUBLIC_ARCHITECTURE_DOC_URL") ?? "",
  voiceDemoEnabled: readBooleanEnv("NEXT_PUBLIC_VOICE_DEMO_ENABLED", false),
} as const;

export type PublicConfig = typeof publicConfig;
