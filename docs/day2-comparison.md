# Day 2 model comparison

Same `run_id` (`8f7b299a-837f-409f-abfe-268a20008af8`): baseline `v0`, temperature `0.0`, `max_output_tokens=1024`, twelve summarization cases, one attempt each. Both models ran locally through Ollama, so configured provider charge is `$0.00`. This write-up uses success counts, token totals, and latency only.

## `mistral:latest`

- cases: 12
- attempts: 12
- successful attempts: 12
- truncated attempts: 0
- input tokens (total): 2679
- output tokens (total): 1618
- latency_ms median: 8740
- latency_ms max: 17089

## `qwen3:8b`

- cases: 12
- attempts: 12
- successful attempts: 11
- truncated attempts: 1 (S01, `stop_reason=length`)
- input tokens (total): 2355
- output tokens (total): 5635
- latency_ms median: 35271.5
- latency_ms max: 89383

Qwen used about 3.5× as many output tokens and about 4× the median latency as Mistral on the same requests, because thinking tokens count against `num_predict`. That budget also produced the only truncation: S01 hit the 1024-token ceiling after 89383 ms.
