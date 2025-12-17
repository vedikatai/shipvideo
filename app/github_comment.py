from github import Github
import os

VIDEO_URL = "http://localhost:8000/out.mp4"  # replace if using other filename

def comment_on_pr(repo_full_name: str, pr_number: int):
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN not set in .env")
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        pr.create_issue_comment(
            f"🎬 **Auto-generated demo video**\n\n{VIDEO_URL}"
        )
        print("💬 Comment posted to PR")

    except Exception as e:
        print("❌ Failed to post comment:", e)
        raise e
