/**
 * Open one of the app's folders, and save the app's log (UX_PLAN U39, U12, U34; APP_API.md "Folders and the log").
 * The page names the folder by what it is; the engine opens it in the computer's file manager. In the demo nothing
 * opens, and the button says so.
 */
import { FileDown, FolderOpen } from "lucide-react";
import { useState } from "react";
import { MOCK } from "../api/backend";
import { api } from "../api/http";
import { downloadFile } from "../lib/download";
import { toast } from "../store/toasts";
import { Button } from "./ui/Button";

type Size = "xs" | "sm" | "md";

export function OpenFolderButton({
  what,
  projectId,
  label,
  size = "sm",
  variant = "secondary",
}: {
  what: "settings" | "logs" | "project";
  projectId?: string;
  label: string;
  size?: Size;
  variant?: "secondary" | "ghost";
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size={size}
      variant={variant}
      icon={FolderOpen}
      loading={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await api.openFolder(what, projectId);
          if (MOCK) toast.info("In the demo, no folder opens", "With the app on your computer, the folder opens in your file manager.");
        } catch (e) {
          toast.error("Couldn't open the folder", e);
        } finally {
          setBusy(false);
        }
      }}
    >
      {label}
    </Button>
  );
}

/** U34: the app's log, saved as a text file to attach to a bug report. */
export function SaveLogButton({ size = "sm" }: { size?: Size }) {
  const stamp = () => {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  };
  return (
    <Button size={size} icon={FileDown} onClick={() => void downloadFile(api.logFileUrl, `openhandscan-log-${stamp()}.txt`)}>
      Save a log for a bug report…
    </Button>
  );
}
