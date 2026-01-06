"""
Update checker utility for Daygle Mail Archiver
Checks for available updates from the git repository
"""
import subprocess
import os
from pathlib import Path
from typing import Dict, Optional

def get_repo_root() -> Optional[Path]:
    """Get the repository root directory"""
    try:
        # Start from the current file's directory and go up until we find .git
        current = Path(__file__).resolve()
        # Go up from api/src/utils/ to the root
        root = current.parent.parent.parent.parent
        if (root / ".git").exists():
            return root
        return None
    except Exception:
        return None


def check_for_updates() -> Dict[str, any]:
    """
    Check if updates are available from the git repository
    
    Returns:
        dict: {
            "updates_available": bool,
            "current_commit": str,
            "latest_commit": str,
            "current_message": str,
            "latest_message": str,
            "commits_behind": int,
            "error": str or None
        }
    """
    result = {
        "updates_available": False,
        "current_commit": "",
        "latest_commit": "",
        "current_message": "",
        "latest_message": "",
        "commits_behind": 0,
        "error": None
    }
    
    try:
        repo_root = get_repo_root()
        if not repo_root:
            result["error"] = "Not in a git repository"
            return result
        
        # Change to repo directory
        os.chdir(repo_root)
        
        # Get current branch
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if branch_result.returncode != 0:
            result["error"] = "Failed to get current branch"
            return result
        
        branch = branch_result.stdout.strip()
        
        # Fetch latest changes from remote
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", branch],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if fetch_result.returncode != 0:
            result["error"] = "Failed to fetch updates"
            return result
        
        # Get current commit hash
        current_commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if current_commit_result.returncode != 0:
            result["error"] = "Failed to get current commit"
            return result
        
        current_commit = current_commit_result.stdout.strip()
        result["current_commit"] = current_commit[:7]  # Short hash
        
        # Get current commit message
        current_msg_result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%s", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if current_msg_result.returncode == 0:
            result["current_message"] = current_msg_result.stdout.strip()
        
        # Get latest commit hash from remote
        latest_commit_result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if latest_commit_result.returncode != 0:
            result["error"] = "Failed to get latest commit"
            return result
        
        latest_commit = latest_commit_result.stdout.strip()
        result["latest_commit"] = latest_commit[:7]  # Short hash
        
        # Get latest commit message
        latest_msg_result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%s", f"origin/{branch}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if latest_msg_result.returncode == 0:
            result["latest_message"] = latest_msg_result.stdout.strip()
        
        # Check if commits are different
        if current_commit != latest_commit:
            result["updates_available"] = True
            
            # Count commits behind
            count_result = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if count_result.returncode == 0:
                try:
                    result["commits_behind"] = int(count_result.stdout.strip())
                except ValueError:
                    result["commits_behind"] = 0
        
        return result
        
    except subprocess.TimeoutExpired:
        result["error"] = "Git command timed out"
        return result
    except FileNotFoundError:
        result["error"] = "Git not found"
        return result
    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"
        return result
