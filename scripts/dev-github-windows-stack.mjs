import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { main } from "./dev-with-automation.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openhands", "agent-canvas");
const stateDir =
  process.env.OH_CANVAS_SAFE_STATE_DIR ||
  process.env.STATE_DIR ||
  DEFAULT_STATE_DIR;
const automationDbPath = join(
  dirname(stateDir),
  "automation",
  "github-windows-stack.db",
);

process.env.OH_AUTOMATION_REPO ??=
  "https://github.com/jamiechicago312/automation.git";
process.env.OH_AUTOMATION_GIT_REF ??=
  "d20a68c1ef581370d1297da42a9b43a1de3c87b4";
process.env.AUTOMATION_DB_URL ??=
  `sqlite+aiosqlite:///${automationDbPath}`;

await main({
  bannerTitle: "Agent Canvas + Automation Development Stack (GitHub Windows test)",
});
