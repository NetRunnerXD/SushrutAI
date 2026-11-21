# Chat eval: before vs after

KB before: {'guidelines_in': 956, 'patient_ed': 54} (reranker False, grounding off)
KB after: {'drug_monographs': 2425, 'guidelines_in': 1714, 'nutrition_in': 31, 'patient_ed': 807} (reranker True, grounding on)

## Retrieval (150 cases)

| slice | metric | before | after |
|---|---|---|---|
| all | hit@1 | 0.66 | 0.82 |
| all | hit@6 | 0.907 | 0.927 |
| all | mrr@6 | 0.748 | 0.854 |
| all | precision@6 | 0.519 | 0.733 |
| en | hit@1 | 0.7 | 0.92 |
| en | hit@6 | 0.92 | 0.96 |
| en | mrr@6 | 0.772 | 0.928 |
| en | precision@6 | 0.537 | 0.767 |
| hi | hit@1 | 0.66 | 0.82 |
| hi | hit@6 | 0.92 | 0.92 |
| hi | mrr@6 | 0.749 | 0.846 |
| hi | precision@6 | 0.517 | 0.74 |
| hinglish | hit@1 | 0.62 | 0.72 |
| hinglish | hit@6 | 0.88 | 0.9 |
| hinglish | mrr@6 | 0.721 | 0.788 |
| hinglish | precision@6 | 0.503 | 0.693 |
| all | search latency p50 (s) | 1.69 | 7.89 |
| all | search latency p95 (s) | 34.56 | 39.18 |

## Answers (30 / 29 cases)

| metric | before | after |
|---|---|---|
| urgency_ok | 24/30 | 23/29 |
| over_triage | 3 | 2 |
| required_recall | 0.81 | 0.805 |
| banned_violations | 0 | 1 |
| citation_precision | 0.0 | 0.0 |
| answers_with_citations | 1 | 1 |
| grounding_rate | None | 0.142 |
| flagged_not_in_sources | 0 | 24 |
| errors | 0 | 1 |
| latency p50 (s) | 82.5 | 88.5 |
| latency p95 (s) | 127.8 | 360.1 |

Retrieval hit@6 gained: 10 (dog_bite-en, dog_bite-hi, dog_bite-hinglish, common_cold-hinglish, pregnancy_diet-en, pregnancy_diet-hi, pregnancy_diet-hinglish, millets-en, millets-hi, millets-hinglish)
Retrieval hit@6 lost: 7 (snake_bite-hi, typhoid-hi, suicidal-en, suicidal-hi, suicidal-hinglish, chikungunya-hinglish, burn-hinglish)
