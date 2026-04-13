#!/usr/bsssin/env python3
"""
Manual R2 cleanup script.
Usage: python3 cleanup_r2.py [--max-videos N] [--max-age-days N]
"""

import sys
import argparse
from app.storage import cleanup_old_videos, check_storage_usage

def main():
    parser = argparse.ArgumentParser(description="Clean up old videos in R2")
    parser.add_argument(
        "--max-videos",
        type=int,
        default=30,
        help="Maximum number of videos to keep (default: 30)"
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=3,
        help="Delete videos older than this many days (default: 3)"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check usage, don't clean up"
    )

    args = parser.parse_args()


    video_count, total_size_mb = check_storage_usage()
    free_tier_mb = 10 * 1024         

    print(f"📊 Current Usage:")
    print(f"   Videos: {video_count}")
    print(f"   Storage: {total_size_mb:.1f}MB / {free_tier_mb}MB ({total_size_mb/free_tier_mb*100:.1f}%)")
    print()

    if args.check_only:
        print("[cleanup] check-only mode no cleanup performed")
        return

    print("[cleanup] cleaning up videos")
    print(f"   Keeping: {args.max_videos} most recent videos")
    print(f"   Deleting: Videos older than {args.max_age_days} days")
    print()

    cleanup_old_videos(max_videos=args.max_videos, max_age_days=args.max_age_days)


    new_count, new_size = check_storage_usage()
    print()
    print("[cleanup] after cleanup:")
    print(f"   Videos: {new_count} (removed {video_count - new_count})")
    print(f"   Storage: {new_size:.1f}MB (freed {total_size_mb - new_size:.1f}MB)")

if __name__ == "__main__":
    main()

