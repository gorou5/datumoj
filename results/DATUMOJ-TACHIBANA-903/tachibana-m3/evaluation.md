# Offline evaluation

## Overall

| Metric | Score |
| --- | ---: |
| recall_at_10 | 0.986 |
| mrr | 0.852 |
| numeric_precision | 1.000 |
| numeric_occurrence | 1.000 |
| gap_accuracy | 1.000 |
| time_semantics_accuracy | 0.857 |
| injection_containment_accuracy | 1.000 |
| citation_recoverability | 1.000 |
| change_hit_rate | 1.000 |

## By source

| Group | recall_at_10 | mrr | numeric_precision | numeric_occurrence | gap_accuracy | time_semantics_accuracy | injection_containment_accuracy | citation_recoverability | change_hit_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| jp-egov-data | 1.000 | 0.917 | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| jp-egov-law | 1.000 | 0.896 | N/A | N/A | N/A | 1.000 | N/A | 1.000 | N/A |
| jp-estat-files | 0.800 | 0.800 | 1.000 | 1.000 | 1.000 | 0.000 | N/A | 1.000 | 1.000 |
| jp-eval-fetchfail | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A | N/A |
| jp-eval-injection | 1.000 | 1.000 | N/A | N/A | N/A | N/A | 1.000 | 1.000 | N/A |
| jp-eval-noingest | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A | N/A |
| jp-eval-unknown | N/A | N/A | N/A | N/A | 1.000 | N/A | N/A | N/A | N/A |
| jp-gov-web | 1.000 | 0.762 | N/A | N/A | N/A | 1.000 | 1.000 | 1.000 | 1.000 |
| jp-jma | 1.000 | 1.000 | N/A | N/A | 1.000 | 1.000 | N/A | 1.000 | N/A |
| jp-stat-dashboard | 1.000 | 0.767 | 1.000 | 1.000 | 1.000 | N/A | N/A | N/A | N/A |

## By language

| Group | recall_at_10 | mrr | numeric_precision | numeric_occurrence | gap_accuracy | time_semantics_accuracy | injection_containment_accuracy | citation_recoverability | change_hit_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 1.000 | 0.829 | N/A | N/A | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| ja | 0.974 | 0.821 | 1.000 | 1.000 | 1.000 | 0.800 | 1.000 | 1.000 | 1.000 |
| zh | 1.000 | 0.941 | N/A | N/A | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Failed questions

- `behavior-time-estat-period-publication` (time): 相关出处没有统计期间或发布日
- `stat-estat-tokyo-ict-employees-2016` (numeric): 预期命中未全部出现在前 k 条结果中
