import { ensureHomeInitialized, loadAllowlist, loadPolicy } from "./config.js";
import { checkAuth } from "./github.js";
import { getDb } from "./state.js";
import { oocoderHome, paths } from "./paths.js";

export async function initOoCoder(home = oocoderHome()): Promise<{
  home: string;
  created: boolean;
  auth: Awaited<ReturnType<typeof checkAuth>>;
  allowlistRepos: number;
  allowlistOrgs: number;
  policyLabels: string[];
}> {
  const result = ensureHomeInitialized(home);
  getDb(home); // ensure schema
  const auth = await checkAuth();
  const allowlist = loadAllowlist(home);
  const policy = loadPolicy(home);
  const p = paths(home);

  return {
    home: p.home,
    created: result.created,
    auth,
    allowlistRepos: allowlist.repos.length,
    allowlistOrgs: allowlist.orgs.length,
    policyLabels: policy.labels,
  };
}
