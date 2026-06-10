/**
 * Test harness for cross-platform GitHub skills + automation bootstrap PRs.
 *
 * Points the automation backend at OpenHands/automation PR #179
 * (fix/preset-cross-platform-178) so preset bootstraps use Python instead of
 * bash. The @openhands/extensions dependency in package.json is already
 * pinned to OpenHands/extensions PR #324 (fix/github-skills-cross-platform-323).
 *
 * Usage:
 *   npm run dev:test-cross-platform
 */
import { main } from "./dev-with-automation.mjs";

// Point automation backend at the cross-platform bootstrap PR branch.
process.env.OH_AUTOMATION_REPO ??=
  "https://github.com/jamiechicago312/automation.git";
process.env.OH_AUTOMATION_GIT_REF ??= "fix/preset-cross-platform-178";

await main({
  bannerTitle:
    "Agent Canvas – Cross-Platform GitHub Skills Test Stack",
});
