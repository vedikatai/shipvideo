from github import Github
import os

def comment_on_pr(repo_full_name: str, pr_number: int, video_url: str):
    """
    Posts a comment on a PR with the video URL.
    
    Args:
        repo_full_name: Full name of the repository (e.g., "owner/repo")
        pr_number: PR number
        video_url: Public URL of the uploaded video
    """
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN not set in .env")
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        pr.create_issue_comment(
            f"🎬 **Auto-generated demo video**\n\n{video_url}"
        )
        print(f"💬 Comment posted to PR with video URL: {video_url}")

    except Exception as e:
        print("❌ Failed to post comment:", e)
        raise e
