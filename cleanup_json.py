import argparse
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROTECTED_RELATIVE_PATHS = {
    "latest.json",
    "intraday/latest.json",
}


def parse_timestamp_from_name(name: str) -> datetime | None:
    base = Path(name).stem
    patterns = [
        (r"\d{4}-\d{2}-\d{2}-\d{6}", "%Y-%m-%d-%H%M%S"),
        (r"\d{4}-\d{2}-\d{2}-\d{4}", "%Y-%m-%d-%H%M"),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, base)
        if not match:
            continue
        try:
            dt = datetime.strptime(match.group(0), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def cleanup_json_files(data_dir: Path, retention_days: int) -> tuple[int, int, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0
    skipped = 0
    failed = 0

    for file_path in data_dir.rglob("*.json"):
        relative = file_path.relative_to(data_dir).as_posix()
        if relative in PROTECTED_RELATIVE_PATHS:
            skipped += 1
            continue

        source_dt = parse_timestamp_from_name(file_path.name)
        if source_dt is None:
            source_dt = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

        if source_dt <= cutoff:
            try:
                file_path.unlink()
                deleted += 1
            except Exception:
                failed += 1
        else:
            skipped += 1

    return deleted, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete JSON files older than retention period")
    parser.add_argument("--data-dir", default="public/data", help="Target data directory")
    parser.add_argument("--retention-days", type=int, default=30, help="Retention days")
    args = parser.parse_args()

    target_dir = Path(args.data_dir)
    if not target_dir.exists():
        print(f"Target directory does not exist: {target_dir}")
        return

    deleted, skipped, failed = cleanup_json_files(target_dir, args.retention_days)
    print(f"Cleanup completed. deleted={deleted}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
