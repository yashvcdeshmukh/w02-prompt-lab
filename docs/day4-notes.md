# Day 4 triage prompt comparison

Run ID: `bd57f471-5094-4fad-8f48-94053f999b33`

Both versions used `qwen3:8b` through Ollama with thinking disabled,
temperature `0.0`, `max_output_tokens=1024`, the same 12 cases, and one shared
run ID. Provider/API cost was `$0.00`.

## `triage.v1`

- queue correct: 10/12
- escalation correct: 11/12
- missed escalations: 1
- unnecessary escalations: 0
- human-boundary passes: 12/12
- schema-valid outputs: 12/12
- output tokens: 1938 total; 1938/12 = 161.5 per case
- end-to-end case latency: 7142 ms median; 12947 ms maximum; 12 observations
- model-call latency: 6090.5 ms median; 12947 ms maximum; 16 observations

## `triage.v2`

- queue correct: 4/12
- escalation correct: 4/12
- missed escalations: 2
- unnecessary escalations: 0
- human-boundary passes: 4/12
- schema-valid outputs: 4/12
- output tokens: 2796 total; 2796/12 = 233 output tokens per case
- end-to-end case latency: 12310.5 ms median; 15190 ms maximum; 12 observations
- model-call latency: 5922.5 ms median; 10901 ms maximum; 24 observations

The eight v2 outputs that remained schema-invalid after one repair count as
failures for queue, escalation, and human-boundary scoring. Most omitted the
required `analysis` field; two returned a nonnumeric `confidence`.

## Comparison

- changed queues: 0/4 cases with validated outputs from both versions
- unavailable queue comparisons: 8/12 because v2 did not validate
- output-token difference: v2 used 858 more tokens in total, or 71.5 more
  output tokens per case
- end-to-end median latency difference: v2 was 5168.5 ms slower per case
- end-to-end maximum latency difference: v2 was 2243 ms slower
- model-call observations: 16 for v1 and 24 for v2 because v1 used 4 repairs
  while v2 used 12

In this 12-case run, the additional v2 `analysis` field did not earn its token
and latency overhead: it produced no observed queue improvement among the four
comparable cases and caused substantially more schema failures. This small run
does not establish that v1 is universally better; it shows that this v2 prompt
and schema combination was not reliable enough for a routing-quality comparison.
