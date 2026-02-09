type ExpectedEnvVar = {
  key: string;
  value: string | boolean | undefined;
};

const EXPECTED_ENV_VARS: ExpectedEnvVar[] = [
  { key: 'VITE_API_URL', value: import.meta.env.VITE_API_URL },
  { key: 'VITE_SUPABASE_URL', value: import.meta.env.VITE_SUPABASE_URL },
  { key: 'VITE_SUPABASE_ANON_KEY', value: import.meta.env.VITE_SUPABASE_ANON_KEY },
  { key: 'VITE_NANGO_PUBLIC_KEY', value: import.meta.env.VITE_NANGO_PUBLIC_KEY },
];

export function logMissingEnvVars(): void {
  EXPECTED_ENV_VARS.forEach(({ key, value }) => {
    if (value === undefined || value === '') {
      console.debug(`[env] Warning: expected environment variable ${key} is not set.`);
    }
  });
}
