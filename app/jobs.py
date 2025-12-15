from github import Github
import os

def post_video(pr_number, video_url):
    token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO")  # e.g. your_user/shipvideo-demo
    g = Github(token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(f"🎬 Demo video: {video_url}")
