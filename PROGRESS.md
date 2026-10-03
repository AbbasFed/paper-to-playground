# Progress

Updated after every step. Owners: `dev/` and `cases/real/` belong to teammates; the pipeline work below does not touch them.

## Done

- **Step 0 – git.** `origin` = github.com/AbbasFed/paper-to-playground. The dev QA tool (`dev/browser_check.py`,
  dev-only Playwright) was replayed onto the team history and pushed as a fast-forward. `.env` / `.env.*` are
  ignored, untracked and absent from every commit.
- **Step 1 – brief additions.**
  - a. Empty-content guard in `llm.chat`: empty content, visible tokens (completion − reasoning) ≤ 16, or
    `finish_reason: "length"` is logged as a failed call and retried once with the next lower reasoning effort
    (counts toward the 10-request cap). A cut-off reply that still fails is kept for repair.
  - b. `llm.model_info` reads the model's `reasoning` object from `GET /api/v1/models` (counted as a request);
    `llm.choose_reasoning` maps `--reasoning` onto `supported_efforts` / `mandatory`; `"exclude": true` is always
    sent; only `reasoning_tokens` counts are logged. The chosen setting is logged as `prepare/reasoning_setting`.
  - c. `checks.ground_labels`: section/equation labels must appear in the excerpt or another case field, otherwise
    they become "the provided excerpt" and an `assemble/label_grounding` event is logged.
  - d. Excerpts are trimmed only above 40,000 characters (was 14,000).
  - e. System prompt: iterative mechanisms get an integer iterations slider; `compute()` runs that many steps.
  - f. `--provider-prefs '<JSON>'` / `P2P_PROVIDER_PREFS` adds OpenRouter provider preferences; off by default.
  - Offline tests: `python -m unittest discover -s tests` (no API calls).
- **Step 2 – resizing bug.** Root cause: only vectors could be resized (`lengthFrom`), matrices could not, and
  dependent inputs were rebuilt only when a *slider* moved, so a size typed into a number box (the reported
  "typing 1" crash in the earlier template) left stale inputs. Fix, generic in the template:
  `PG.size()` (shared by page and checker) clamps every size to 1..declared; `PG.params()` cuts vectors and
  matrices (`rowsFrom` / `colsFrom` added); the page keeps full arrays and rebuilds any input whose size changed
  after *every* update. `checks.py` validates size links, makes size sliders integer with min 1, pads defaults and
  accepts smaller overrides for linked matrices. New hard check `resize_sweep` (harness): compute + render at every
  allowed size, 1 included, and exact shapes. Tests: `tests/test_resize.py` (V8 + a Chromium test of the real
  template that fails on the pre-fix template: matrix stayed 3×3; typed size ignored).
- Environment: tests run in a Python 3.11 venv (`.venv/`, ignored) with the pinned `requirements.txt` plus
  dev-only Playwright; the browser test is skipped where Playwright is absent.

- **Step 3 – "How it works".** Confirmed present: `templates/page.html` has `<section id="how">` (nav link
  `#how`) filled with `content.steps` as an ordered list; the committed attention example shows its 4 steps;
  `content_complete` fails (and triggers repair) with fewer than 2 steps. Added: a degraded page with no steps now
  shows a short note instead of an empty list. `tests/test_assemble.py` pins the rendering and all 7 sections.

- **Step 4 – Python 3.11.** Fresh CPython 3.11.15 venv with only `requirements.txt` (Windows): install OK,
  `compileall` OK, 25 tests OK (2 Playwright tests skipped, dev-only), `agent.py` runs (exit 2 without a key, as
  designed). Linux: `pip download --python-version 3.11 --only-binary=:all:` resolves every pin to a prebuilt wheel
  (`mini-racer` manylinux_2_27, i.e. glibc ≥ 2.27), so no compiler or system package is needed. Not executed on
  Linux itself (would need a Docker image pull). Real-model runs below use the 3.11 venv.

- **Step 5 – every check can fail.** `tests/test_checks_can_fail.py` feeds a deliberately broken variant of a
  known-good candidate to each of the 36 check names `checks.py` can emit (structure, JSON ×4, content, LaTeX,
  quotes ×2, controls ×3, explorations, test params, code ×4, V8 engine/load ×3, defaults, edge sweep, infinity,
  inert control, known-case test, missing tests, invariant, render ×2, resize sweep, readouts, final page ×4).
  Each one fails on its input; the unmodified candidate fails none; a guard test fails if a new check name
  appears without a failing case. Report: `python tests/test_checks_can_fail.py --report`.
  **Cannot fail:** only the no-excerpt variant of `quotes_grounded` (a soft pass by design: without an excerpt,
  quotes are omitted because nothing can verify them).

- **Step 6 prep.** `tools/bench.py` runs `agent.py` N times per case (in parallel), reads each `trace.jsonl` and
  writes `runs/<label>/summary.md|json`: total/prompt/completion/reasoning tokens (mean/min/max), API calls, wall
  seconds, repairs, checks passed, pass rate, exit codes, guard retries and relabelled citations. Key from the
  environment or a git-ignored `.env`, never printed.
- **Step 8 (interim).** `git grep -i "sk-or"` finds only the line in this file that names the command; no
  key-shaped string (`sk-or-v1-…`) in any commit on any branch; the committed trace has none. To be repeated over
  the run traces after step 6.

## Next

6. Real-model runs: every case ×3, with token/latency/repair statistics.
7. Reasoning A/B (`none` vs `low`), with a recommendation only.
8. Secret scan (`git grep -i "sk-or"`).
9. Rubric review of two generated pages.

## Known issues

- Real-model runs need `OPENROUTER_API_KEY` in the environment; it is not set on the machine running this work yet.
- `examples/attention/out/` was generated before the step 2 template change; regenerate it from a real run.
- `dev/browser_check.py` was written against an older template; its `SELECTORS` still name that template's ids
  and fall back to heading heuristics on `templates/page.html` (owned by teammates; not changed here).
