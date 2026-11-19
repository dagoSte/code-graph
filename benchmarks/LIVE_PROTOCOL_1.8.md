# Paired live-agent pilot, second run (1.8)

Protocol fixed before live calls, 13 November 2026. It extends the
[1.7.0 protocol](LIVE_PROTOCOL.md), which stopped at the Claude account limit, and
keeps every rule of that protocol unless this page changes it. This is still a small
reproducible pilot, not a statistical estimate of time, token or quality effects.

## What changes from the first run

- **Four tasks instead of two.** The two frozen 1.7.0 tasks (`refund`, `falsy`) are
  repeated unchanged, and two larger tasks are added before any observation:
  - **`discount`:** loyalty members spending exactly 100.00 get no discount. The
    defect sits two calls below the checkout entry point
    (`checkout` → `order_total` → `member_discount`), and an unused
    `legacy/promo_rules.py` matches the words *loyalty discount* by name.
  - **`reserve`:** an order listing the same product on several lines is accepted
    even when the combined quantity exceeds stock. The defect sits one call below
    the entry point (`place_order` → `reserve_lines`), next to an unused
    `legacy_allocation.py` decoy.
  The intents of the two new tasks describe the behaviour a user observes and never
  name the function or file to change; a test enforces this.
- **Arm order alternates by task:** source-only first for tasks 1 and 3, graph-assisted
  first for tasks 2 and 4.
- **At most 24 live starts** (4 tasks × 2 arms × plan/edit/review).
- **Claude spending:** each review keeps `--max-budget-usd`, now 0.60 USD, and the
  whole run stops before a review could take the reported Claude total above
  **5 USD**, the owner's cap. A review whose cost the CLI does not report counts at its
  full cap. Codex uses the signed-in account; the owner confirmed that both accounts
  are available on 13 November 2026.
- The receipt format is `codegraph-live-pilot/2`; it adds the total cap, the reported
  Claude total and the stop reason.

## Unchanged

Author Codex, reviewer Claude; the same change flow, isolated worktrees, approved test
argv, host-side indexing in both arms, source-only arm limited to `cg_read`, removal of
host graph evidence from both arms' prompts, 180-second call deadline, one edit round,
no prompt tuning, no reruns, stop on quota/authentication failure or unconfirmed
cleanup, external frozen public tests, independent held-out cases, test and API
retention, a regression test that must fail on the original sources, native token
counts only (never estimates), and publication of sanitized receipts only.

## Reading the result

Four task pairs still cannot establish a general improvement. Report every path,
including failures, with its stages, checks, native counters, tool calls and whether
graph evidence was actually used. No task is excluded after observation.
