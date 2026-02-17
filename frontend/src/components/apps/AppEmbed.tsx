/**
 * Standalone embed page at /embed/:id?token=...
 *
 * Minimal chrome – just the Sandpack renderer with token-based auth.
 * Designed to be iframed into external dashboards, Notion, Slack canvas, etc.
 */

import { useState, useEffect, useCallback } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import { API_BASE } from "../../lib/api";

interface AppDetail {
  id: string;
  title: string | null;
  frontend_code: string;
}

export function AppEmbed(): JSX.Element {
  const [app, setApp] = useState<AppDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Parse app ID and token from URL
  const pathParts: string[] = window.location.pathname.split("/");
  const appId: string = pathParts[pathParts.length - 1] ?? "";
  const params = new URLSearchParams(window.location.search);
  const token: string = params.get("token") ?? "";

  const fetchApp = useCallback(async (): Promise<void> => {
    if (!appId || !token) {
      setError("Missing app ID or token");
      setLoading(false);
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/apps/${appId}/embed-data`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        setError("Failed to load app. The embed token may have expired.");
        setLoading(false);
        return;
      }

      const data = (await response.json()) as AppDetail;
      setApp(data);
    } catch {
      setError("Failed to load app");
    } finally {
      setLoading(false);
    }
  }, [appId, token]);

  useEffect(() => {
    void fetchApp();
  }, [fetchApp]);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "#18181b",
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            border: "3px solid #3f3f46",
            borderTop: "3px solid #6366f1",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite",
          }}
        />
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </div>
    );
  }

  if (error || !app) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "#18181b",
          color: "#fca5a5",
          fontFamily: "system-ui, sans-serif",
          padding: "2rem",
          textAlign: "center",
        }}
      >
        {error ?? "App not found"}
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", background: "#18181b" }}>
      <SandpackAppRenderer
        appId={appId}
        frontendCode={app.frontend_code}
        embedToken={token}
      />
    </div>
  );
}
