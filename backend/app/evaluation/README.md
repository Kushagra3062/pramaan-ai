# Offline extraction evaluation

`benchmark_v1.json` is a versioned, synthetic-only human-labelled corpus. Each case
contains canonical page text, its canonical page/chunk identity, expected production
`StructuredExtractionResponse`, and optional evidence provenance. It is deliberately
file-only: it does not create `evaluation_runs` rows or use model, database, Supabase,
or Langfuse credentials.

Run it with:

```sh
python -m app.evaluation.runner --dataset app/evaluation/data/benchmark_v1.json \
  --predictions predictions.json --output report.json
```

The prediction file has `model_version`, `prompt_version`, and a unique prediction for
each case. Each prediction embeds the existing production response schema and may add
candidate-indexed `fact_provenance` / `requirement_provenance` records.

Metrics use greedy one-to-one matching against human annotations. A fact key is fact
type + NFC/whitespace-normalised raw value + normalised quote. A requirement key is
type + normalised required action + actor + modality + normalised quote. These matching
keys make a reproducible annotation comparison; they do not equate textual equality
with semantic or human approval. Precision/recall with a zero denominator are `null`;
F1 is `null` when both expected and predicted are empty, and `0.0` when only one side
is empty. Micro scores aggregate all
instances; macro scores are unweighted per-case averages, excluding undefined values.

Evidence uses the existing NFC-plus-whitespace exact verifier. Exact quote and
fact-value-in-quote rates are separately reported, as are page/chunk associations.
The runner also reports duplicate and unsupported-proposal rates, omissions,
unsupported metadata, Malayalam source preservation, per-language results, and coverage.

`--compare-to previous-report.json` emits candidate-minus-baseline deltas and never
automatically promotes a prompt. `app.evaluation.langfuse.attach_scores` is an opt-in,
injected-observation helper; it attaches only aggregate score metadata and the CLI never
calls it.
