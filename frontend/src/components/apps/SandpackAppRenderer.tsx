/**
 * Renders a Penny App inside a sandboxed srcdoc iframe.
 *
 * Replaces the Sandpack-based approach with a simpler, more reliable method:
 * 1. React + ReactDOM + Plotly loaded from CDN as UMD globals
 * 2. Babel standalone transpiles JSX in-browser
 * 3. SDK + Plot shim inlined (no module bundler needed)
 * 4. Penny's code has imports stripped (everything is already in scope)
 *
 * The iframe uses sandbox="allow-scripts" for security isolation.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { APP_SDK_SOURCE, APP_STYLES, REACT_PLOTLY_SHIM } from "./appSdkSource";
import { apiRequest, API_BASE } from "../../lib/api";

// ---- types ----------------------------------------------------------------

interface AppTokenData {
  token: string;
  expires_at: string;
  app_id: string;
  api_base: string;
}

interface SandpackAppRendererProps {
  appId: string;
  frontendCode: string;
  embedToken?: string;
  onError?: (message: string) => void;
}

// ---- helpers --------------------------------------------------------------

/** Strip import/export statements so code can live in a shared Babel block. */
function stripModuleSyntax(code: string): string {
  return code
    // Remove import lines
    .replace(/^\s*import\s+.*?from\s+['"].*?['"];?\s*$/gm, "")
    // export function Foo → function Foo
    .replace(/export\s+function\s+/g, "function ")
    // export default function Foo → function Foo
    .replace(/export\s+default\s+function\s+/g, "function ")
    // export default <identifier>;
    .replace(/export\s+default\s+/g, "")
    // export { ... }
    .replace(/export\s+\{[^}]*\};?/g, "");
}

/**
 * Transform Penny's code for the srcdoc environment:
 * - Strip imports (everything is in global scope)
 * - Capture the default-exported component name
 */
function transformAppCode(code: string): { transformed: string; appName: string } {
  let appName = "App";

  // Capture name from `export default function Foo`
  const namedMatch = code.match(/export\s+default\s+function\s+(\w+)/);
  if (namedMatch?.[1]) {
    appName = namedMatch[1];
  } else {
    // Capture name from standalone `export default Foo`
    const defaultMatch = code.match(/export\s+default\s+(\w+)\s*;?/);
    if (defaultMatch?.[1]) {
      appName = defaultMatch[1];
    }
  }

  const transformed: string = code
    // Remove all import lines
    .replace(/^\s*import\s+.*?from\s+['"].*?['"];?\s*$/gm, "")
    // export default function Foo → function Foo
    .replace(/export\s+default\s+function\s+(\w+)/, "function $1")
    // export default Foo; → (remove)
    .replace(/export\s+default\s+\w+\s*;?/, "");

  return { transformed, appName };
}

/** Escape a string for safe embedding inside an HTML <script> block. */
function escapeForScript(s: string): string {
  return s.replace(/<\/script>/gi, "<\\/script>");
}

// ---- build the srcdoc HTML ------------------------------------------------

function buildSrcdocHtml(opts: {
  frontendCode: string;
  token: string;
  apiBase: string;
  appId: string;
}): string {
  const sdkInline: string = stripModuleSyntax(APP_SDK_SOURCE);
  const plotInline: string = stripModuleSyntax(REACT_PLOTLY_SHIM);
  const { transformed, appName } = transformAppCode(opts.frontendCode);

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"><${"/"}>script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"><${"/"}>script>
<script src="https://cdn.plot.ly/plotly-2.35.3.min.js"><${"/"}>script>
<script src="https://unpkg.com/@babel/standalone@7/babel.min.js"><${"/"}>script>
<style>${escapeForScript(APP_STYLES)}</style>
</head>
<body>
<div id="root"></div>

<script>
// Globals for the SDK
window.__REVTOPS_APP_TOKEN__ = ${JSON.stringify(opts.token)};
window.__REVTOPS_API_BASE__  = ${JSON.stringify(opts.apiBase)};
window.__REVTOPS_APP_ID__    = ${JSON.stringify(opts.appId)};

// Global error handler → show in UI + notify parent
window.onerror = function(msg, url, line, col, err) {
  var el = document.getElementById('root');
  if (el) {
    el.innerHTML = '<div style="color:#fca5a5;padding:1rem;font-family:monospace;font-size:12px;white-space:pre-wrap;">'
      + (err ? err.stack || err.message : msg) + '<' + '/div>';
  }
  try { window.parent.postMessage({ type:"app-error", error: String(msg) }, "*"); } catch(_){}
};
<${"/"}>script>

<script type="text/babel">
/* ---- React destructured ---- */
const { useState, useEffect, useCallback, useRef, useMemo, useReducer, useContext, createContext, Fragment } = React;

/* ---- SDK (inlined) ---- */
${escapeForScript(sdkInline)}

/* ---- Plot shim (inlined) ---- */
${escapeForScript(plotInline)}

/* ---- App code ---- */
${escapeForScript(transformed)}

/* ---- Boot ---- */
try {
  const _root = ReactDOM.createRoot(document.getElementById("root"));
  _root.render(React.createElement(typeof ${appName} !== "undefined" ? ${appName} : function() {
    return React.createElement("div", { style: { color: "#fca5a5", padding: "1rem" } }, "No component found");
  }));
} catch(e) {
  document.getElementById("root").innerHTML =
    '<div style="color:#fca5a5;padding:1rem;font-family:monospace;font-size:12px;white-space:pre-wrap;">' + e.message + '<' + '/div>';
  try { window.parent.postMessage({ type:"app-error", error: e.message }, "*"); } catch(_){}
}
<${"/"}>script>
</body>
</html>`;
}

// ---- component ------------------------------------------------------------

export function SandpackAppRenderer({
  appId,
  frontendCode,
  embedToken,
  onError,
}: SandpackAppRendererProps): JSX.Element {
  const [tokenData, setTokenData] = useState<AppTokenData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Listen for error messages from the iframe
  useEffect(() => {
    const handler = (event: MessageEvent): void => {
      const data = event.data as { type?: string; error?: string } | null;
      if (data?.type === "app-error" && data.error && onError) {
        onError(data.error);
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [onError]);

  const fetchToken = useCallback(async (): Promise<void> => {
    if (embedToken) {
      setTokenData({
        token: embedToken,
        expires_at: "",
        app_id: appId,
        api_base: API_BASE,
      });
      return;
    }

    const resp = await apiRequest<AppTokenData>(`/apps/${appId}/token`, {
      method: "POST",
    });

    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to get app token");
      return;
    }
    setTokenData(resp.data);
  }, [appId, embedToken]);

  useEffect(() => {
    void fetchToken();
  }, [fetchToken]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full min-h-[200px]">
        <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm max-w-md text-center">
          {error}
        </div>
      </div>
    );
  }

  if (!tokenData) {
    return (
      <div className="flex items-center justify-center h-full min-h-[200px]">
        <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  const resolvedApiBase: string =
    tokenData.api_base.startsWith("/")
      ? `${window.location.origin}${tokenData.api_base}`
      : tokenData.api_base;

  const srcdoc: string = buildSrcdocHtml({
    frontendCode,
    token: tokenData.token,
    apiBase: resolvedApiBase,
    appId,
  });

  return (
    <div className="w-full h-full min-h-[400px] relative">
      <iframe
        ref={iframeRef}
        srcDoc={srcdoc}
        sandbox="allow-scripts allow-same-origin"
        style={{
          width: "100%",
          height: "100%",
          minHeight: 400,
          border: "none",
          borderRadius: 8,
          background: "#18181b",
        }}
        title="Penny App"
      />
    </div>
  );
}
