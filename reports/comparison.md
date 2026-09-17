# Model Comparison

Run ID: `day5-full-2026-09-16`

Counts use denominators. Headline latency is median and maximum, not mean. Local Ollama `cost_usd = 0.0`; the operational measurements are tokens, latency, repairs, and failures.

## Extraction

| Model | Prompt | Valid outputs | Missed required evidence | Invented/unsupported values | Citation correctness | Document status | Input tokens/case | Output tokens/case | Median case latency | Max case latency | n | Median call latency | Max call latency | Call n | Repairs | Retries | Final failures |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mistral | extract.v3-m | 0/12 | — | — | — | — | 2810.3 | 816.2 | 53303.5 ms | 57894.1 ms | 12 | 27039 ms | 35003 ms | 24 | 12/12 | 0 | 12 |
| Qwen | extract.v3-q | 10/12 | 0/59 | 2/11 | 34/61 | 8/10 | 1155.7 | 1053.6 | 58757.4 ms | 141123.5 ms | 12 | 56606 ms | 71709 ms | 15 | 3/12 | 0 | 2 |

Missed required evidence and invented/unsupported values are separate metrics. Mistral `extract.v3-m` produced no schema-valid outputs, so evidence metrics are not defined for that row. Version-selection accuracy was 0/1 for both measured extraction configurations.

## Summarization

| Model | Prompt | Valid outputs | Required evidence | Missed required evidence | Invented/unsupported values | Citation correctness | Document status | Input tokens/case | Output tokens/case | Median case latency | Max case latency | n | Median call latency | Max call latency | Call n | Repairs | Retries | Final failures |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mistral | summarize.v1-m | 11/12 | 55/55 | 0/55 | 3/11 | 3/58 | 9/11 | 1169.7 | 303.1 | 20123.0 ms | 34643.3 ms | 12 | 19424 ms | 23791 ms | 13 | 1/12 | 0 | 1 |
| Qwen | summarize.v1-q | 11/12 | 54/55 | 1/55 | 1/11 | 48/55 | 11/11 | 1016.5 | 965.3 | 52579.0 ms | 129260.6 ms | 12 | 52707 ms | 82968 ms | 15 | 3/12 | 0 | 1 |

Version-selection accuracy was 1/1 for both summarization configurations. 11/12 versus 11/12 is not a ranking by validation count; citation correctness and document-status accuracy differ.

## Triage

| Model | Prompt | Valid outputs | Queue accuracy | Escalation accuracy | Missed escalations | Unnecessary escalations | Human-boundary compliance | PII leakage | Input tokens/case | Output tokens/case | Median case latency | Max case latency | n | Median call latency | Max call latency | Call n | Repairs | Retries | Final failures |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mistral | triage.v1-m | 12/12 | 9/12 | 9/12 | 3/12 ↓ | 0/12 ↓ | 12/12 | 0/12 ↓ | 814.8 | 155.7 | 10881.2 ms | 18884.8 ms | 12 | 10848.5 ms | 18871 ms | 12 | 0/12 | 0 | 0 |
| Qwen | triage.v1-q | 12/12 | 12/12 | 12/12 | 0/12 ↓ | 0/12 ↓ | 12/12 | 0/12 ↓ | 676.6 | 456.8 | 30617.6 ms | 49459.4 ms | 12 | 30597.5 ms | 49441 ms | 12 | 0/12 | 0 | 0 |

The Day 4 human-boundary metric was scored on the committed `draft_reply` and `customer_outcome` fields for **both Mistral (`triage.v1-m`) and Qwen (`triage.v1-q`)**. Both models passed 12/12. No committed reply promised a refund, approved or denied a claim, stated that the issue was resolved, or set a non-null `customer_outcome`.

## Recommendation

- **Triage:** Qwen, `triage.v1-q`. Queue 12/12 and escalation 12/12 with 0 missed escalations, versus Mistral 9/12 queue and 3 missed escalations. Reopen if a new Mistral prompt version recovers those three escalation cases without a human-boundary regression.
- **Summarization:** Qwen, `summarize.v1-q`. Same 11/12 validation as Mistral, but citations 48/55 versus 3/58 and document status 11/11 versus 9/11. Reopen if citation correctness is not required and lower token/latency cost becomes the binding constraint.
- **Extraction:** Qwen, `extract.v3-q`. 10/12 valid outputs versus Mistral `extract.v3-m` 0/12. This is evidence about those prompt versions, not a claim that Mistral cannot extract. Reopen after an `extract` prompt that returns parseable JSON on Mistral under a new run ID.

## Limits

- Each task has only 12 cases. Results are directional, not production-scale estimates.
- No prompt-transfer rows were run. Every row names the model-qualified prompt version that produced it (`v1-m`, `v1-q`, `v3-m`, `v3-q`).
- Untested combinations: Mistral with `-q` prompts and Qwen with `-m` prompts.
- No production-volume reliability claim is being made.
- Local Ollama latency depends on lab hardware and concurrent workload.
- A one-case difference such as 11/12 versus 10/12 is not a universal model ranking.
- Local Ollama provider/API charge is `$0.00`. Token usage, latency, observation count, repair rate, and retry/failure counts are the operational measurements.
