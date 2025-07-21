# main.py
# This script will contain the core logic for checking Unreal Engine updates.
import os
import requests
from github import Github
from github.GithubException import UnknownObjectException
import google.generativeai as genai
import time
from datetime import datetime, timedelta
import subprocess
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

UE_REPO_NAME = "ldl19691031/UnrealEngine5" # Target repository
raw_limit = os.environ.get("COMMIT_SCAN_LIMIT") # Keep for manual override
COMMIT_SCAN_LIMIT = int(raw_limit) if raw_limit and raw_limit.isdigit() else None


def fetch_new_commits(github_client):
    """
    Fetches new commits from the UE repo.
    - If COMMIT_SCAN_LIMIT is set (manual run), it fetches that many recent commits.
    - Otherwise (scheduled run), it fetches commits from the last 24 hours.
    """
    print(f"Fetching commits from {UE_REPO_NAME}...")
    try:
        repo = github_client.get_repo(UE_REPO_NAME)
        print("Successfully accessed repository.")

        if COMMIT_SCAN_LIMIT:
            print(f"Manual override: Fetching the latest {COMMIT_SCAN_LIMIT} commits.")
            commits = repo.get_commits()
            new_commits = list(commits[:COMMIT_SCAN_LIMIT])
            new_commits.reverse() # Oldest to newest
        else:
            since_time = datetime.utcnow() - timedelta(hours=24)
            print(f"Scheduled run: Fetching commits since {since_time.isoformat()} UTC...")
            commits = repo.get_commits(since=since_time)
            new_commits = list(commits)
            # Commits from .get_commits(since=...) are already in chronological order.

        print(f"Found {len(new_commits)} new commits.")
        return new_commits

    except UnknownObjectException:
        print(f"Error: Repository '{UE_REPO_NAME}' not found. Check PAT permissions.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while fetching commits: {e}")
        return None


def filter_commit(commit):
    """
    Performs primary filtering to exclude obviously unimportant commits.
    Returns True if the commit is potentially important, False otherwise.
    """
    commit_message = commit.commit.message.lower()
    # Ignore commits that only touch documentation
    if all(f.filename.startswith("Documentation/") for f in commit.files):
        return False
    # Ignore simple typo fixes
    if "typo" in commit_message and len(commit.files) == 1:
        return False
    # Ignore merge commits without file changes
    if commit.parents and len(commit.parents) > 1 and not commit.files:
        return False
    # Ignore localization-only changes
    if all("Localization/" in f.filename for f in commit.files):
        return False
    return True


def analyze_commits_in_bulk(model, commits):
    """
    Analyzes a list of commits in bulk with the Gemini API and returns a formatted Markdown report.
    """
    print(f"Aggregating {len(commits)} commits for bulk analysis...")
    
    commits_data = []
    for commit in commits:
        # IMPORTANT: To comply with Epic Games' license and prevent leaking sensitive information,
        # DO NOT include file contents or diffs in the data sent to the AI.
        # Only commit messages and file paths are used.
        file_list = "\n".join([f"- {file.filename}" for file in commit.files])
        commit_info = f"""---
Commit: {commit.sha}
URL: {commit.html_url}
Message:
{commit.commit.message}
Files Changed:
{file_list}
"""
        commits_data.append(commit_info)
    
    aggregated_commits = "\n".join(commits_data)

    prompt = f"""你是一位Unreal Engine专家，同时也是一名中文技术写作者。下面是从Unreal Engine的GitHub仓库获取的多个提交信息列表。

你的任务是分析这些提交，**提取对开发者来说特别重要的变更，用中文撰写摘要报告**。

报告需采用Markdown格式，并遵循以下指南：

### 中文报告指南
1.  **只提取最重要内容：** 不要罗列所有提交，只需筛选出新功能添加、大规模重构、API重大变更、对性能有显著影响的修复等特别值得关注的变更。**你认为不重要的提交请完全省略，不要在报告中出现。**
2.  **分类整理：** 将提取的内容按以下类别分组并添加标题：
    - `## 🚀 新功能 (New Features)`
    - `## 💥 重大变更 (Breaking Changes)`
    - `## ✨ 性能优化 (Performance Improvements)`
    - `## 🛠️ 重构与改进 (Refactoring & Improvements)`
    - `## 🐛 Bug修复 (Bug Fixes)`
    - `## 📚 其他 (Miscellaneous)`
3.  **摘要与归纳：**
    *   相似内容的更新请合并归纳。
    *   每项内容请简明扼要地描述变更。
    *   对特别重要的变更，请补充多行详细说明，包括背景和对开发者的影响。
4.  **链接列举：**
    *   在每个分组下，列出所有相关提交的链接，格式为 `* ([提交SHA前7位](完整提交URL))`。
5.  **Markdown格式：** 使用Markdown语法，确保报告易读。
6.  **语言要求：** 报告必须使用中文撰写，确保技术术语准确。
7.  **避免冗余：** 不要重复提交信息，确保每个变更只出现一次。
8.  **直接给出报告：** 不要添加任何额外的解释或说明，直接输出报告内容。比如，不要说“以下是报告”或者“好的...”，直接开始报告内容。
```markdown
{{这里是中文报告}}

---

---
以下是分析对象的提交信息：
---

{aggregated_commits}
"""

    try:
        print(f"  > Sending aggregated prompt to Gemini for {len(commits)} commits...")
        
        # --- Start of Detailed Logging ---
        # print(f"\n--- BULK PROMPT ---\n{prompt}\n--------------------")
        # --- End of Detailed Logging ---

        response = model.generate_content(prompt)
        
        # --- Start of Detailed Logging ---
        print(f"--- BULK RESPONSE ---\n{response.text}\n--------------------\n")
        # --- End of Detailed Logging ---

        print(f"  < Received bulk response from Gemini.")
        
        return response.text

    except Exception as e:
        print(f"Error analyzing commits in bulk with AI: {e}")
        return None


def _run_graphql_query(query, variables, pat):
    """A helper function to run a GraphQL query."""
    headers = {"Authorization": f"bearer {pat}"}
    response = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=headers
    )
    if response.status_code == 200:
        result = response.json()
        if "errors" in result:
            raise Exception(f"GraphQL query failed: {result['errors']}")
        return result
    else:
        raise Exception(f"Query failed with status code {response.status_code}: {response.text}")

def get_repository_and_category_ids(repo_name, pat, category_name="Daily Reports"):
    """Gets the repository and discussion category IDs using the GraphQL API."""
    owner, name = repo_name.split('/')
    query = """
    query GetRepoAndCategory($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        id
        discussionCategories(first: 10) {
          nodes {
            id
            name
          }
        }
      }
    }
    """
    variables = {"owner": owner, "name": name}
    result = _run_graphql_query(query, variables, pat)
    
    repo_id = result["data"]["repository"]["id"]
    category_id = None
    for category in result["data"]["repository"]["discussionCategories"]["nodes"]:
        if category["name"] == category_name:
            category_id = category["id"]
            break
            
    if not category_id:
        # Fallback to the first category if the named one isn't found
        categories = result["data"]["repository"]["discussionCategories"]["nodes"]
        if categories:
            fallback_category = categories[0]
            category_id = fallback_category["id"]
            print(f"Warning: Discussion category '{category_name}' not found. Falling back to '{fallback_category['name']}'.")
        else:
            raise Exception(f"No discussion categories found in the repository.")
        
    return repo_id, category_id

def create_discussion(repo_name, title, body, pat, category_name="Daily Reports"):
    """Creates a new GitHub Discussion using the GraphQL API."""
    print("---")
    print("Creating GitHub Discussion via GraphQL...")
    try:
        repo_id, category_id = get_repository_and_category_ids(repo_name, pat, category_name)
        print(f"Found Repository ID: {repo_id}")
        print(f"Found Category ID: {category_id} for category '{category_name}'")

        mutation_query = """
        mutation CreateDiscussion($repoId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
          createDiscussion(input: {
            repositoryId: $repoId,
            categoryId: $categoryId,
            title: $title,
            body: $body
          }) {
            discussion {
              url
            }
          }
        }
        """
        variables = {
            "repoId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body
        }

        result = _run_graphql_query(mutation_query, variables, pat)
        discussion_url = result["data"]["createDiscussion"]["discussion"]["url"]
        print(f"Successfully created GitHub Discussion: {discussion_url}")
        return True

    except Exception as e:
        print(f"An error occurred while creating discussion: {e}")
        return False


def main():
    print("---")
    """
    Main function to execute the update check.
    """
    print("=============================================")
    print("Starting Unreal Engine Update Check Script")
    print("=============================================")
    
    # --- API Setup ---
    print("\n--- 1. Setting up APIs ---")
    pat = os.environ.get("UE_REPO_PAT")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    
    if not pat:
        print("FATAL: UE_REPO_PAT environment variable not set.")
        return
    print("UE_REPO_PAT found.")
        
    if not gemini_api_key:
        print("FATAL: GEMINI_API_KEY environment variable not set.")
        return
    print("GEMINI_API_KEY found.")
    
    try:
        print("Initializing GitHub client...")
        github_client = Github(pat)
        print("GitHub client initialized.")
        
        gemini_model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
        print(f"Configuring Gemini API with model: {gemini_model_name}...")
        genai.configure(api_key=gemini_api_key)
        ai_model = genai.GenerativeModel(gemini_model_name)
        print("Gemini API configured.")
    except Exception as e:
        print(f"FATAL: Failed to initialize APIs: {e}")
        return

    # --- State and Commit Fetching ---
    print("\n--- 2. Fetching Commits ---")
    new_commits = fetch_new_commits(github_client)

    if new_commits is None:
        print("Failed to fetch commits. Exiting.")
        return

    if not new_commits:
        print("No new commits found since last check. Exiting.")
        return

    # --- Process Commits ---
    print("\n--- 3. Analyzing New Commits ---")
    important_commits = [commit for commit in new_commits if filter_commit(commit)]
    
    if not important_commits:
        print("No potentially important commits found after filtering. Exiting.")
        return
        
    print(f"Found {len(important_commits)} potentially important commits to analyze.")

    # --- Generate Report and Post Discussion ---
    print("\n--- 4. Generating Report ---")
    report_body = analyze_commits_in_bulk(ai_model, important_commits)
    
    if report_body:
        report_title = f"Unreal Engine Daily Report - {time.strftime('%Y-%m-%d')}"
        
        # --- Discussion Target Validation ---
        # To prevent accidental information leakage, posting to a specific, private
        # repository is mandatory. These environment variables MUST be set.
        discussion_repo_name = os.environ.get("DISCUSSION_REPO")
        discussion_repo_pat = os.environ.get("DISCUSSION_REPO_PAT")

        if not discussion_repo_name:
            print("FATAL: DISCUSSION_REPO environment variable is not set.")
            print("This must be set to the target repository (e.g., 'owner/repo-name') to prevent accidental leaks.")
            return

        if not discussion_repo_pat:
            print("FATAL: DISCUSSION_REPO_PAT environment variable is not set.")
            print("A Personal Access Token with 'discussion:write' permissions for the target repository is required.")
            return
            
        discussion_category = os.environ.get("DISCUSSION_CATEGORY", "Daily Reports")
        
        print(f"Attempting to post to repository '{discussion_repo_name}' in category: '{discussion_category}'")
        create_discussion(discussion_repo_name, report_title, report_body, discussion_repo_pat, category_name=discussion_category)
    else:
        print("Failed to generate report from AI. Skipping discussion post.")

    # --- Save State ---
    # State saving is no longer needed as we fetch by time window
    
    print("\n=============================================")
    print("Update Check Script Finished Successfully")
    print("=============================================")

if __name__ == "__main__":
    main()
