"""
Unified Reddit Scraper - Enhanced with Smart Default Mode
Works as both CLI tool and importable Python module

Installation:
    pip install requests beautifulsoup4 lxml python-dotenv

Usage as CLI:
    python reddit_unified.py                              # Interactive mode
    python reddit_unified.py --default r/python           # Auto-detect: subreddit
    python reddit_unified.py --default u/username         # Auto-detect: user
    python reddit_unified.py --default "machine learning" # Auto-detect: search

Usage as Module:
    from reddit_unified import reddit

    # Default mode - auto-detect
    result = reddit("r/python")
    result = reddit("u/username")
    result = reddit("machine learning tips")

    # Specific modes
    result = reddit("r/python", mode="subreddit", limit=10, category="top")
    result = reddit("u/username", mode="user", limit=10)
    result = reddit("AI trends", mode="search", limit=10)
    result = reddit("https://reddit.com/r/python/comments/...", mode="post")

    # Get full results
    print(result['stats'])
    print(result['posts'])
    print(result['comments'])

Features:
    - Smart default mode: auto-detects input type
    - Scrape posts with ALL comments and nested replies
    - Fetch subreddit posts with FULL comments for each
    - Search Reddit by keyword/phrase with comments
    - User activity scraping with comments
    - Rate limiting to avoid IP bans
    - No API keys required
    - Works as CLI or importable module
"""

import os
import sys
import json
import time
import argparse
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from pathlib import Path
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

# Configuration
REDDIT_BASE = "https://www.reddit.com"
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RATE_LIMIT_DELAY = 2  # Seconds between requests
OUTPUT_DIR = Path("reddit_data")


class RedditScraper:
    """Unified Reddit scraper using requests + BeautifulSoup."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.session = self._setup_session()
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)
        if self.verbose:
            self._log("✓ Reddit Scraper initialized")

    def _setup_session(self) -> requests.Session:
        """Setup requests session with headers."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        return session

    def _log(self, message: str):
        """Print log message."""
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def _delay(self):
        """Add rate limiting delay."""
        time.sleep(RATE_LIMIT_DELAY)

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename."""
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        return safe[:50]

    def _detect_input_type(self, user_input: str) -> Tuple[str, str]:
        """
        Detect the type of input and return (type, cleaned_value).

        Types:
        - 'subreddit': r/name, /r/name, name
        - 'user': u/name, /u/name
        - 'search': anything else (phrase/keyword)
        - 'post': full URL with /comments/
        """
        user_input = (user_input or "").strip()

        # Check for URL
        if user_input.startswith('http'):
            if '/comments/' in user_input:
                return 'post', user_input
            elif '/r/' in user_input:
                return 'subreddit', user_input
            elif '/user/' in user_input:
                return 'user', user_input

        # Check for u/ prefix (user)
        if user_input.lower().startswith('u/') or user_input.lower().startswith('/u/'):
            username = re.sub(r'^/?(u/)?', '', user_input, flags=re.IGNORECASE).strip('/')
            return 'user', username

        # Check for r/ prefix (subreddit)
        if user_input.lower().startswith('r/') or user_input.lower().startswith('/r/'):
            subreddit = re.sub(r'^/?(r/)?', '', user_input, flags=re.IGNORECASE).strip('/')
            return 'subreddit', subreddit

        # Check if it's a valid subreddit name (alphanumeric, underscore, 3-21 chars)
        if re.match(r'^[a-zA-Z0-9_]{3,21}$', user_input):
            return 'subreddit', user_input

        # Default: treat as search query
        return 'search', user_input

    def _normalize_subreddit(self, name: str) -> str:
        """
        Normalize subreddit input.
        Handles: r/python, /r/python, python, https://reddit.com/r/python
        """
        name = (name or "").strip()

        # Extract from URL
        if name.startswith('http'):
            match = re.search(r'/r/([a-zA-Z0-9_]+)', name)
            if match:
                return match.group(1)

        # Remove prefixes
        name = re.sub(r'^/?(r/)?', '', name, flags=re.IGNORECASE)
        name = name.strip('/')

        # Validate
        if re.match(r'^[a-zA-Z0-9_]{3,21}$', name):
            return name

        return ""

    def _normalize_username(self, username: str) -> str:
        """
        Normalize username input.
        Handles: u/username, /u/username, username
        """
        username = (username or "").strip()

        # Extract from URL
        if username.startswith('http'):
            match = re.search(r'/user/([a-zA-Z0-9_\-]+)', username)
            if match:
                return match.group(1)

        # Remove prefixes
        username = re.sub(r'^/?(u/)?', '', username, flags=re.IGNORECASE)
        username = username.strip('/')

        # Validate
        if re.match(r'^[a-zA-Z0-9_\-]{3,20}$', username):
            return username

        return ""

    def _normalize_url(self, url: str) -> str:
        """
        Normalize Reddit URL.
        Handles: reddit.com/r/..., www.reddit.com/r/..., https://...
        """
        url = (url or "").strip()

        if not url.startswith('http'):
            if url.startswith('r/') or url.startswith('/r/'):
                url = f"{REDDIT_BASE}/{url}"
            elif url.startswith('u/') or url.startswith('/u/'):
                url = f"{REDDIT_BASE}/{url}"
            elif not url.startswith('/'):
                url = f"{REDDIT_BASE}/{url}"

        # Remove www
        url = url.replace('www.reddit.com', 'reddit.com')

        return url

    def _fetch_json(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Fetch JSON from Reddit URL."""
        try:
            if not url.endswith('.json'):
                url = f"{url}.json" if url.endswith('/') else f"{url}/.json"

            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            self._delay()
            return response.json()
        except Exception as e:
            self._log(f"✗ Error fetching: {str(e)[:60]}")
            return None

    def _extract_post_data(self, post_json: Dict) -> Dict:
        """Extract post data from JSON."""
        try:
            data = post_json.get('data', {})
            return {
                'id': data.get('id'),
                'title': data.get('title'),
                'author': data.get('author'),
                'subreddit': data.get('subreddit'),
                'url': data.get('url'),
                'selftext': data.get('selftext', '')[:3000],
                'score': data.get('score', 0),
                'upvote_ratio': data.get('upvote_ratio'),
                'num_comments': data.get('num_comments', 0),
                'created_utc': data.get('created_utc'),
                'edited': data.get('edited'),
                'permalink': data.get('permalink'),
                'is_self': data.get('is_self'),
                'is_video': data.get('is_video'),
                'over_18': data.get('over_18'),
                'spoiler': data.get('spoiler'),
                'gilded': data.get('gilded', 0),
                'all_awardings': data.get('all_awardings', []),
            }
        except Exception as e:
            self._log(f"✗ Error extracting post data: {e}")
            return {}

    def _extract_comment_data(self, comment_json: Dict) -> Dict:
        """Extract comment data from JSON."""
        try:
            data = comment_json.get('data', {})
            return {
                'id': data.get('id'),
                'author': data.get('author'),
                'body': data.get('body', '')[:2000],
                'score': data.get('score', 0),
                'created_utc': data.get('created_utc'),
                'edited': data.get('edited'),
                'depth': data.get('depth', 0),
                'parent_id': data.get('parent_id'),
                'gilded': data.get('gilded', 0),
                'awards': data.get('all_awardings', []),
            }
        except Exception as e:
            return {}

    def _fetch_comments_recursive(self, comment_list: List, depth: int = 0, max_depth: int = 10) -> List[Dict]:
        """Recursively fetch and process comments."""
        all_comments = []

        if depth > max_depth:
            return all_comments

        for item in comment_list:
            if item.get('kind') == 'more':
                continue

            if item.get('kind') == 't1':  # Comment
                comment_data = self._extract_comment_data(item)

                # Check for nested comments
                if 'replies' in item and item['replies']:
                    replies_list = item['replies'].get('data', {}).get('children', [])
                    comment_data['replies'] = self._fetch_comments_recursive(replies_list, depth + 1, max_depth)
                else:
                    comment_data['replies'] = []

                all_comments.append(comment_data)

        return all_comments

    def _fetch_post_with_comments(self, post_url: str) -> Tuple[Dict, List[Dict]]:
        """
        Fetch a single post with all its comments.

        Returns:
            Tuple of (post_data, comments)
        """
        # Normalize URL
        post_url = self._normalize_url(post_url)

        # Fetch post JSON
        post_json = self._fetch_json(post_url)
        if not post_json:
            return {}, []

        # Extract post data
        try:
            post_listing = post_json[0]['data']['children'][0]
            post_data = self._extract_post_data(post_listing)
        except Exception as e:
            self._log(f"✗ Error extracting post: {e}")
            return {}, []

        # Extract comments
        comments = []
        try:
            comments_listing = post_json[1]['data']['children']
            comments = self._fetch_comments_recursive(comments_listing)
        except Exception as e:
            self._log(f"✗ Error extracting comments: {e}")

        return post_data, comments

    def scrape_post(self, post_url: str) -> Dict:
        """
        Scrape a Reddit post with all comments.

        Args:
            post_url: Full URL to Reddit post

        Returns:
            Dictionary with post data and comments
        """
        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING POST")
        self._log(f"{'=' * 70}")

        post_data, comments = self._fetch_post_with_comments(post_url)

        if not post_data:
            return {'error': 'Failed to fetch post', 'type': 'post'}

        self._log(f"✓ Title: {post_data.get('title', '')[:50]}")
        self._log(f"✓ Score: {post_data.get('score')} | Comments: {len(comments)}")

        result = {
            'type': 'post',
            'post': post_data,
            'comments': comments,
            'stats': {
                'total_comments': len(comments),
                'total_replies': sum(len(c.get('replies', [])) for c in comments),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"post_{post_data.get('id', 'unknown')}")
        return result

    def scrape_subreddit(self, subreddit: str, limit: int = 25, category: str = "hot",
                         time_filter: str = "week", scrape_comments: bool = True) -> Dict:
        """
        Scrape subreddit posts with optional comments.

        Args:
            subreddit: Subreddit name
            limit: Number of posts to fetch
            category: hot/top/new/rising/controversial
            time_filter: all/year/month/week/day/hour
            scrape_comments: If True, fetch comments for each post

        Returns:
            Dictionary with subreddit data and posts
        """
        # Normalize subreddit name
        subreddit = self._normalize_subreddit(subreddit)
        if not subreddit:
            return {'error': 'Invalid subreddit name', 'type': 'subreddit'}

        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING SUBREDDIT: r/{subreddit}")
        self._log(f"Category: {category} | Limit: {limit}")
        if scrape_comments:
            self._log(f"Scraping comments for each post...")
        self._log(f"{'=' * 70}")

        # Fetch subreddit info
        sub_info_url = f"{REDDIT_BASE}/r/{subreddit}/about.json"
        sub_info_json = self._fetch_json(sub_info_url)

        sub_info = {}
        if sub_info_json:
            try:
                sub_data = sub_info_json.get('data', {})
                sub_info = {
                    'name': sub_data.get('display_name'),
                    'title': sub_data.get('title'),
                    'public_description': sub_data.get('public_description', '')[:1000],
                    'subscribers': sub_data.get('subscribers', 0),
                    'created_utc': sub_data.get('created_utc'),
                }
                self._log(f"✓ Subreddit: {sub_info.get('title')} ({sub_info.get('subscribers')} subscribers)")
            except Exception as e:
                self._log(f"✗ Error extracting subreddit info: {e}")

        # Build URL
        url = f"{REDDIT_BASE}/r/{subreddit}/{category}.json"
        params = {'limit': limit}

        if category in ['top', 'controversial']:
            params['t'] = time_filter

        # Fetch posts
        posts_json = self._fetch_json(url, params=params)
        if not posts_json:
            return {'error': 'Failed to fetch subreddit posts', 'type': 'subreddit'}

        # Extract posts
        posts = []
        try:
            posts_listing = posts_json.get('data', {}).get('children', [])

            for idx, post_item in enumerate(posts_listing, 1):
                if post_item.get('kind') == 't3':  # Post
                    post_data = self._extract_post_data(post_item)

                    # Scrape comments for this post if requested
                    comments = []
                    if scrape_comments and post_data.get('permalink'):
                        self._log(
                            f"  [{idx}/{len(posts_listing)}] Fetching comments for: {post_data.get('title', '')[:40]}...")

                        post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                        _, comments = self._fetch_post_with_comments(post_url)
                        self._log(f"      ✓ Got {len(comments)} comments")

                    post_data['comments'] = comments
                    posts.append(post_data)

            self._log(f"✓ Extracted {len(posts)} posts")

        except Exception as e:
            self._log(f"✗ Error extracting posts: {e}")

        result = {
            'type': 'subreddit',
            'subreddit_info': sub_info,
            'posts': posts,
            'stats': {
                'posts_extracted': len(posts),
                'total_comments': sum(len(p.get('comments', [])) for p in posts),
                'category': category,
                'time_filter': time_filter,
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"subreddit_{subreddit}")
        return result

    def search_reddit(self, query: str, limit: int = 25, scrape_comments: bool = True) -> Dict:
        """
        Search Reddit for posts by keyword/phrase with optional comments.

        Args:
            query: Search query (supports phrases in quotes)
            limit: Number of results
            scrape_comments: If True, fetch comments for each post

        Returns:
            Dictionary with search results
        """
        self._log(f"\n{'=' * 70}")
        self._log(f"SEARCHING REDDIT: '{query}'")
        self._log(f"Limit: {limit}")
        if scrape_comments:
            self._log(f"Scraping comments for each post...")
        self._log(f"{'=' * 70}")

        url = f"{REDDIT_BASE}/search.json"
        params = {
            'q': query,
            'limit': limit,
            'sort': 'relevance',
            'type': 'link',
        }

        # Fetch search results
        search_json = self._fetch_json(url, params=params)
        if not search_json:
            return {'error': 'Search failed', 'type': 'search'}

        # Extract posts
        posts = []
        try:
            posts_listing = search_json.get('data', {}).get('children', [])

            for idx, post_item in enumerate(posts_listing, 1):
                if post_item.get('kind') == 't3':  # Post
                    post_data = self._extract_post_data(post_item)

                    # Scrape comments for this post if requested
                    comments = []
                    if scrape_comments and post_data.get('permalink'):
                        self._log(
                            f"  [{idx}/{len(posts_listing)}] Fetching comments for: {post_data.get('title', '')[:40]}...")

                        post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                        _, comments = self._fetch_post_with_comments(post_url)
                        self._log(f"      ✓ Got {len(comments)} comments")

                    post_data['comments'] = comments
                    posts.append(post_data)

            self._log(f"✓ Found {len(posts)} posts matching '{query}'")

        except Exception as e:
            self._log(f"✗ Error extracting search results: {e}")

        result = {
            'type': 'search',
            'query': query,
            'posts': posts,
            'stats': {
                'results': len(posts),
                'total_comments': sum(len(p.get('comments', [])) for p in posts),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"search_{self._sanitize_filename(query)}")
        return result

    def scrape_user(self, username: str, limit: int = 25, scrape_post_comments: bool = False) -> Dict:
        """
        Scrape user activity (posts and comments).

        Args:
            username: Reddit username
            limit: Number of items to fetch
            scrape_post_comments: If True, fetch comments on user's posts

        Returns:
            Dictionary with user data
        """
        # Normalize username
        username = self._normalize_username(username)
        if not username:
            return {'error': 'Invalid username', 'type': 'user'}

        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING USER: u/{username}")
        self._log(f"Limit: {limit}")
        if scrape_post_comments:
            self._log(f"Scraping comments for user's posts...")
        self._log(f"{'=' * 70}")

        # Fetch user info
        user_info_url = f"{REDDIT_BASE}/user/{username}/about.json"
        user_info_json = self._fetch_json(user_info_url)

        user_info = {}
        if user_info_json:
            try:
                user_data = user_info_json.get('data', {})
                user_info = {
                    'name': user_data.get('name'),
                    'link_karma': user_data.get('link_karma', 0),
                    'comment_karma': user_data.get('comment_karma', 0),
                    'created_utc': user_data.get('created_utc'),
                    'is_gold': user_data.get('is_gold'),
                    'is_mod': user_data.get('is_mod'),
                    'verified': user_data.get('verified'),
                }
                self._log(f"✓ User: {user_info.get('name')}")
                self._log(
                    f"  Link Karma: {user_info.get('link_karma')} | Comment Karma: {user_info.get('comment_karma')}")
            except Exception as e:
                self._log(f"✗ Error extracting user info: {e}")

        # Fetch user posts
        posts_url = f"{REDDIT_BASE}/user/{username}/submitted.json"
        posts_json = self._fetch_json(posts_url, params={'limit': limit})

        posts = []
        if posts_json:
            try:
                posts_listing = posts_json.get('data', {}).get('children', [])
                for idx, post_item in enumerate(posts_listing, 1):
                    if post_item.get('kind') == 't3':
                        post_data = self._extract_post_data(post_item)

                        # Scrape comments for this post if requested
                        comments = []
                        if scrape_post_comments and post_data.get('permalink'):
                            post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                            _, comments = self._fetch_post_with_comments(post_url)

                        post_data['comments'] = comments
                        posts.append(post_data)
            except Exception as e:
                self._log(f"✗ Error extracting user posts: {e}")

        self._log(f"✓ Extracted {len(posts)} posts")

        # Fetch user comments
        comments_url = f"{REDDIT_BASE}/user/{username}/comments.json"
        comments_json = self._fetch_json(comments_url, params={'limit': limit})

        comments = []
        if comments_json:
            try:
                comments_listing = comments_json.get('data', {}).get('children', [])
                for comment_item in comments_listing:
                    if comment_item.get('kind') == 't1':
                        comment_data = self._extract_comment_data(comment_item)
                        comments.append(comment_data)
            except Exception as e:
                self._log(f"✗ Error extracting user comments: {e}")

        self._log(f"✓ Extracted {len(comments)} comments")

        result = {
            'type': 'user',
            'user_info': user_info,
            'posts': posts,
            'comments': comments,
            'stats': {
                'posts': len(posts),
                'comments': len(comments),
                'total_post_comments': sum(len(p.get('comments', [])) for p in posts),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"user_{username}")
        return result

    def _save_result(self, data: Dict, filename: str):
        """Save scrape result to JSON file."""
        try:
            safe_filename = self._sanitize_filename(filename)
            filepath = self.output_dir / f"{safe_filename}.json"

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            self._log(f"✓ Saved: {filepath}")
        except Exception as e:
            self._log(f"✗ Error saving file: {e}")


def reddit(
        user_input: str,
        mode: Optional[str] = None,
        limit: int = 10,
        category: str = "hot",
        time_filter: str = "week",
        scrape_comments: bool = True,
        verbose: bool = True,
        save: bool = True,
) -> Dict:
    """
    Main function to scrape Reddit content - works as importable module.

    Args:
        user_input: Input to scrape (required)
                   - Subreddit: 'r/python', 'python', '/r/python'
                   - User: 'u/username', 'username' (if unambiguous)
                   - Search: 'machine learning', '"python tips"'
                   - Post: Full URL

        mode: Specific mode to use (optional, auto-detects if None)
              - 'subreddit': Force subreddit mode
              - 'user': Force user mode
              - 'search': Force search mode
              - 'post': Force post mode
              - None: Auto-detect based on input

        limit: Number of items to fetch (default 10)
        category: Post category for subreddit (default 'hot')
                 - 'hot', 'top', 'new', 'rising', 'controversial'
        time_filter: Time filter for top/controversial (default 'week')
                    - 'all', 'year', 'month', 'week', 'day', 'hour'
        scrape_comments: Whether to scrape comments (default True)
        verbose: Whether to print progress (default True)
        save: Whether to save results to file (default True)

    Returns:
        Dictionary with scraped data and metadata

    Example:
        # Auto-detect subreddit
        result = reddit("r/python")

        # Auto-detect user
        result = reddit("u/username")

        # Auto-detect search
        result = reddit("machine learning tips")

        # Specific mode with options
        result = reddit("python", mode="subreddit", limit=20, category="top")
        result = reddit("username", mode="user", limit=10)
        result = reddit("AI trends", mode="search", limit=15, scrape_comments=False)

        # Post URL
        result = reddit("https://reddit.com/r/python/comments/...")

        # Access results
        print(result['type'])           # 'subreddit', 'user', 'search', 'post'
        print(result['stats'])          # Statistics
        print(result['posts'])          # List of posts
        print(result['comments'])       # Comments (if user mode)
        print(result['subreddit_info']) # Info (if subreddit mode)
    """
    scraper = RedditScraper(verbose=verbose)

    # Auto-detect mode if not specified
    if not mode:
        detected_mode, value = scraper._detect_input_type(user_input)
        mode = detected_mode
        if verbose:
            scraper._log(f"✓ Auto-detected: {mode.upper()}")
    else:
        value = user_input

    try:
        if mode == 'post':
            return scraper.scrape_post(user_input)

        elif mode == 'subreddit':
            return scraper.scrape_subreddit(
                value,
                limit=limit,
                category=category,
                time_filter=time_filter,
                scrape_comments=scrape_comments
            )

        elif mode == 'user':
            return scraper.scrape_user(
                value,
                limit=limit,
                scrape_post_comments=scrape_comments
            )

        elif mode == 'search':
            return scraper.search_reddit(
                value,
                limit=limit,
                scrape_comments=scrape_comments
            )

        else:
            return {'error': f'Unknown mode: {mode}', 'type': mode}

    except Exception as e:
        return {'error': str(e), 'type': mode}


def interactive_mode(scraper: RedditScraper):
    """Interactive mode for user input with smart parsing."""
    print("\n" + "=" * 70)
    print("REDDIT SCRAPER - INTERACTIVE MODE")
    print("=" * 70)
    print("\nWhat would you like to scrape?\n")
    print("  1. Post (by URL)")
    print("  2. Subreddit (by name)")
    print("  3. Search (by keyword/phrase)")
    print("  4. User (by username)")
    print("  5. Default (auto-detect)")
    print("  6. Exit")
    print()

    choice = input("Select option (1-6): ").strip()

    if choice == '1':
        url = input("\nEnter post URL: ").strip()
        if url:
            scraper.scrape_post(url)

    elif choice == '2':
        subreddit = input("\nEnter subreddit name (e.g., python, r/python, or /r/python): ").strip()
        if subreddit:
            limit = input("Number of posts (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            category = input("Category [hot/top/new/rising/controversial] (default hot): ").strip() or "hot"

            scrape_comments = input("Scrape comments for each post? [y/N] (Warning: takes longer): ").strip().lower()
            scrape_comments = scrape_comments == 'y'

            scraper.scrape_subreddit(subreddit, limit=limit, category=category,
                                     scrape_comments=scrape_comments)

    elif choice == '3':
        query = input("\nEnter search query (supports phrases in quotes): ").strip()
        if query:
            limit = input("Number of results (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            scrape_comments = input("Scrape comments for each post? [y/N] (Warning: takes longer): ").strip().lower()
            scrape_comments = scrape_comments == 'y'

            scraper.search_reddit(query, limit=limit, scrape_comments=scrape_comments)

    elif choice == '4':
        username = input("\nEnter username (e.g., username, u/username, or /u/username): ").strip()
        if username:
            limit = input("Number of items (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            scrape_post_comments = input(
                "Scrape comments on user's posts? [y/N] (Warning: takes much longer): ").strip().lower()
            scrape_post_comments = scrape_post_comments == 'y'

            scraper.scrape_user(username, limit=limit, scrape_post_comments=scrape_post_comments)

    elif choice == '5':
        print("\nDefault mode - auto-detects input type")
        print("Examples:")
        print("  - Subreddit: python, r/python, /r/python")
        print("  - User: u/username, /u/username")
        print("  - Search: machine learning, 'python tips'")
        user_input = input("\nEnter input (subreddit, user, or search phrase): ").strip()

        if user_input:
            result = reddit(user_input, verbose=True)
            if 'error' in result:
                scraper._log(f"✗ Error: {result['error']}")

    elif choice == '6':
        print("Exiting...")
        sys.exit(0)

    else:
        print("Invalid option")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Unified Reddit Scraper - Posts, Comments, and Metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_unified.py

  DEFAULT MODE (auto-detect, 10 items, comments included):
  python reddit_unified.py --default r/python
  python reddit_unified.py --default u/username
  python reddit_unified.py --default "machine learning tips"

  SPECIFIC MODES:
  python reddit_unified.py --type post --url "https://reddit.com/r/python/comments/..."
  python reddit_unified.py --type subreddit --name python --limit 10 --scrape-comments
  python reddit_unified.py --type search --query "machine learning" --limit 10 --scrape-comments
  python reddit_unified.py --type user --username example_user --limit 10

Note:
  - Default mode: automatically detects input and scrapes 10 items with comments
  - Subreddit names: python, r/python, /r/python, https://reddit.com/r/python all work
  - Usernames: username, u/username, /u/username all work
  - Search queries support phrases: "machine learning tips"
        """
    )

    parser.add_argument('--default',
                        help='Default mode - auto-detect and scrape 10 items (e.g., r/python, u/username, "search phrase")')
    parser.add_argument('--type', choices=['post', 'subreddit', 'search', 'user'],
                        help='Type of content to scrape')
    parser.add_argument('--url', help='Post URL (for post mode)')
    parser.add_argument('--name', help='Subreddit name (for subreddit mode)')
    parser.add_argument('--query', help='Search query (for search mode)')
    parser.add_argument('--username', help='Username (for user mode)')
    parser.add_argument('--limit', type=int, default=25, help='Number of items to fetch')
    parser.add_argument('--category', default='hot',
                        choices=['hot', 'top', 'new', 'rising', 'controversial'],
                        help='Post category (for subreddit mode)')
    parser.add_argument('--time-filter', default='week',
                        choices=['all', 'year', 'month', 'week', 'day', 'hour'],
                        help='Time filter (for top/controversial)')
    parser.add_argument('--scrape-comments', action='store_true',
                        help='Scrape comments for each post (takes longer)')
    parser.add_argument('--no-verbose', action='store_true', help='Disable verbose output')

    args = parser.parse_args()

    scraper = RedditScraper(verbose=not args.no_verbose)

    # Default mode
    if args.default:
        reddit(
            args.default,
            limit=10,
            scrape_comments=True,
            verbose=not args.no_verbose
        )
        return

    # If no arguments, run interactive mode
    if not args.type:
        interactive_mode(scraper)
        return

    # Run specific scraping mode
    try:
        if args.type == 'post':
            if not args.url:
                print("Error: --url required for post mode")
                sys.exit(1)
            scraper.scrape_post(args.url)

        elif args.type == 'subreddit':
            if not args.name:
                print("Error: --name required for subreddit mode")
                sys.exit(1)
            scraper.scrape_subreddit(args.name, limit=args.limit,
                                     category=args.category,
                                     time_filter=args.time_filter,
                                     scrape_comments=args.scrape_comments)

        elif args.type == 'search':
            if not args.query:
                print("Error: --query required for search mode")
                sys.exit(1)
            scraper.search_reddit(args.query, limit=args.limit,
                                  scrape_comments=args.scrape_comments)

        elif args.type == 'user':
            if not args.username:
                print("Error: --username required for user mode")
                sys.exit(1)
            scraper.scrape_user(args.username, limit=args.limit,
                                scrape_post_comments=args.scrape_comments)

    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# docs of this file
''' As an Importable Module from Other Scripts

# other_script.py
from reddit_unified import reddit

# Simple default mode (auto-detect)
result = reddit("r/python")
result = reddit("u/username")
result = reddit("machine learning tips")

# With specific options
result = reddit(
    "r/python",
    mode="subreddit",
    limit=20,
    category="top",
    time_filter="month",
    scrape_comments=True,
    verbose=True,
    save=True
)

# Access the data
print(result['type'])           # 'subreddit', 'user', 'search', 'post'
print(result['stats'])          # Statistics
print(result['posts'])          # List of posts
print(result['comments'])       # Comments (if applicable)
print(result['subreddit_info']) # Subreddit info (if subreddit mode)
print(result['user_info'])      # User info (if user mode)

# Handle errors
if 'error' in result:
    print(f"Error: {result['error']}")
else:
    print(f"Success! Scraped {result['stats']['posts_extracted']} posts")

# ----------------------
### Function Signature:


reddit(
    user_input: str,                    # Required: r/python, u/user, search term, or URL
    mode: Optional[str] = None,         # Optional: auto-detect if None
    limit: int = 10,                    # Default: 10 items
    category: str = "hot",              # For subreddit: hot/top/new/rising
    time_filter: str = "week",          # For top/controversial: week/month/year etc
    scrape_comments: bool = True,       # Whether to fetch comments
    verbose: bool = True,               # Print progress
    save: bool = True,                  # Save to file
) -> Dict

# ----------------------
### Return Value Structure:

{
    'type': 'subreddit',        # subreddit, user, search, or post
    'posts': [...],             # List of posts
    'comments': [...],          # Comments (if user mode)
    'subreddit_info': {...},    # Info (if subreddit mode)
    'user_info': {...},         # Info (if user mode)
    'query': 'search term',     # Query (if search mode)
    'stats': {...},             # Statistics
    'scraped_at': '2024...',    # Timestamp
    'error': 'message'          # If error occurred
}

# ----------------------
### Example Usage Scripts:

# example_1.py - Simple usage
from reddit_unified import reddit

result = reddit("r/python")
print(f"Found {result['stats']['posts_extracted']} posts")
for post in result['posts'][:3]:
    print(f"- {post['title']}")
    
# ----------------------
# example_2.py - Advanced usage
from reddit_unified import reddit
import json

# Search with specific options
result = reddit(
    "AI machine learning",
    mode="search",
    limit=5,
    scrape_comments=True,
    verbose=False  # Silent mode
)

# Process results
if 'error' not in result:
    for post in result['posts']:
        print(f"Title: {post['title']}")
        print(f"Score: {post['score']}")
        print(f"Comments: {len(post['comments'])}")
        print("---")
        
# ---------------------- 

# example_3.py - Batch processing
from reddit_unified import reddit

subreddits = ["python", "learnprogramming", "webdev"]

for sub in subreddits:
    print(f"\nScraping r/{sub}...")
    result = reddit(f"r/{sub}", limit=5, scrape_comments=False)
    
    if 'error' not in result:
        print(f"✓ {result['stats']['posts_extracted']} posts")
    else:
        print(f"✗ Error: {result['error']}")
'''
