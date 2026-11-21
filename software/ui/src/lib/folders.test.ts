/**
 * Opening the app's folders and saving its log (UX_PLAN U39, U12, U34; `npm test`): the pages offer them by what they
 * are, never by a path. Reads the page sources as text (not run), as `words.test.ts` does.
 */
const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
const src = (p: string) => fs.readFileSync(new URL(p, import.meta.url), "utf8");

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`folders.test: ${what}`);
}

const settings = src("../pages/SettingsPage.tsx");
const project = src("../pages/ProjectPage.tsx");
const http = src("../api/http.ts");

// U12's leftover: the settings folder opens; its path is no longer copied
check(settings.includes('what="settings"') && settings.includes("Open the settings folder"), "Settings opens the settings folder");
check(!settings.includes("Copy the settings folder's path") && !settings.includes("copyText("), "no path to copy");
// U34: the log folder and a log for a bug report, in About
check(settings.includes('what="logs"') && settings.includes("Open the log folder"), "Settings opens the log folder");
check(settings.includes("<SaveLogButton"), "Settings saves a log for a bug report");
check(src("../components/FolderButtons.tsx").includes("Save a log for a bug report…"), "the log button's words");
// U39: the project folder opens from the project page
check(project.includes('what="project"') && project.includes("Open the project folder"), "the project page opens its folder");
// the page sends what the folder is, never a path
check(/openFolder: \(what: "settings" \| "logs" \| "project", projectId\?: string\)/.test(http), "openFolder takes a name, not a path");
check(!/openFolder[^\n]*path/.test(http), "no path in openFolder");

console.log(`folders.test: ${checks} checks passed`);
