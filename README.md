# Paper to Playground

An autonomous agent that turns a research-paper excerpt plus a learning brief into one self-contained,
offline, interactive explainer page for engineering undergraduates.

**TEAM MEMBERS: Abbas Yassine, Habib Shouman, Adam Al Khatib**

**MODEL_ID: `deepseek/deepseek-v4.1-flash`** (DeepSeek V4.1 Flash on OpenRouter; slug confirmed through
`GET https://openrouter.ai/api/v1/models`). The code is model-agnostic: it uses whatever `--model` is given.

## Setup and run

Python 3.11, no GPU, no system packages, no browser download, no Node. Every pinned dependency installs from a
prebuilt wheel (on Linux, `mini-racer` 0.14.1 needs glibc 2.27 or newer).

```
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY=...          # read from the environment only, never written anywhere
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

Outputs:

- `out/index.html` – one file with embedded CSS, JS and SVG. Open it directly or serve it with
  `python -m http.server`; it loads nothing from the network.
- `out/trace.jsonl` – the execution trace.

Exit code `0` when a working page was written, non-zero otherwise (the trace is always written).

Optional flags: `--reasoning {off,none,minimal,low,medium,high,xhigh,max,auto}` (default `off`; mapped to what
the model supports, see "Token efficiency"), `--provider-prefs '<JSON>'` (OpenRouter provider preferences;
**off by default**, also settable as `P2P_PROVIDER_PREFS`), `--temperature` (default 0.3), `--debug` (also saves
the raw model replies next to the page).

### Input

`case.json` is a UTF-8 JSON object. No field name is hard-coded: every field is passed to the model labelled by
its key. The excerpt is found by name (`excerpt`, `text`, `section`, `content`, ...) or, failing that, as the
longest text field, and sent whole unless it is longer than about 40,000 characters (a longer text is cut to the
part that best matches the brief). Only if a case has no excerpt at all does the agent try `source_url` once, with
a 3 second timeout. If the fetch fails (as it will when network access is limited to OpenRouter) the agent carries
on from the brief alone, and quotes are omitted because they cannot be verified.

## Architecture

One generation call, deterministic checks, and at most two targeted repair calls. The model never writes the
page: it writes *parts* (text, a control spec, and two pure JavaScript functions), and a fixed, paper-agnostic
template turns them into the page. No agent framework; a plain `requests` loop.

```
case.json
   |
[Prepare]   read all fields, locate the excerpt, open the trace, read the model's reasoning options (GET /models)
   |
[Generate]  1 LLM call -> <plan> <content> <controls> <compute> <render> <readouts> <tests>
   |
[Check]     Python checks + the generated JS executed in an embedded V8 engine (free, deterministic)
   |  ^
   |  +---- [Repair]  <= 2 LLM calls: only the failing tags and the exact error messages are sent,
   |                  only corrected tags come back, then everything is re-checked
   v
[Assemble]  best candidate -> fixed HTML template -> index.html, final page checks, trace, exit code
```

| File | Role |
| --- | --- |
| `agent.py` | CLI, pipeline, repair loop, exit codes |
| `llm.py` | OpenRouter client, usage capture, retries, hard budget guard |
| `checks.py` | tag parsing, spec normalisation, all checks, V8 harness driver |
| `assemble.py` | template injection and escaping |
| `trace.py` | JSONL trace writer |
| `prompts/system.txt`, `prompts/repair.txt` | the two prompts |
| `templates/page.html`, `style.css` | page layout and runtime (controls, live update loop) |
| `templates/helpers.js` | `H`: SVG charts/diagrams and math helpers used by generated code |
| `templates/harness.js` | the checker that runs inside V8 |
| `cases/` | six practice inputs |
| `tests/` | offline unit tests, no API calls: `python -m unittest discover -s tests` |
| `tools/bench.py` | repeated real runs over the practice cases with a token/latency/check summary (spends tokens) |
| `PROGRESS.md` | done / next / known issues |
| `examples/attention/` | one committed input/output pair |

### What every page contains

Title and citation, the idea and why it matters, the paper's governing equation, a symbols table, the mechanism
as ordered steps, an interactive playground (controls, an SVG visual, a table of intermediate and final values,
and live checks), two guided explorations (Change / Observe / Why, each with a button that applies the settings),
one limitation or common misunderstanding, and a source-grounding section. Blue blocks marked **From the paper**
hold the equation and verbatim quotes; dashed amber blocks marked **Our example / simplification** hold what the
demo invents, plus a fixed disclaimer that the demo does not reproduce the paper's experimental results.

Below the model's own visual the template adds automatic graphs: one chosen result plotted against each slider across
its whole range, with the current setting marked. These need no model tokens; every point is a `compute()` call.

Every number on the page comes from the generated `compute()` running in the browser on the current control
values. `render()` only draws what `compute()` returned.

A vector or matrix can be tied to integer size sliders (`lengthFrom`, `rowsFrom`, `colsFrom`, minimum 1). The page
keeps the full declared array and only passes the selected part to `compute()`, so shrinking and growing again
never loses typed values; any change that alters a size (slider, number box, exploration button, reset) rebuilds
the affected inputs.

## Checks and repairs

Each check is logged to the trace with pass / fail / warn and a message.

1. **Structure** – all tags present; JSON tags parse (tolerant of code fences, trailing commas and wrong closing tags).
2. **Content** – idea, why, formula, at least 2 symbols, steps, exactly 2 explorations each with change / observe / why,
   a limitation, section and equation, at least 1 simplification; leaked LaTeX is converted to Unicode/HTML, and any
   that remains fails the check.
3. **Grounding** – every quote must occur in the excerpt (whitespace- and case-normalised, fuzzy match >= 0.9).
   Unmatched quotes are dropped; if none survive the check fails and is repaired. The cited section and equation
   labels must also appear in the excerpt or another case field (e.g. `3.2.1`, `Section 6`, `(1)`, `Eq. 1`);
   otherwise they are replaced by "the provided excerpt" and the replacement is logged.
4. **Controls** – at least 2 valid controls, unique ids, defaults inside ranges; exploration and test settings must fit the controls.
5. **Purity** – generated code may not use `fetch`, `import()`, XHR, the DOM, `Math.random`, `Date` or URLs.
6. **Execution in V8** (`mini-racer`) –
   `compute(defaults)` runs and returns finite values;
   an edge sweep runs `compute` and `render` with every control at min and max, every option, and vectors and
   matrices filled with their extremes (so all-zero inputs occur): no exceptions, no NaN;
   every control must change at least one output;
   vectors and matrices tied to integer size sliders (`lengthFrom`, `rowsFrom`, `colsFrom`) are run at every size
   the slider allows, 1 included, and `compute` must receive exactly that shape (`resize_sweep`);
   the model's own `<tests>` (known cases with hand-derivable answers) must be true, and its invariants must hold
   at every swept setting;
   `render` must return an SVG with no `NaN` / `undefined` in it;
   readout keys must resolve;
   every decimal number quoted in an exploration's "observe" text must be a value `compute` really produces at the
   settings that exploration describes (the model's prose is otherwise unverified).
   Numbers in scientific notation are understood. An exploration that merely reloads the default settings is logged
   as a warning; the page then tells the learner the settings are already loaded.
7. **Final page** – no remote `src`/`href`/`url()`, no `fetch`/`import`/XHR/`<link>`, every inline script parses,
   size under 1 MB, API key absent.

**Repair.** If a hard check fails and budget remains, the agent sends the brief, the exact failure messages and
only the tags involved, and asks for only the corrected tags. The reply is merged and everything is re-checked;
a repair that makes things worse is rejected. At most two rounds; the second is skipped when the only remaining
failures are known-case tests, which are removed from the page instead.

**Fallback.** The best candidate so far is always kept. When budget or time runs out the agent still assembles
it; the page runtime shows an inline error rather than a blank page if a part is broken. Exit code is 0 only if
`compute` and `render` work at the defaults with at least two valid controls.

The tests that pass are also shown on the page as **Live checks**: invariants are re-evaluated on the current
settings at every change, and known cases have a button that loads them into the playground.

## Limits enforcement

Enforced in `llm.py` by a single `Budget` object consulted before every HTTP attempt:

| Limit (assignment) | Guard |
| --- | --- |
| 10 minutes wall clock | global deadline of 510 s; per-call timeout `min(150 s, time left - 10 s)` with a hard thread join |
| 10 API requests incl. retries | every HTTP attempt increments the counter, including the one `GET /models` lookup; no call is made at 10 |
| 30,000 completion tokens | `max_tokens` is set on every call to `min(planned, remaining - 500)`; planned is 9000 (generate) and 5000 (repair) |

Retries (429, 5xx, timeouts, empty replies) use a short backoff and count toward the request cap. A call that
returns no usage (a timeout or dropped connection) is assumed to have spent its whole `max_tokens`, so the
completion-token cap holds even in the worst case. Typical use
is 1 to 2 requests and roughly 5k to 9k total tokens per case.

**Token efficiency.** Reasoning is disabled by default (`"reasoning": {"enabled": false}`). Measured on the
practice cases with `deepseek/deepseek-v4.1-flash`: with `effort: low` a case cost 12k to 27k completion tokens
and 3 to 8 minutes, and two of three runs ran out of budget; with reasoning off the same cases cost about 3k
completion tokens and finish in well under a minute with all checks passing. If a model rejects the
reasoning setting, the call is retried without it.

Before the first call the agent reads the model's `reasoning` object from `GET /api/v1/models`
(`supported_efforts`, `mandatory`) and maps `--reasoning` onto it: `off` sends `{"enabled": false}` unless reasoning
is mandatory (then the lowest supported effort), and an effort the model does not offer becomes the next lower one.
`"exclude": true` is always sent, so reasoning text is never returned; only the `reasoning_tokens` count is logged.

**Empty-content guard.** A reply that is empty (no content, or completion tokens minus reasoning tokens close to
zero) or cut off (`finish_reason: "length"`) is logged as a failed call and retried once with the next lower
reasoning effort; the retry counts toward the 10-request cap. If the retry also fails, a cut-off reply is kept and
its missing parts are left to the repair step.

**Iterative mechanisms.** The system prompt asks for an integer iterations slider (max 100) whenever the mechanism
is an update applied repeatedly, with `compute()` running exactly that many steps and returning the history.

## Trace format

`trace.jsonl` has one JSON object per event, flushed as it happens:

```
{"t": 12.05, "stage": "generate", "action": "llm_call", "result": "ok", "request_no": 1, "model": "...",
 "generation_id": "gen-...", "prompt_tokens": 2161, "completion_tokens": 2943, "reasoning_tokens": 0,
 "elapsed_s": 12.05, "finish_reason": "stop", ...}
{"t": 12.11, "stage": "check", "action": "check:edge_sweep", "result": "pass", "message": "...", "round": 0}
{"t": 12.11, "stage": "repair", "action": "revision", "result": "requested", "round": 1, "tags": [...], "reasons": [...]}
{"t": 28.70, "stage": "done", "action": "exit", "result": "success", "exit_code": 0, "requests": 1,
 "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ..., "elapsed_s": ..., "checks_run": 26, "checks_passed": 26}
```

Every event has `t` (seconds since start), `stage` (`start`, `prepare`, `generate`, `plan`, `check`, `repair`,
`assemble`, `done`, `error`), `action` and `result`. LLM events carry token counts, latency and the OpenRouter
generation id; the `plan` event records the model's stated plan for the explanation (its `<plan>` tag); check events
carry pass/fail and a message; repair events record which tags were revised and why.
The trace never contains the API key (lines are scrubbed as a second line of defence), request headers, or any
model reasoning text.

## Example

`examples/attention/case.json` is the input; `examples/attention/out/index.html` and
`examples/attention/out/trace.jsonl` are the unedited output of

```
python agent.py --input examples/attention/case.json --output examples/attention/out --model deepseek/deepseek-v4.1-flash
```

## Reuse and credits

- [`requests`](https://pypi.org/project/requests/) (Apache-2.0) for HTTP.
- [`mini-racer`](https://pypi.org/project/mini-racer/) (ISC), an embedded V8 engine shipped as a pip wheel, to
  execute the generated JavaScript during checks.
- `H.rng` uses the public-domain mulberry32 pseudo-random generator.
- The page layout (navbar, hero with stat cards, centred section headings, numbered cards) is modelled on the
  "Eduleb" HTML education template; no code, fonts or images from that template are included, the CSS was
  written for this project.
- `templates/aub_logo.png` is the American University of Beirut logo from aub.edu.lb, embedded in each page as a
  data URI. It is the property of AUB and is used to identify the course this project was built for.
- Everything else (pipeline, prompts, template, helper library, checker) was written for this project. The
  templates and helpers are generic; the repository contains no paper-specific answers or pages apart from the
  example output above, which the agent produced. The practice excerpts in `cases/` are short quotations from
  the cited papers.
