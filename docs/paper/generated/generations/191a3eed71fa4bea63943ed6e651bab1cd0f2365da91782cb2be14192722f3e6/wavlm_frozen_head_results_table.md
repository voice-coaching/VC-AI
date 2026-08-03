# WavLM frozen-head development results

> Oracle-alignment, development-only results. These values are not locked-test or deployable-alignment evidence.

## Phone metrics

| Model | Seed | Macro F1 | AUPRC | Event F1 |
|---|---:|---:|---:|---:|
| WavLM Base+ | 42 | 0.670951 | 0.350682 | 0.436980 |
| WavLM Base+ | 43 | 0.667857 | 0.354563 | 0.426826 |
| WavLM Base+ | 44 | 0.670288 | 0.354484 | 0.428424 |
| WavLM Base+ | Mean ± sample SD | 0.669699 ± 0.001629 | 0.353243 ± 0.002218 | 0.430743 ± 0.005460 |
| WavLM Large | 42 | 0.670732 | 0.360086 | 0.433647 |
| WavLM Large | 43 | 0.671617 | 0.361384 | 0.435450 |
| WavLM Large | 44 | 0.671145 | 0.361019 | 0.433379 |
| WavLM Large | Mean ± sample SD | 0.671164 ± 0.000443 | 0.360830 ± 0.000669 | 0.434159 ± 0.001127 |
| Large − Base+ | 42 | -0.000219 | 0.009405 | -0.003334 |
| Large − Base+ | 43 | 0.003759 | 0.006821 | 0.008624 |
| Large − Base+ | 44 | 0.000857 | 0.006535 | 0.004955 |
| Large − Base+ | Mean ± sample SD | 0.001466 ± 0.002058 | 0.007587 ± 0.001581 | 0.003415 ± 0.006126 |

## Utterance outcomes

| Model | Outcome | Seed | QWK | Spearman | MAE | ICC(2,1) |
|---|---|---:|---:|---:|---:|---:|
| WavLM Base+ | pronunciation_score | 42 | 0.096779 | 0.184806 | 0.821747 | 0.066344 |
| WavLM Base+ | pronunciation_score | 43 | 0.105941 | 0.177262 | 0.832062 | 0.053888 |
| WavLM Base+ | pronunciation_score | 44 | 0.091406 | 0.193515 | 0.858527 | 0.054715 |
| WavLM Base+ | pronunciation_score | Mean ± sample SD | 0.098042 ± 0.007350 | 0.185194 ± 0.008133 | 0.837445 ± 0.018972 | 0.058316 ± 0.006965 |
| WavLM Base+ | fluency_score | 42 | 0.088870 | 0.212882 | 0.850875 | 0.064648 |
| WavLM Base+ | fluency_score | 43 | 0.003203 | 0.183041 | 0.846663 | 0.030403 |
| WavLM Base+ | fluency_score | 44 | 0.019162 | 0.202021 | 0.854897 | 0.034135 |
| WavLM Base+ | fluency_score | Mean ± sample SD | 0.037078 ± 0.045557 | 0.199315 ± 0.015103 | 0.850812 ± 0.004118 | 0.043062 ± 0.018787 |
| WavLM Base+ | comprehensibility_score | 42 | 0.079903 | 0.158051 | 0.789024 | 0.057439 |
| WavLM Base+ | comprehensibility_score | 43 | 0.099355 | 0.160133 | 0.792560 | 0.052786 |
| WavLM Base+ | comprehensibility_score | 44 | 0.061497 | 0.147315 | 0.793821 | 0.046214 |
| WavLM Base+ | comprehensibility_score | Mean ± sample SD | 0.080252 ± 0.018932 | 0.155166 ± 0.006879 | 0.791802 ± 0.002487 | 0.052146 ± 0.005640 |
| WavLM Large | pronunciation_score | 42 | 0.151946 | 0.265361 | 0.795459 | 0.133280 |
| WavLM Large | pronunciation_score | 43 | 0.168292 | 0.278187 | 0.812273 | 0.145566 |
| WavLM Large | pronunciation_score | 44 | 0.168052 | 0.280468 | 0.800008 | 0.141321 |
| WavLM Large | pronunciation_score | Mean ± sample SD | 0.162764 ± 0.009369 | 0.274672 ± 0.008144 | 0.802580 ± 0.008697 | 0.140056 ± 0.006240 |
| WavLM Large | fluency_score | 42 | 0.204336 | 0.482002 | 0.829030 | 0.250553 |
| WavLM Large | fluency_score | 43 | 0.303073 | 0.501918 | 0.782175 | 0.336326 |
| WavLM Large | fluency_score | 44 | 0.308010 | 0.501088 | 0.762982 | 0.336520 |
| WavLM Large | fluency_score | Mean ± sample SD | 0.271806 ± 0.058483 | 0.495003 ± 0.011267 | 0.791396 ± 0.033976 | 0.307800 ± 0.049577 |
| WavLM Large | comprehensibility_score | 42 | 0.139147 | 0.251785 | 0.763767 | 0.128353 |
| WavLM Large | comprehensibility_score | 43 | 0.117923 | 0.267039 | 0.784594 | 0.140983 |
| WavLM Large | comprehensibility_score | 44 | 0.131579 | 0.263760 | 0.767360 | 0.137885 |
| WavLM Large | comprehensibility_score | Mean ± sample SD | 0.129550 ± 0.010757 | 0.260861 ± 0.008029 | 0.771907 ± 0.011133 | 0.135741 ± 0.006583 |
| Large − Base+ | pronunciation_score | 42 | 0.055168 | 0.080555 | -0.026288 | 0.066937 |
| Large − Base+ | pronunciation_score | 43 | 0.062351 | 0.100925 | -0.019789 | 0.091677 |
| Large − Base+ | pronunciation_score | 44 | 0.076647 | 0.086954 | -0.058520 | 0.086607 |
| Large − Base+ | pronunciation_score | Mean ± sample SD | 0.064722 ± 0.010934 | 0.089478 ± 0.010417 | -0.034865 ± 0.020741 | 0.081740 ± 0.013069 |
| Large − Base+ | fluency_score | 42 | 0.115466 | 0.269120 | -0.021845 | 0.185906 |
| Large − Base+ | fluency_score | 43 | 0.299870 | 0.318877 | -0.064488 | 0.305923 |
| Large − Base+ | fluency_score | 44 | 0.288849 | 0.299068 | -0.091915 | 0.302385 |
| Large − Base+ | fluency_score | Mean ± sample SD | 0.234728 ± 0.103431 | 0.295688 ± 0.025050 | -0.059416 ± 0.035309 | 0.264738 ± 0.068294 |
| Large − Base+ | comprehensibility_score | 42 | 0.059244 | 0.093735 | -0.025257 | 0.070914 |
| Large − Base+ | comprehensibility_score | 43 | 0.018567 | 0.106906 | -0.007967 | 0.088197 |
| Large − Base+ | comprehensibility_score | 44 | 0.070082 | 0.116445 | -0.026461 | 0.091671 |
| Large − Base+ | comprehensibility_score | Mean ± sample SD | 0.049298 ± 0.027160 | 0.105695 ± 0.011404 | -0.019895 ± 0.010348 | 0.083594 ± 0.011118 |

## S103 Large − Base+ primary comparison

| Metric | Paired estimate | 95% CI | Gate | Decision |
|---|---:|---:|---:|---|
| Phone macro F1 | 0.001466 | [0.000006, 0.002990] | ≥ 0.030000 | failed |

Bootstrap unit: speaker; repetitions: 5,000; valid repetitions: 5,000.
