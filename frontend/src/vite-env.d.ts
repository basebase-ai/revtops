/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string | undefined;
  readonly VITE_SUPABASE_URL: string;
  readonly VITE_SUPABASE_ANON_KEY: string;
  readonly VITE_NANGO_PUBLIC_KEY: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
