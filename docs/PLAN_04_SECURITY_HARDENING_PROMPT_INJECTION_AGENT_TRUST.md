# Security Hardening Plan
## Support Triage Copilot — Prompt Injection & Agent Trust

**Version 1.0 · March 2026**

## 1. Background — What are invisible character attacks?

You recently learned about a class of prompt injection attack that uses invisible Unicode characters. This section explains what they are and why they matter specifically for your system.

### What they are
Certain Unicode code points render as nothing visible in email clients and web browsers, but are fully readable by a language model. An attacker can embed instructions inside these invisible characters — for example, inside a legitimate-looking support email — and the LLM will process them as real instructions.

### Common character ranges used
- Zero-width spaces and joiners: `U+200B` through `U+200F`
- Bidirectional control characters: `U+202A` through `U+202E`
- Word joiner and invisible separators: `U+2060` through `U+2064`
- Byte order mark (BOM): `U+FEFF`
- HTML-based hiding: `display:none`, `font-size:0`, white-on-white colour

### What an attacker could instruct the LLM to do
- Change the `case_type` or `severity` in the triage JSON output
- Inject a tool name into `suggested_tools` to invoke a privileged operation
- Craft a draft reply that tricks the operator into approving a harmful message
- Poison the learning loop by making an injected example look like a valid approval

The risk is real. Your system already has several good defences (schema validation, human approval gate, allowlist tool registry). This plan fills the remaining gaps.

## 2. Current security posture

The table below maps your existing architecture against the key controls discussed. Green items are already covered; orange items are partially covered; red items are missing.

| Area | Current state | Status |
| --- | --- | --- |
| Invisible char stripping | Not present at ingress | Missing |
| HTML hidden-content stripping | Not present | Missing |
| Schema validation on triage JSON | Exists in triage worker | Done |
| Allowlist tool registry | `tools/registry.py` | Done |
| LLM influence over tool selection | `suggested_tools` from LLM is trusted | Missing |
| Human approval gate | No auto-send enforced | Done |
| PII redaction before LLM | Redaction stage exists | Done |
| Learning loop quarantine | Approvals feed directly to few-shot | Missing |
| Orchestrator / hard stop on bad JSON | Partial — daemon supervises | Partial |

## 3. Threat model

Understanding the attack path helps prioritise fixes. The most likely attack path in your system is:

1. Attacker sends a support email with invisible Unicode instructions embedded.
2. `imap_ingest_db.py` fetches the email and inserts the raw text into the SQLite queue without stripping.
3. `triage_worker.py` feeds the raw text (after redaction but before Unicode sanitisation) to the LLM.
4. The LLM reads the injected instruction and outputs a manipulated triage JSON — for example with an unexpected tool in `suggested_tools`.
5. The worker trusts `suggested_tools` and invokes a tool the attacker chose.
6. If the operator approves the resulting draft, the injected example enters the few-shot learning pool.

Steps 1–4 are the highest priority to fix. Steps 5–6 are secondary mitigations.

## 4. Implementation plan

Four phases, ordered by impact and effort. Each phase is independent — you can ship them one at a time.

| Phase | Task | File / location | Effort |
| --- | --- | --- | --- |
| Phase 1 | Unicode sanitiser at ingress | `app/ingress.py` or `tools/ingest_eml.py` | 30 min |
| Phase 1 | Strip hidden HTML content at ingress | `app/ingress.py` | 1 hour |
| Phase 2 | Remove LLM control over tool selection | `tools/triage_worker.py` | 1–2 hours |
| Phase 2 | Hard-stop orchestrator on schema failure | `tools/triage_worker.py` / `daemon.py` | 1 hour |
| Phase 3 | Quarantine flag on learning samples | DB schema + `run_learning_cycle.py` | 1–2 hours |
| Phase 4 | Add PowerShell / future skill agents | `tools/registry.py` + new agent files | Variable |

### Phase 1 — Sanitise input at ingress (highest priority)

This is the first and most important fix. All input must be sanitised before it touches the queue or the LLM.

#### 1a. Unicode invisible character stripping
Add a `sanitize()` function and call it at every ingress point before the queue insert. The function should:
- Strip all characters in the invisible/control Unicode ranges listed in Section 1
- Normalise the remaining text to `NFKC` (composed, compatibility form)
- Optionally log a warning if invisible characters were found — this is a useful signal

Where to add this call:
- `tools/ingest_eml.py` — before inserting into the queue
- `tools/imap_ingest_db.py` — before inserting into the queue
- `app/routes` or `/triage/enqueue` API handler — before queue insert

Suggested implementation location: a shared `app/sanitize.py` module imported by all three ingress points.

```python
# app/sanitize.py
import re, unicodedata

INVISIBLE = re.compile(
    r'[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]'
)

def sanitize_text(text: str) -> tuple[str, bool]:
    had_invisible = bool(INVISIBLE.search(text))
    text = INVISIBLE.sub('', text)
    text = unicodedata.normalize('NFKC', text)
    return text, had_invisible
```

#### 1b. HTML hidden-content stripping
If emails arrive as HTML (from IMAP or the API), parse them with BeautifulSoup before the text is extracted. Remove any element with `display:none`, `visibility:hidden`, `font-size:0`, or color matching the background. Extract only the visible text for LLM processing.
- Install: `beautifulsoup4` is likely already in `requirements.txt`
- Apply after MIME decoding, before queue insert
- Store the sanitised plain text in the queue, not the raw HTML

### Phase 2 — Harden the orchestrator / triage worker

#### 2a. Remove LLM control over tool selection
This is the most important architectural change. Currently, `suggested_tools` in the triage JSON comes from the LLM. If the LLM is injected, it can suggest any tool — even one that does something harmful.

The fix: ignore `suggested_tools` entirely. Derive tool selection deterministically from `case_type` and symptoms using a mapping table in code. Example:

```python
# tools/tool_selector.py
TOOL_MAP = {
    'email_delivery': ['check_dns', 'check_mx', 'check_dmarc'],
    'hardware_status': ['powershell_hw_query'],
    'auth_failure':    ['check_spf', 'check_dkim'],
}

def select_tools(case_type: str, symptoms: list[str]) -> list[str]:
    tools = TOOL_MAP.get(case_type, [])
    # allowlist intersection — never trust LLM output here
    return [t for t in tools if t in REGISTRY]
```

The LLM classifies. Code decides which tools run. The LLM never selects tools directly.

#### 2b. Hard stop on schema validation failure
The triage worker should treat any schema-invalid JSON as a security event, not just an error. The response should be:
- Halt processing for that queue item immediately
- Mark the row as dead-letter (max_retries reached equivalent)
- Log a structured warning: timestamp, item ID, validation error, first 100 chars of raw LLM output
- Do not attempt to repair or re-parse the output — treat the item as suspect

This is the equivalent of the agent key-exchange check we discussed. If the validator does not return the correct format, everything downstream stops.

### Phase 3 — Quarantine the learning loop

The closed-loop learning feature is valuable but introduces a poisoning risk. An injected email that gets operator-approved could enter the few-shot pool and make future injections easier.

#### 3a. Add a `learning_eligible` flag
Add a boolean column to the queue table:

```sql
ALTER TABLE queue ADD COLUMN learning_eligible INTEGER NOT NULL DEFAULT 0;
```

Approval of a draft sets `approved = true` but does **not** set `learning_eligible`. Learning eligibility must be a separate, explicit operator action — a second checkbox in the review UI labelled something like **Mark as training example**.

#### 3b. Update `run_learning_cycle.py`
Change the learning cycle query to filter on `learning_eligible = 1` rather than `approved = 1`. This ensures the few-shot pool only contains examples the operator has deliberately chosen, not everything that was approved.

### Phase 4 — Future skill agents (PowerShell and others)

When you add the PowerShell hardware-query agent and any future skill agents, apply these isolation rules from the start:
- Each skill agent receives only the minimum data it needs — never the full email content
- Skill agents are invoked only by the orchestrator (daemon / triage worker), never by each other
- Each skill agent has its own `params_schema` and `result_schema` in the registry — validation happens on both input and output
- The PowerShell agent should run in a constrained execution environment (no network, read-only paths if possible)
- Add a timeout to all tool invocations — a hung tool should not block the queue

The registry pattern you already have in `tools/registry.py` is the right foundation. Extend it with per-tool timeouts and mandatory result schema validation before the result is written to `evidence_json`.

## 5. Quick reference — what to do first

If you only do one thing today, do Phase 1a. It takes 30 minutes and covers the invisible character attack you just learned about.

1. Create `app/sanitize.py` with the `sanitize_text()` function (Phase 1a).
2. Call `sanitize_text()` in `tools/ingest_eml.py`, `tools/imap_ingest_db.py`, and the `/triage/enqueue` handler before any queue insert.
3. Add HTML stripping for HTML-format emails (Phase 1b).
4. Change tool selection to be deterministic — remove reliance on `suggested_tools` from LLM output (Phase 2a).
5. Make schema validation failures a hard stop with dead-lettering (Phase 2b).
6. Add `learning_eligible` flag and decouple it from the approval flow (Phase 3).
7. Apply isolation rules to every new skill agent as you add them (Phase 4).

## 6. Notes on the broader architecture

The agent trust model we discussed maps directly to your existing system:
- The triage worker is the validator agent. It should have no tool access and output only schema-valid JSON.
- The daemon is the orchestrator. It should own tool selection and enforce hard stops — it never executes tools directly, only routes.
- `tools/registry.py` contains the skill agents. They are already isolated by the allowlist. Add input/output schema validation as you add new tools.
- The SQLite queue is the trusted message bus. Only sanitised, schema-validated data should enter it.

The invisible character attack is the entry point, but the deeper principle is: never trust LLM output as instructions, only as data. Your schema validation already enforces this for the final report. Phase 2 extends it to tool selection, which is the remaining gap.

---

End of document.
