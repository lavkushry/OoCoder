import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadPolicy } from "./config.js";
import { getPull, parsePullUrl } from "./github.js";
import { oocoderHome } from "./paths.js";
import { getDb, listCandidates, logEvent } from "./state.js";

export interface DashboardModel {
  generatedAt: string;
  days: number;
  kpis: {
    actionsTotal: number;
    actionsToday: number;
    prsOpened: number;
    merged: number;
    acceptanceRate: number;
    reposActive: number;
    cooldownsActive: number;
  };
  timeline: Array<{ day: string; kind: string; n: number }>;
  funnel: Array<{ stage: string; n: number }>;
  repos: Array<{ repo: string; status: string; n: number }>;
  prs: Array<{ pr: string; repo: string; state: string; title: string }>;
  fixes: Array<{ outcome: string; n: number }>;
  friction: Array<{ reason: string; n: number }>;
  lessons: Array<{ ts: string; repo: string; lesson: string }>;
}

function dayKey(isoOrUnix: string | number): string {
  if (typeof isoOrUnix === "number") {
    return new Date(isoOrUnix * 1000).toISOString().slice(0, 10);
  }
  return String(isoOrUnix).slice(0, 10);
}

export function loadDashboardModel(
  home = oocoderHome(),
  days = 30,
  now = new Date(),
): DashboardModel {
  const db = getDb(home);
  const sinceIso = new Date(now.getTime() - days * 86400000).toISOString();
  const today = now.toISOString().slice(0, 10);

  const actionsTotal = Number(
    (db.prepare(`SELECT COUNT(*) AS n FROM candidates`).get() as { n: number }).n,
  );
  const actionsToday = Number(
    (
      db
        .prepare(`SELECT COUNT(*) AS n FROM candidates WHERE substr(updated_at,1,10) = ?`)
        .get(today) as { n: number }
    ).n,
  );
  const prsOpened = Number(
    (
      db
        .prepare(`SELECT COUNT(*) AS n FROM candidates WHERE status = 'pr_opened' OR pr_url IS NOT NULL`)
        .get() as { n: number }
    ).n,
  );
  const merged = Number(
    (
      db
        .prepare(`SELECT COUNT(*) AS n FROM events WHERE kind = 'pr_state' AND payload LIKE '%"state":"merged"%'`)
        .get() as { n: number }
    ).n,
  );
  const reposActive = Number(
    (
      db.prepare(`SELECT COUNT(DISTINCT owner || '/' || repo) AS n FROM candidates`).get() as {
        n: number;
      }
    ).n,
  );
  const observed = Number(
    (
      db
        .prepare(
          `SELECT COALESCE(SUM(candidates),0) AS n FROM observations WHERE kind NOT LIKE 'skip:%' AND ts >= ?`,
        )
        .get(Date.parse(sinceIso) / 1000) as { n: number }
    ).n,
  );
  const skips = db
    .prepare(
      `SELECT kind, SUM(candidates) AS n FROM observations WHERE kind LIKE 'skip:%' GROUP BY kind`,
    )
    .all() as Array<{ kind: string; n: number }>;

  const candidates = listCandidates(undefined, home);
  const timelineMap = new Map<string, number>();
  for (const row of candidates) {
    if (row.updatedAt < sinceIso) continue;
    const key = `${dayKey(row.updatedAt)}|${row.status}`;
    timelineMap.set(key, (timelineMap.get(key) ?? 0) + 1);
  }
  const timeline = [...timelineMap.entries()].map(([key, n]) => {
    const [day, kind] = key.split("|");
    return { day: day ?? "", kind: kind ?? "", n };
  });

  const repoMap = new Map<string, number>();
  for (const row of candidates) {
    const key = `${row.owner}/${row.repo}|${row.status}`;
    repoMap.set(key, (repoMap.get(key) ?? 0) + 1);
  }
  const repos = [...repoMap.entries()].map(([key, n]) => {
    const [repo, status] = key.split("|");
    return { repo: repo ?? "", status: status ?? "", n };
  });

  const prs = candidates
    .filter((row) => row.prUrl)
    .slice(0, 25)
    .map((row) => ({
      pr: row.prUrl ?? "",
      repo: `${row.owner}/${row.repo}`,
      state: row.status,
      title: row.title,
    }));

  let fixes: Array<{ outcome: string; n: number }> = [];
  const tables = db
    .prepare(`SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ci_fix_attempts'`)
    .get() as { name: string } | undefined;
  if (tables) {
    fixes = (
      db
        .prepare(`SELECT outcome, COUNT(*) AS n FROM ci_fix_attempts GROUP BY outcome`)
        .all() as Array<{ outcome: string; n: number }>
    ).map((row) => ({ outcome: row.outcome, n: Number(row.n) }));
  }

  const lessons = (
    db
      .prepare(
        `SELECT created_at, ref, payload FROM events WHERE kind = 'lesson' ORDER BY id DESC LIMIT 20`,
      )
      .all() as Array<{ created_at: string; ref: string | null; payload: string }>
  ).map((row) => ({
    ts: row.created_at,
    repo: row.ref ?? "",
    lesson: row.payload,
  }));

  const acceptanceRate = prsOpened === 0 ? 0 : Math.round((merged / prsOpened) * 100);

  return {
    generatedAt: now.toISOString(),
    days,
    kpis: {
      actionsTotal,
      actionsToday,
      prsOpened,
      merged,
      acceptanceRate,
      reposActive,
      cooldownsActive: skips.reduce((sum, row) => sum + Number(row.n), 0),
    },
    timeline,
    funnel: [
      { stage: "observed", n: observed },
      { stage: "acted", n: actionsTotal },
      { stage: "pr", n: prsOpened },
      { stage: "merged", n: merged },
    ],
    repos,
    prs,
    fixes,
    friction: skips.map((row) => ({
      reason: row.kind.replace(/^skip:/, ""),
      n: Number(row.n),
    })),
    lessons,
  };
}

function esc(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function chartSource(): string {
  const asset = join(dirname(fileURLToPath(import.meta.url)), "../assets/chart.umd.js");
  try {
    return readFileSync(asset, "utf8");
  } catch {
    return "globalThis.Chart = globalThis.Chart || function Chart(){ this.destroy = function(){}; };";
  }
}

export function renderDashboard(model: DashboardModel, chartJs = chartSource()): string {
  const data = JSON.stringify(model);
  const prRows = model.prs
    .map(
      (row) =>
        `<tr><td><a href="${esc(row.pr)}">${esc(row.pr)}</a></td><td>${esc(row.repo)}</td><td>${esc(row.state)}</td><td>${esc(row.title)}</td></tr>`,
    )
    .join("");
  const lessonRows = model.lessons
    .map(
      (row) =>
        `<tr><td>${esc(row.ts)}</td><td>${esc(row.repo)}</td><td>${esc(row.lesson)}</td></tr>`,
    )
    .join("");
  const fixRows = model.fixes
    .map((row) => `<tr><td>${esc(row.outcome)}</td><td>${row.n}</td></tr>`)
    .join("");
  const frictionRows = model.friction
    .map((row) => `<tr><td>${esc(row.reason)}</td><td>${row.n}</td></tr>`)
    .join("");

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>OoCoder stats</title>
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #f6f4ef; color: #1c1917; }
  main { max-width: 1100px; margin: 0 auto; padding: 24px; display: grid; gap: 16px; }
  @media (min-width: 1200px) { main { grid-template-columns: 1fr 1fr; } header, #kpi, #timeline, #funnel { grid-column: 1 / -1; } }
  section, header { background: white; border: 1px solid #e7e5e4; border-radius: 12px; padding: 16px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
  .card { background: #fafaf9; border-radius: 8px; padding: 10px; }
  .card b { display: block; font-size: 1.4rem; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #e7e5e4; }
  canvas { width: 100%; max-height: 220px; }
</style>
</head>
<body>
<main>
<header>
  <h1>OoCoder stats</h1>
  <p>Generated ${esc(model.generatedAt)} · window ${model.days} days · data: memory sqlite</p>
</header>
<section id="kpi">
  <h2>KPI</h2>
  <div class="cards">
    <div class="card"><b>${model.kpis.actionsTotal}</b>actions</div>
    <div class="card"><b>${model.kpis.actionsToday}</b>today</div>
    <div class="card"><b>${model.kpis.prsOpened}</b>PRs</div>
    <div class="card"><b>${model.kpis.merged}</b>merged</div>
    <div class="card"><b>${model.kpis.acceptanceRate}%</b>acceptance</div>
    <div class="card"><b>${model.kpis.reposActive}</b>repos</div>
    <div class="card"><b>${model.kpis.cooldownsActive}</b>skips</div>
  </div>
</section>
<section id="timeline"><h2>Activity</h2><canvas id="chart-timeline"></canvas><p>${model.timeline.map((row) => `${esc(row.day)} ${esc(row.kind)} ${row.n}`).join(" · ") || "none"}</p></section>
<section id="funnel"><h2>Funnel</h2><canvas id="chart-funnel"></canvas><p>${model.funnel.map((row) => `${esc(row.stage)} ${row.n}`).join(" → ")}</p></section>
<section id="repos"><h2>Repos</h2><ul>${model.repos.map((row) => `<li>${esc(row.repo)} ${esc(row.status)} ${row.n}</li>`).join("") || "<li>none</li>"}</ul></section>
<section id="prs"><h2>PR lifecycle</h2><table><tbody>${prRows || "<tr><td>none</td></tr>"}</tbody></table></section>
<section id="fixes"><h2>CI fixes</h2><canvas id="chart-fixes"></canvas><table><tbody>${fixRows || "<tr><td>none</td></tr>"}</tbody></table></section>
<section id="friction"><h2>Friction</h2><table><tbody>${frictionRows || "<tr><td>none</td></tr>"}</tbody></table></section>
<section id="lessons"><h2>Lessons</h2><table><tbody>${lessonRows || "<tr><td>none</td></tr>"}</tbody></table></section>
</main>
<script type="application/json" id="oocoder-data">${data}</script>
<script>const DATA = JSON.parse(document.getElementById("oocoder-data").textContent);</script>
<script>${chartJs}</script>
<script>
  if (typeof Chart === "function") {
    const text = document.getElementById("chart-timeline");
    if (text) new Chart(text, { type: "bar", data: { labels: DATA.timeline.map(function(r){return r.day}), datasets: [{ data: DATA.timeline.map(function(r){return r.n}) }] } });
  }
</script>
<footer style="padding:16px;text-align:center">generated by oocoder — data: memory.sqlite3</footer>
</body>
</html>
`;
}

export async function refreshPrStates(home = oocoderHome(), limit = 50): Promise<number> {
  const rows = listCandidates("pr_opened", home).filter((row) => row.prUrl).slice(0, limit);
  let updated = 0;
  for (const row of rows) {
    if (!row.prUrl) continue;
    const ref = parsePullUrl(row.prUrl);
    const pull = await getPull(ref.owner, ref.repo, ref.number);
    const merged = Boolean(pull.merged);
    const state = merged ? "merged" : String(pull.state ?? "open");
    logEvent("pr_state", `${ref.owner}/${ref.repo}`, { state, number: ref.number }, home);
    updated += 1;
  }
  return updated;
}

export async function writeDashboard(options: {
  home?: string;
  out: string;
  days?: number;
  refresh?: boolean;
}): Promise<{ out: string; model: DashboardModel }> {
  const home = options.home ?? oocoderHome();
  const policy = loadPolicy(home);
  const days = options.days ?? policy.dashboard.historyDays;
  if (options.refresh ?? policy.dashboard.refreshPrStates) {
    try {
      await refreshPrStates(home);
    } catch {
      // offline generation still writes from the database
    }
  }
  const model = loadDashboardModel(home, days);
  const html = renderDashboard(model);
  writeFileSync(options.out, html, "utf8");
  return { out: options.out, model };
}
