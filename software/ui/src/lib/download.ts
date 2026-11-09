/**
 * Save a result file. With the real engine the browser (or the pywebview window) streams the
 * file straight to disk; in demo mode the file comes from the in-browser mock, so it is
 * fetched into a blob first.
 */
import { MOCK } from "../api/backend";
import { ApiError, api } from "../api/http";
import { toast } from "../store/toasts";

function clickLink(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export async function downloadFile(url: string, filename: string): Promise<void> {
  if (!MOCK) {
    clickLink(url, filename);
    return;
  }
  try {
    const res = await api.rawFetch(url);
    if (!res.ok) throw new ApiError(res.status, `The file isn't available (${res.status})`, "Make the mesh first.", url);
    const blob = await res.blob();
    const obj = URL.createObjectURL(blob);
    clickLink(obj, filename);
    setTimeout(() => URL.revokeObjectURL(obj), 30_000);
  } catch (e) {
    toast.error(`Couldn't download ${filename}`, e);
  }
}
