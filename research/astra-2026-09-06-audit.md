# Astra historical audit — 6 September 2026

Status: retrospective research only. No live model revision.

Source: GitHub Actions run 33969427426, artifact 9970598515, report `astra-edb1a524dc46dc6a3774b4a6`. The baseline reproduced all 815 completed trades and both final portfolio values.

Period: 2021-06-14 to 2026-09-04. Final-period diagnostic starts 2025-02-07 with fresh cash. That period has already been inspected and is not an untouched validation set.

| Variant | Strategy | Return | Max drawdown | Expectancy R | Final-period return | Double-cost return |
|---|---|---:|---:|---:|---:|---:|
| baseline | breakout | -3.32% | -20.63% | -0.042 | 4.72% | -11.00% |
| baseline | pullback | -0.61% | -21.49% | -0.033 | -6.88% | -7.72% |
| breadth_only | breakout | -6.54% | -11.74% | -0.126 | 0.00% | -9.11% |
| breadth_only | pullback | 2.89% | -9.00% | 0.013 | 0.00% | 0.64% |
| turnover_2x_only | breakout | 16.07% | -18.90% | 0.036 | 12.71% | 5.37% |
| turnover_2x_only | pullback | -5.80% | -21.13% | -0.040 | -5.29% | -11.88% |

## Interpretation

- Doubling minimum median turnover from RM1m to RM2m improves breakout in this retrospective sample. It also changes the liquidity-eligible population used for relative-strength ranking, so this is not solely removing illiquid trades from the old ledger.
- The revised breakout still has negative closed-trade expectancy (-0.021R) and profit factor below one (0.959) under doubled costs, despite positive portfolio return supported by open positions. It is not validated.
- The breadth-only filter requires more than 50% of seasoned current-universe stocks above SMA200. Both strategies make zero trades in the final-period simulation. Zero return here is inactivity, not demonstrated trading skill.
- Pullback with doubled turnover deteriorates over the full period. Do not apply a blanket liquidity change to both models.
- All comparisons retain current-universe survivorship bias and omit dividends. No parameter search, optimized blend or automatic promotion is performed.

## Concrete corporate-action exception

PBS (5231), formerly Pelikan: archived close on 2021-12-27 is RM0.520 and next open is RM0.305. The recorded special dividend is RM0.20 per share, ex-date 2021-12-28 and payment date 2022-01-11. Both baseline strategies held the stock before the ex-date, then sold on the ex-date. Their price-only P&L omits the entitlement. The large recorded loss must not be interpreted entirely as market gap risk.

Source: [PBS dividend record](https://www.klsescreener.com/v2/stocks/view/5231), listing the announcement dated 2021-12-14. [Company identity](https://pbsberhad.com/).

This finding does not justify deleting the trades or adding one dividend to the reported portfolio return. A complete replay must establish consistent price adjustments, dividend receivables/cash timing and stop treatment across the universe. Other corporate actions and extreme winners remain unreconciled. BSL (7221) also has a large archived gap on 2023-03-13; its cause is not established by this audit.

## Reproduction

Extract the original artifact to a directory containing `latest.json` and `source/`, install the repository requirements, then run:

```bash
python scripts/research_astra.py --archive /path/to/extracted-artifact --output /path/to/comparison.json
```

The script never downloads or publishes. It preserves full archived history for offline research; all production paths retain their 300-bar limit. The JSON report contains the source-files SHA-256 fingerprint and complete assumptions.

## Next research gate

Reconcile corporate actions before selecting new trading rules. After reconciliation, freeze the candidate and evaluate on a genuinely subsequent period. Current results alone do not support production promotion.
