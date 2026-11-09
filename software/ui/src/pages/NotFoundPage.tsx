import { House, MapPinOff } from "lucide-react";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/Feedback";
import { navigate, paths } from "../lib/router";

export function NotFoundPage({ path }: { path: string }) {
  return (
    <div className="grid h-full place-items-center">
      <EmptyState
        icon={MapPinOff}
        title="This page doesn't exist"
        actions={
          <Button variant="primary" icon={House} onClick={() => navigate(paths.home())}>
            Go to projects
          </Button>
        }
      >
        Nothing lives at <span className="font-mono text-xs">{path || "this address"}</span>.
      </EmptyState>
    </div>
  );
}
