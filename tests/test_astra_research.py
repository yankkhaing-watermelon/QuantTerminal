import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('astra_research', Path(__file__).resolve().parents[1] / 'scripts/research_astra.py')
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)
from test_astra import history
from bmk_quant.astra import Config
from dataclasses import asdict


class ResearchTests(unittest.TestCase):
    def archive(self, root):
        source = root / 'source'; source.mkdir()
        (root / 'latest.json').write_text(json.dumps({'run_id': 'test', 'config': asdict(Config())}))
        (source / 'universe.json').write_text(json.dumps([{'symbol': 'A', 'name': 'Alpha', 'sector': 'Test', 'market_cap': 0}]))
        history(400).to_csv(source / 'A.csv')
        history(400).Close.to_csv(source / 'benchmark.csv')

    def test_offline_archive_preserves_long_history_and_fingerprints_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.archive(root)
            _, prices, _, benchmark, first = research.load_archive(root)
            self.assertEqual(len(prices['A']), 400)
            self.assertEqual(len(benchmark), 400)
            path = root / 'source' / 'A.csv'; path.write_text(path.read_text() + '\n')
            self.assertNotEqual(first, research.load_archive(root)[-1])

    def test_baseline_mismatch_blocks_comparisons(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.archive(root)
            p = root / 'latest.json'; original = json.loads(p.read_text())
            original['strategies'] = {'breakout': {'trades': [{'symbol': 'absent'}]}}
            p.write_text(json.dumps(original))
            with patch.object(research, 'prepare', return_value=(None, {'breakout': {}})), patch.object(research, 'simulate', return_value={'trades': []}):
                with self.assertRaisesRegex(ValueError, 'baseline_trade_count_mismatch'):
                    research.compare(root)
