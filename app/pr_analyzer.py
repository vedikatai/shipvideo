"""
Analyzes PR diffs to generate dynamic capture steps based on code changes.
Eliminates the need for hardcoded templates by understanding what actually changed.
"""
import os
import re
from typing import List, Dict, Optional
from github import Github


def fetch_pr_files(repo_full_name: str, pr_number: int) -> List[Dict[str, str]]:
    """
    Fetches changed files for a PR using GitHub API.
    
    Args:
        repo_full_name: Full name of the repository (e.g., "owner/repo")
        pr_number: PR number
    
    Returns:
        List of dicts with 'filename', 'status' (added/modified/deleted), and 'path'
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set in .env")
    
    g = Github(token)
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    
    # Get changed files using PyGithub's get_files() method
    files = []
    for file in pr.get_files():
        # Map GitHub status to our status
        status_map = {
            'added': 'added',
            'modified': 'modified',
            'removed': 'deleted',
            'renamed': 'modified',  # Treat renamed as modified
        }
        status = status_map.get(file.status, 'modified')
        
        files.append({
            'filename': file.filename.split('/')[-1],
            'path': file.filename,
            'status': status
        })
    
    return files




def detect_routes(changed_files: List[Dict[str, str]]) -> List[str]:
    """
    Detects routes/pages that were changed based on file paths.
    Supports Next.js App Router, Pages Router, and common patterns.
    
    Args:
        changed_files: List of changed file dicts
    
    Returns:
        List of route paths (e.g., ['/billing', '/dashboard'])
    """
    routes = []
    
    for file in changed_files:
        path = file['path']
        
        # Next.js App Router: app/[route]/page.tsx or app/[route]/page.jsx
        match = re.search(r'app/([^/]+)/page\.(tsx|jsx|ts|js)$', path)
        if match:
            route = '/' + match.group(1)
            if route not in routes:
                routes.append(route)
        
        # Next.js Pages Router: pages/[route].tsx or pages/[route]/index.tsx
        match = re.search(r'pages/([^/]+)(?:/index)?\.(tsx|jsx|ts|js)$', path)
        if match:
            route = '/' + match.group(1)
            if route == '/index':
                route = '/'
            if route not in routes:
                routes.append(route)
        
        # React Router or similar: src/pages/[route].tsx
        match = re.search(r'src/pages/([^/]+)\.(tsx|jsx|ts|js)$', path)
        if match:
            route = '/' + match.group(1).lower()
            if route not in routes:
                routes.append(route)
        
        # Look for route definitions in the path
        if 'route' in path.lower() or 'page' in path.lower():
            # Try to extract route from path structure
            parts = path.split('/')
            for i, part in enumerate(parts):
                if part in ['pages', 'app', 'routes'] and i + 1 < len(parts):
                    route = '/' + parts[i + 1].replace('.tsx', '').replace('.jsx', '').replace('.ts', '').replace('.js', '')
                    if route != '/' and route not in routes:
                        routes.append(route)
    
    return routes


def detect_components(changed_files: List[Dict[str, str]]) -> List[str]:
    """
    Detects React components that were changed.
    
    Args:
        changed_files: List of changed file dicts
    
    Returns:
        List of component names (e.g., ['Button', 'Dashboard'])
    """
    components = []
    
    for file in changed_files:
        filename = file['filename']
        path = file['path']
        
        # React component files: ComponentName.tsx, ComponentName.jsx
        if filename.endswith(('.tsx', '.jsx')) and 'component' in path.lower():
            component_name = filename.replace('.tsx', '').replace('.jsx', '')
            if component_name not in components:
                components.append(component_name)
        
        # Also check for common component patterns
        if '/components/' in path or '/Components/' in path:
            component_name = filename.replace('.tsx', '').replace('.jsx', '').replace('.ts', '').replace('.js', '')
            if component_name not in components:
                components.append(component_name)
    
    return components


def generate_steps_from_diff(repo_full_name: str, pr_number: int) -> List[Dict[str, any]]:
    """
    Analyzes PR diff and generates capture steps dynamically.
    
    Args:
        repo_full_name: Full name of the repository
        pr_number: PR number
    
    Returns:
        List of step dictionaries for capture_demo()
    """
    try:
        print(f"🔍 Fetching PR files for {repo_full_name}#{pr_number}...", flush=True)
        changed_files = fetch_pr_files(repo_full_name, pr_number)
        
        if not changed_files:
            print("⚠️ No changed files detected, using default steps", flush=True)
            return [{"action": "screenshot"}]
        
        print(f"📁 Found {len(changed_files)} changed file(s)", flush=True)
        for file in changed_files:
            print(f"   - {file['path']} ({file['status']})", flush=True)
        
        # Detect routes and components
        routes = detect_routes(changed_files)
        components = detect_components(changed_files)
        
        print(f"🛣️ Detected routes: {routes}", flush=True)
        print(f"🧩 Detected components: {components}", flush=True)
        
        # Generate steps based on what we found
        steps = []
        
        # Always start with a screenshot of the home page
        steps.append({"action": "screenshot"})
        
        # If we found routes, navigate to each one
        if routes:
            for route in routes:
                steps.append({"action": "goto", "url": route})
                steps.append({"action": "screenshot"})
        else:
            # If no routes but we have components, try to find them on the page
            # For now, just take another screenshot
            # In the future, we could try to find and interact with components
            steps.append({"action": "screenshot"})
        
        # If we only found components (no routes), we might want to interact with them
        # This is a future enhancement - for now we just take screenshots
        
        print(f"✅ Generated {len(steps)} capture steps from diff analysis", flush=True)
        return steps
        
    except Exception as e:
        print(f"❌ Error analyzing PR diff: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        # Fallback to default steps
        print("🔄 Falling back to default steps", flush=True)
        return [{"action": "screenshot"}]


def generate_steps_from_diff_with_fallback(repo_full_name: str, pr_number: int, pr_title: Optional[str] = None) -> List[Dict[str, any]]:
    """
    Generates steps from diff with intelligent fallback.
    If diff analysis fails or produces no useful results, falls back to title-based heuristics.
    
    Args:
        repo_full_name: Full name of the repository
        pr_number: PR number
        pr_title: Optional PR title for fallback
    
    Returns:
        List of step dictionaries for capture_demo()
    """
    steps = generate_steps_from_diff(repo_full_name, pr_number)
    
    # If we only got one screenshot (default fallback), try title-based heuristics
    if len(steps) == 1 and steps[0].get("action") == "screenshot" and pr_title:
        print("🔄 Trying title-based fallback...", flush=True)
        title_lower = pr_title.lower()
        
        # Simple keyword matching as fallback
        if "billing" in title_lower:
            steps = [
                {"action": "screenshot"},
                {"action": "goto", "url": "/billing"},
                {"action": "screenshot"},
            ]
        elif "dashboard" in title_lower:
            steps = [
                {"action": "screenshot"},
                {"action": "goto", "url": "/dashboard"},
                {"action": "screenshot"},
            ]
        else:
            # Try to extract route from title
            # Look for patterns like "Add /settings page" or "Update billing page"
            route_match = re.search(r'[/]([a-z-]+)', title_lower)
            if route_match:
                route = '/' + route_match.group(1)
                steps = [
                    {"action": "screenshot"},
                    {"action": "goto", "url": route},
                    {"action": "screenshot"},
                ]
    
    return steps
