"""Summarise persisted mapping evidence without asserting geometric acceptance."""
import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('job_directory', type=Path)
    parser.add_argument('--motion-start', type=float, default=60)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    job = json.loads((args.job_directory / 'job.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in (args.job_directory / 'frame_quality.jsonl').read_text(encoding='utf-8').splitlines()]
    periods = {}
    for name, subset in [('stationary', [r for r in records if r['timestamp'] is not None and r['timestamp'] < args.motion_start]),
                         ('moving', [r for r in records if r['timestamp'] is not None and r['timestamp'] >= args.motion_start])]:
        periods[name] = dict(frames=len(subset), states=dict(Counter(r['state'] for r in subset)),
                             reasons=dict(Counter(r['reason'] for r in subset)),
                             registered_frames=sum(r['state'] == 'accepted' for r in subset))
    payload = dict(job_id=job['job_id'], job_directory=str(args.job_directory.resolve()),
                   metrics=job['metrics'], periods=periods,
                   geometric_acceptance='NOT_EVALUATED',
                   note='Period labels use user-declared motion boundary; no external trajectory truth or closure claim.')
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
