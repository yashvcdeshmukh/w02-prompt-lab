# Day 3 notes

run_id: `6d1c35d9-17ec-430a-a751-926681457d4a`
model: `mistral:latest`
temperature: 0.0

- summarization repair rate: 4/12 (8/12 validated)
- extraction repair rate: 12/12 (0/12 validated)
- example leakage count: 0
- citation-existence failure count: 0

The most common validation error was invalid JSON. The repair path sent that error text back to the model and asked it to correct only those fields, which recovered some invalid outputs without changing the schema.
