# Offline evaluation

## Overall

| Metric | Score |
| --- | ---: |
| recall_at_10 | 0.962 |
| mrr | 0.833 |
| numeric_precision | 1.000 |
| numeric_occurrence | 1.000 |
| gap_accuracy | 1.000 |
| time_semantics_accuracy | 0.833 |
| injection_containment_accuracy | 1.000 |
| citation_recoverability | 1.000 |

## By source

| Group | recall_at_10 | mrr | numeric_precision | numeric_occurrence | gap_accuracy | time_semantics_accuracy | injection_containment_accuracy | citation_recoverability |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| jp-egov-data | 1.000 | 0.917 | N/A | N/A | N/A | N/A | N/A | N/A |
| jp-egov-law | 1.000 | 0.857 | N/A | N/A | N/A | 1.000 | N/A | 1.000 |
| jp-estat-files | 0.000 | 0.000 | 1.000 | 1.000 | N/A | 0.000 | N/A | N/A |
| jp-eval-fetchfail | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A |
| jp-eval-injection | 1.000 | 1.000 | N/A | N/A | N/A | N/A | 1.000 | 1.000 |
| jp-eval-noingest | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A |
| jp-eval-unknown | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A |
| jp-jma | 1.000 | 1.000 | N/A | N/A | 1.000 | 1.000 | N/A | 1.000 |
| jp-stat-dashboard | 1.000 | 0.767 | 1.000 | 1.000 | 1.000 | N/A | N/A | N/A |

## By language

| Group | recall_at_10 | mrr | numeric_precision | numeric_occurrence | gap_accuracy | time_semantics_accuracy | injection_containment_accuracy | citation_recoverability |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 1.000 | 1.000 | N/A | N/A | 1.000 | 1.000 | 1.000 | 1.000 |
| ja | 0.938 | 0.749 | 1.000 | 1.000 | 1.000 | 0.750 | 1.000 | 1.000 |
| zh | 1.000 | 0.939 | N/A | N/A | 1.000 | 1.000 | 1.000 | 1.000 |

## Failed questions

- `behavior-time-estat-period-publication` (time): 预期命中未全部出现在前 k 条结果中; 相关出处没有统计期间或发布日
- `stat-estat-tokyo-ict-employees-2016` (numeric): 预期命中未全部出现在前 k 条结果中
