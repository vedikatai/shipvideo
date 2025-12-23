import boto3
import os
from pathlib import Path
from datetime import datetime
from botocore.config import Config


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


def upload_video(local_path: Path) -> str:
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    public_base = os.getenv("R2_PUBLIC_BASE_URL")
    if not public_base:
        raise ValueError("R2_PUBLIC_BASE_URL is required")

    s3_client, bucket_name = get_r2_client()

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    object_key = f"videos/{timestamp}_{local_path.name}"

    print(f"📤 Uploading {object_key}", flush=True)

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

    return public_url
