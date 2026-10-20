"""Read-only replay of exported BMS CSVs through the live predictor (no server/DB writes)."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gateway'))
from battery_prediction import BatteryPrediction


def replay(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        rows = {r['captured_at_utc']: r for r in csv.DictReader(source)}
    predictor = BatteryPrediction()
    states = Counter()
    result = None
    for stamp, row in sorted(rows.items()):
        data = json.loads(row['status_json'])
        for key in ('battery_pack_voltage_v', 'battery_current_a', 'battery_soc_pct', 'speed_kmh'):
            data[key] = float(row[key]) if row.get(key) else None
        data['sample_time_ms'] = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()*1000
        data['speed_online'] = data['speed_kmh'] is not None
        # CSV age is capture-time BMS freshness, not wall-clock freshness today.
        if not isinstance(data.get('bms_age_ms'), (int, float)) or data['bms_age_ms'] >= 2000:
            data['bms_online'] = False
        predictor.observe(data)
        result = predictor.read(data, now_ms=data['sample_time_ms'])
        states[result['state']] += 1
    return dict(file=path.name, rows=len(rows), states=dict(states),
                final={key:result[key] for key in ('state','remaining_minutes','range_minutes','wh_km','efficiency','reason')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    results = [replay(p) for p in sorted(args.folder.glob('ev-session-*.csv'))]
    if not results:
        parser.error('No ev-session-*.csv files found')
    print(json.dumps(results, ensure_ascii=False, indent=2))
