import logging
from github import Github, GithubException
import os

logger = logging.getLogger(__name__)
VIDEO_URL = "http://localhost:8000/out.mp4"

def comment_on_pr(repo_full_name: str, pr_number: int, job_id: str):
    logger.info(
        "Posting PR comment",
        extra={
            "job_id": job_id,
            "repo": repo_full_name,
            "pr": pr_number,
        }
    )

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error("Missing GITHUB_TOKEN", extra={"job_id": job_id})
        raise RuntimeError("Missing GitHub token")

    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        pr.create_issue_comment(
            f"🎬 **Auto-generated demo video**\n\n{VIDEO_URL}"
        )

        logger.info(
            "PR comment posted successfully",
            extra={"job_id": job_id}
        )

    except GithubException as e:
        logger.exception(
            "Failed to post PR comment",
            extra={
                "job_id": job_id,
                "status": e.status,
                "data": e.data,
            }
        )
        raise
