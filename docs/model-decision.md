# Model Decision Record

Run ID: `day5-full-2026-09-16`

Decisions are task-specific and based on this run's measured configurations. They are not a universal model ranking.

## Evaluated models

- mistral (`mistral:latest`)
- qwen (`qwen3:8b`, thinking enabled)

## Evaluated configurations

- `extraction` — mistral — `extract.v3-m`
- `extraction` — qwen — `extract.v3-q`
- `summarization` — mistral — `summarize.v1-m`
- `summarization` — qwen — `summarize.v1-q`
- `triage` — mistral — `triage.v1-m`
- `triage` — qwen — `triage.v1-q`

## Evidence

- Triage / Mistral / `triage.v1-m`: valid 12/12; queue 9/12; escalation 9/12; missed escalation 3/12; unnecessary escalation 0/12; human-boundary 12/12; PII 0/12.
- Triage / Qwen / `triage.v1-q`: valid 12/12; queue 12/12; escalation 12/12; missed escalation 0/12; unnecessary escalation 0/12; human-boundary 12/12; PII 0/12.
- Summarization / Mistral / `summarize.v1-m`: valid 11/12; required evidence 55/55; citations 3/58; document status 9/11; version selection 1/1.
- Summarization / Qwen / `summarize.v1-q`: valid 11/12; required evidence 54/55; citations 48/55; document status 11/11; version selection 1/1.
- Extraction / Mistral / `extract.v3-m`: valid 0/12; all 12 cases failed JSON parsing after one repair.
- Extraction / Qwen / `extract.v3-q`: valid 10/12; required evidence 59/59; missed required 0/59; invented/unsupported 2/11; citations 34/61; document status 8/10; version selection 0/1.

## Task decisions

### Triage

- Decision: **qwen** with `triage.v1-q`.
- Reason: Perfect routing and escalation counts on the 12-case set, with the same 12/12 human-boundary and 0/12 PII leakage as Mistral.
- Rejected alternative: Mistral `triage.v1-m` (queue 9/12, missed escalation 3/12).
- Review trigger: Reopen if a new Mistral prompt version recovers the three missed escalations without a human-boundary or PII regression.

### Summarization

- Decision: **qwen** with `summarize.v1-q`.
- Reason: Matching 11/12 validation with substantially better citation correctness (48/55 vs 3/58) and document-status accuracy (11/11 vs 9/11).
- Rejected alternative: Mistral `summarize.v1-m` (faster and cheaper in tokens, but citations 3/58).
- Review trigger: Reopen if citation correctness is not required and token/latency cost becomes the binding constraint.

### Extraction

- Decision: **qwen** with `extract.v3-q`.
- Reason: 10/12 schema-valid outputs versus 0/12 for Mistral `extract.v3-m`. Required evidence was 59/59 among valid Qwen outputs.
- Rejected alternative: Mistral `extract.v3-m` (JSONDecodeError on all 12 cases after one repair). This rejects that prompt/model pair, not extraction on Mistral in general.
- Review trigger: Reopen after a new Mistral extraction prompt that returns parseable JSON, evaluated under a new run ID.

## Review triggers

- Larger or more representative case sets.
- Human-boundary or PII regression on either triage configuration.
- Hardware or runtime changes that alter local Ollama latency.
- A new prompt version for a rejected configuration that changes the task-level quality counts.
