# R2 Free Tier Safeguards Guide

## 🆓 Free Tier Limits

Your Cloudflare R2 free tier includes:
- **10 GB storage** per month
- **1 million Class A operations** (PUT, POST, DELETE) per month
- **10 million Class B operations** (GET, HEAD) per month

## 🛡️ Automatic Safeguards

The code now includes automatic protections:

### 1. **File Size Warnings**
- Warns if video > 50MB before upload
- Shows file size in upload logs

### 2. **Storage Usage Monitoring**
- Checks current storage before each upload
- Warns at 50% capacity (5GB)
- Automatically triggers cleanup if needed

### 3. **Automatic Cleanup**
When storage gets high (>50%), automatically:
- Keeps only the **30 most recent videos**
- Deletes videos older than **3 days**
- More aggressive cleanup to stay safe

### 4. **Usage Reporting**
After each upload, shows:
- Total video count
- Total storage used
- Remaining free tier capacity

## 📊 Manual Cleanup

You can manually clean up old videos:

```python
from app.storage import cleanup_old_videos

# Keep 30 most recent videos, delete older than 3 days
cleanup_old_videos(max_videos=30, max_age_days=3)

# Or more aggressive: keep only 20 videos, delete older than 7 days
cleanup_old_videos(max_videos=20, max_age_days=7)
```

## 📈 Check Current Usage

```python
from app.storage import check_storage_usage

video_count, total_size_mb = check_storage_usage()
print(f"{video_count} videos, {total_size_mb:.1f}MB used")
```

## ⚠️ Best Practices

1. **Monitor regularly**: Check usage weekly
2. **Set cleanup limits**: Adjust `max_videos` and `max_age_days` based on your needs
3. **Video size**: Keep videos under 10MB if possible (smaller = more videos)
4. **Manual cleanup**: Run cleanup before major testing sessions

## 🚨 Warning Thresholds

- **50% storage** → Automatic cleanup triggered
- **50MB+ video** → Warning shown (still uploads)
- **100% storage** → Upload will fail (manual cleanup required)

## 💡 Cost Estimation

Assuming average video size of 5MB:
- **10 GB = 2,000 videos** (theoretical max)
- **Safe limit: ~1,500 videos** (with buffer)

With automatic cleanup (30 videos max):
- **~150MB storage** (well within free tier)
- **Plenty of room** for growth

## 🔧 Configuration

You can adjust cleanup behavior in `app/storage.py`:

```python
# In upload_video() function, line ~161:
cleanup_old_videos(max_videos=30, max_age_days=3)  # Adjust these values
```

## 📝 Monitoring

Check Cloudflare Dashboard:
1. Go to R2 → Your Bucket
2. View "Storage" tab for current usage
3. View "Analytics" for operation counts

## ✅ What's Protected

✅ **Storage limits** - Automatic cleanup  
✅ **Large files** - Warnings before upload  
✅ **Usage tracking** - Real-time reporting  
✅ **Old videos** - Automatic deletion  

## ❌ What's NOT Protected

❌ **Operation limits** - No automatic throttling (but unlikely to hit with normal use)  
❌ **Manual uploads** - Direct R2 uploads bypass safeguards  
❌ **Other buckets** - Only protects the configured bucket  

## 🎯 Recommended Settings

For active development:
```python
cleanup_old_videos(max_videos=50, max_age_days=30)
```

For production:
```python
cleanup_old_videos(max_videos=100, max_age_days=60)
```

For strict free tier:
```python
cleanup_old_videos(max_videos=20, max_age_days=7)
```

