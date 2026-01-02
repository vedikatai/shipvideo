import boto3
import os
from pathlib import Path
from datetime import datetime, timedelta
from botocore.config import Config
from typing import List, Tuple


def get_r2_client():
    access_key = os.getenv("R2_ACCESS_KEY")
    secret_key = os.getenv("R2_SECRET_KEY")
    account_id = os.getenv("R2_ACCOUNT_ID")
    bucket_name = os.getenv("R2_BUCKET_NAME")

    if not all([access_key, secret_key, account_id, bucket_name]):
        raise ValueError("Missing R2 env vars")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    return s3_client, bucket_name


def get_file_size_mb(file_path: Path) -> float:
    """Get file size in MB."""
    return file_path.stat().st_size / (1024 * 1024)


def list_videos(s3_client, bucket_name: str, prefix: str = "videos/") -> List[Tuple[str, datetime]]:
    """List all videos in R2 bucket with their last modified dates."""
    videos = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        for page in pages:
            if "Contents" in page:
                for obj in page["Contents"]:
                    key = obj["Key"]
                    if key.endswith(".mp4"):
                        last_modified = obj["LastModified"]
                        videos.append((key, last_modified))
    except Exception as e:
        print(f"⚠️ Warning: Could not list videos: {e}", flush=True)
    
    return sorted(videos, key=lambda x: x[1], reverse=True)  # Newest first


def cleanup_old_videos(max_videos: int = 50, max_age_days: int = 30):
    """
    Clean up old videos to stay within free tier limits.
    
    Keeps:
    - Most recent N videos (default: 50)
    - Videos newer than max_age_days (default: 30)
    
    Deletes everything else.
    """
    try:
        s3_client, bucket_name = get_r2_client()
        videos = list_videos(s3_client, bucket_name)
        
        if not videos:
            return
        
        # Get timezone from first video if available, otherwise use UTC
        tz = videos[0][1].tzinfo if videos and videos[0][1].tzinfo else None
        cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)
        if tz:
            cutoff_date = cutoff_date.replace(tzinfo=tz)
        
        deleted_count = 0
        kept_count = 0
        
        for i, (key, last_modified) in enumerate(videos):
            # Keep the most recent N videos
            if i < max_videos:
                # Also check age - delete even recent ones if too old
                # Normalize both datetbdimes for comparison
                last_mod_naive = last_modified.replace(tzinfo=None) if last_modified.tzinfo else last_modified
                cutoff_naive = cutoff_date.replace(tzinfo=None) if cutoff_date.tzinfo else cutoff_date
                if last_mod_naive < cutoff_naive:
                    try:
                        s3_client.delete_object(Bucket=bucket_name, Key=key)
                        deleted_count += 1
                        print(f"🗑️  Deleted old video: {key}", flush=True)
                    except Exception as e:
                        print(f"⚠️  Failed to delete {key}: {e}", flush=True)
                else:
                    kept_count += 1
            else:
                # Delete everything beyond the limit
                try:
                    s3_client.delete_object(Bucket=bucket_name, Key=key)
                    deleted_count += 1
                    print(f"🗑️  Deleted excess video: {key}", flush=True)
                except Exception as e:
                    print(f"⚠️  Failed to delete {key}: {e}", flush=True)
        
        if deleted_count > 0:
            print(f"🧹 Cleanup: Deleted {deleted_count} videos, kept {kept_count}", flush=True)
        else:
            print(f"✅ Cleanup: All {kept_count} videos within limits", flush=True)
            
    except Exception as e:
        print(f"⚠️  Cleanup failed: {e}", flush=True)


def check_storage_usage() -> Tuple[int, float]:
    """
    Check current storage usage.
    Returns: (video_count, total_size_mb)
    """
    try:
        s3_client, bucket_name = get_r2_client()
        videos = list_videos(s3_client, bucket_name)
        
        total_size = 0
        for key, _ in videos:
            try:
                obj = s3_client.head_object(Bucket=bucket_name, Key=key)
                total_size += obj.get("ContentLength", 0) / (1024 * 1024)  # MB
            except:
                pass
        
        return len(videos), total_size
    except Exception as e:
        print(f"⚠️  Could not check storage usage: {e}", flush=True)
        return 0, 0.0


def upload_video(local_path: Path, auto_cleanup: bool = True) -> str:
    """
    Upload video to R2 with free tier safeguards.
    
    Args:
        local_path: Path to video file
        auto_cleanup: Automatically clean old videos before upload (default: True)
    """
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    # Check file size (warn if > 50MB)
    file_size_mb = get_file_size_mb(local_path)
    if file_size_mb > 50:
        print(f"⚠️  WARNING: Video is {file_size_mb:.1f}MB (large file!)", flush=True)
    
    # Check current usage
    video_count, total_size_mb = check_storage_usage()
    
    # Free tier limits: 10 GB storage
    FREE_TIER_STORAGE_GB = 10
    FREE_TIER_STORAGE_MB = FREE_TIER_STORAGE_GB * 1024
    
    if total_size_mb + file_size_mb > FREE_TIER_STORAGE_MB * 0.5:  # Warn at 50%
        print(f"⚠️  WARNING: Storage usage high ({total_size_mb:.1f}MB / {FREE_TIER_STORAGE_MB}MB)", flush=True)
        if auto_cleanup:
            print("🧹 Running automatic cleanup...", flush=True)
            cleanup_old_videos(max_videos=30, max_age_days=3)  # More aggressive cleanup
    
    public_base = os.getenv("R2_PUBLIC_BASE_URL")
    if not public_base:
        raise ValueError("R2_PUBLIC_BASE_URL is required")

    s3_client, bucket_name = get_r2_client()

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    object_key = f"videos/{timestamp}_{local_path.name}"

    print(f"📤 Uploading {object_key} ({file_size_mb:.1f}MB)", flush=True)

    s3_client.upload_file(
        str(local_path),
        bucket_name,
        object_key,
        ExtraArgs={
            "ContentType": "video/mp4",
            "CacheControl": "public, max-age=31536000",
        },
    )

    public_url = f"{public_base}/{object_key}"
    print(f"🌐 Public URL: {public_url}", flush=True)
    
    # Show updated usage
    new_count, new_size = check_storage_usage()
    print(f"📊 Storage: {new_count} videos, {new_size:.1f}MB / {FREE_TIER_STORAGE_MB}MB", flush=True)

    return public_url
