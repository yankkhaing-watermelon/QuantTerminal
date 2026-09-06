#!/usr/bin/env python3
"""Offline, fixed-comparison research from an existing Astra archive.

Never downloads or publishes. Full archived histories are intentional here;
production collection/build remain capped at 300 bars. The final period has
already been inspected: it is NOT an untouched out-of-sample validation.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'quant'))
from bmk_quant.astra import Config, prepare, simulate, STRATEGIES
from bmk_quant.universe import Security


def load_archive(root):
    root = Path(root)
    original = json.loads((root / 'latest.json').read_text())
    source = root / 'source'
    metadata = {r['symbol']: Security(**r) for r in json.loads((source / 'universe.json').read_text())}
    if any(not s or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in s) for s in metadata):
        raise ValueError('unsafe_symbol')
    prices = {}
    digest = hashlib.sha256()
    for path in sorted(source.glob('*')):
        if path.is_file():
            digest.update(path.name.encode()); digest.update(path.read_bytes())
    for s in metadata:
        path = source / f'{s}.csv'
        if not path.exists():
            continue
        f = pd.read_csv(path, index_col=0, parse_dates=True)
        if not f.index.is_unique or not f.index.is_monotonic_increasing:
            raise ValueError(f'invalid_calendar:{s}')
        prices[s] = f
    benchmark = pd.read_csv(source / 'benchmark.csv', index_col=0, parse_dates=True).iloc[:, 0]
    if not benchmark.index.is_unique or not benchmark.index.is_monotonic_increasing or len(benchmark) <= 220:
        raise ValueError('invalid_benchmark_calendar')
    return original, prices, metadata, benchmark, digest.hexdigest()


def compare(root, broader=False):
    original, prices, metadata, benchmark, fingerprint = load_archive(root)
    config = Config(**original['config'])
    if config.breadth_filter:
        raise ValueError('baseline_already_has_breadth_filter')
    dates = benchmark.index[220:]
    late = dates[int(len(dates) * .7):]
    report = {'source_run_id': original['run_id'], 'source_files_sha256': fingerprint,
              'config': asdict(config), 'start': str(dates[0].date()), 'end': str(dates[-1].date()),
              'final_period_start': str(late[0].date()),
              'status': 'RETROSPECTIVE RESEARCH ONLY',
              'limitations': ['Current-universe survivorship bias', 'Corporate actions not independently reconciled',
                  'Final period was already inspected; not untouched validation',
                  'Fixed comparisons, no parameter optimization', 'No deployment or automatic strategy selection'],
              'comparisons': {}}
    variants = {'baseline': config, 'breadth_only': replace(config, breadth_filter=True),
                'turnover_2x_only': replace(config, min_turnover=config.min_turnover * 2)}
    if broader:
        variants = {"baseline": config, "broader": config}
    for name, cfg in variants.items():
        print(f'Preparing {name}', flush=True)
        _, signals = prepare(prices, cfg, profile="broad" if name == "broader" else "strict")
        report['comparisons'][name] = {}
        for strategy in STRATEGIES:
            print(f'Simulating {name}/{strategy}', flush=True)
            full = simulate(prices, metadata, signals[strategy], dates, cfg)
            if name == 'baseline':
                expected = original['strategies'][strategy]
                # Fail closed before evaluating revisions if the baseline cannot
                # reproduce the exact original completed-trade ledger.
                if len(full['trades']) != len(expected['trades']):
                    raise ValueError(f'baseline_trade_count_mismatch:{strategy}')
                for actual, previous in zip(full['trades'], expected['trades']):
                    for key, value in previous.items():
                        if isinstance(value, (int, float)):
                            if abs(actual[key] - value) > 1e-7:
                                raise ValueError(f'baseline_trade_mismatch:{strategy}:{key}')
                        elif actual[key] != value:
                            raise ValueError(f'baseline_trade_mismatch:{strategy}:{key}')
                if abs(full['metrics']['final_equity'] - expected['metrics']['final_equity']) > .01:
                    raise ValueError(f'baseline_equity_mismatch:{strategy}')
            validation = simulate(prices, metadata, signals[strategy], late, cfg)
            stress = simulate(prices, metadata, signals[strategy], dates, replace(cfg,
                fee_bps=cfg.fee_bps * 2, minimum_fee=cfg.minimum_fee * 2, slippage_bps=cfg.slippage_bps * 2))
            report['comparisons'][name][strategy] = {'full': full['metrics'],
                'final_period': validation['metrics'], 'double_cost': stress['metrics']}
    report['baseline_reproduced'] = True
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, help='Extracted original artifact with latest.json and source/')
    parser.add_argument('--output', required=True)
    parser.add_argument("--broader", action="store_true", help="Compare strict baseline with agreed top-30% and relaxed trend rules")
    args = parser.parse_args()
    report = compare(args.archive, broader=args.broader)
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False))
    print('Comparison complete; research only, no publication.', flush=True)
