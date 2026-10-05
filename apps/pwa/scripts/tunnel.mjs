// Boots an ngrok HTTP tunnel pointed at the local Vite preview server.
// Restricts access to a small allow-list of IPs (your phone's egress IP).
//
// Required env (load via `node --env-file=.env.local`):
//   PWA_ALLOWED_HOST — your ngrok static domain
//   PWA_ALLOWED_IPS  — comma-separated list of CIDRs (e.g. "1.2.3.4/32,5.6.7.8/32")
//
// Optional:
//   PWA_PREVIEW_PORT — port `pnpm preview` is bound to (default 4173)
//
// ngrok 3 enforces ACLs via traffic-policy files, so we generate one in
// os.tmpdir(), point ngrok at it, and clean it up on exit.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const host = process.env.PWA_ALLOWED_HOST;
const ipsRaw = process.env.PWA_ALLOWED_IPS;
const port = process.env.PWA_PREVIEW_PORT ?? "4173";

if (!host) {
  console.error("PWA_ALLOWED_HOST is not set. Add it to .env.local.");
  process.exit(1);
}
if (!ipsRaw) {
  console.error(
    "PWA_ALLOWED_IPS is not set. Add it to .env.local — comma-separated CIDRs " +
      'like "1.2.3.4/32".',
  );
  process.exit(1);
}

const ips = ipsRaw
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean)
  .map((entry) => {
    // Auto-append a host-mask if the user wrote a bare IP — ngrok rejects
    // anything that isn't valid CIDR.
    if (entry.includes("/")) return entry;
    return entry.includes(":") ? `${entry}/128` : `${entry}/32`;
  });
if (ips.length === 0) {
  console.error("PWA_ALLOWED_IPS is empty after parsing.");
  process.exit(1);
}

const dir = mkdtempSync(join(tmpdir(), "pwa-tunnel-"));
const policyPath = join(dir, "policy.yml");
const policy = `on_http_request:
  - actions:
      - type: restrict-ips
        config:
          enforce: true
          allow:
${ips.map((cidr) => `            - "${cidr}"`).join("\n")}
`;
writeFileSync(policyPath, policy, { mode: 0o600 });

let cleaned = false;
function cleanup() {
  if (cleaned) return;
  cleaned = true;
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    // best-effort
  }
}
process.on("exit", cleanup);
process.on("SIGINT", () => {
  cleanup();
  process.exit(130);
});
process.on("SIGTERM", () => {
  cleanup();
  process.exit(143);
});

const args = ["dlx", "ngrok", "http", `--url=${host}`, `--traffic-policy-file=${policyPath}`, port];
console.log(`Starting ngrok → https://${host} (port ${port})`);
console.log(`Allowed IPs: ${ips.join(", ")}`);

const child = spawn("pnpm", args, { stdio: "inherit" });
child.on("exit", (code) => {
  cleanup();
  process.exit(code ?? 0);
});
