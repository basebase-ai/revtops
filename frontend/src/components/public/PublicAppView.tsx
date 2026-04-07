/**
 * Standalone public app page at /public/apps/:id (no auth).
 */

import { SandpackAppRenderer } from "../apps/SandpackAppRenderer";

interface PublicAppViewProps {
  appId: string;
}

export function PublicAppView({ appId }: PublicAppViewProps): JSX.Element {
  return (
    <div className="min-h-screen bg-surface-950">
      <SandpackAppRenderer appId={appId} publicMode />
    </div>
  );
}
