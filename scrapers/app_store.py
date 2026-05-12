"""
app_store_2.py - Advanced Apple App Store Scraper (iTunes API)
With granular control over reviews per rating category.

Usage:
    python app_store_2.py -u "Instagram"
    python app_store_2.py -u "Instagram" --reviews-per-rating 30
    python app_store_2.py -u "Instagram" --r1 20 --r2 20 --r3 20 --r4 30 --r5 50
    python app_store_2.py -u "Instagram" --custom-distribution 10,15,20,30,40

    from app_store_2 import app_store
    result = app_store("Instagram", reviews_distribution={'1': 20, '2': 20, '3': 20, '4': 30, '5': 50})
"""

import os
import sys
import json
import argparse
import re
import time
import logging
from typing import Optional, List, Dict, Union
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Try requests, if not available use urllib
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    import urllib.request

HF_TOKEN = os.environ.get("HF_TOKEN", "")


class AppStoreAPIClient:
    """iTunes API client for App Store data extraction."""

    BASE_URL = "https://itunes.apple.com"
    SEARCH_URL = f"{BASE_URL}/search"
    LOOKUP_URL = f"{BASE_URL}/lookup"

    COUNTRIES = {
        "in": "India",
        "us": "United States",
        "uk": "United Kingdom",
        "ca": "Canada",
        "au": "Australia",
    }

    def __init__(self, country: str = "in"):
        self.country = country.lower()
        self.session = None
        if REQUESTS_AVAILABLE:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

    def _make_request(self, url: str, params: Dict) -> Dict:
        """Make HTTP request."""
        try:
            if REQUESTS_AVAILABLE and self.session:
                resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            else:
                import urllib.request
                full_url = f"{url}?{urlencode(params)}"
                with urllib.request.urlopen(full_url, timeout=15) as r:
                    return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {}

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for apps by name."""
        logger.info(f"Searching: {query}")

        params = {
            'term': query,
            'country': self.country,
            'entity': 'software',
            'limit': limit,
            'media': 'software',
            'sort': 'relevance'
        }

        data = self._make_request(self.SEARCH_URL, params)
        results = []

        for item in data.get('results', []):
            results.append({
                'trackId': item.get('trackId'),
                'trackName': item.get('trackName'),
                'artistName': item.get('artistName'),
                'averageUserRating': item.get('averageUserRating', 0),
                'userRatingCount': item.get('userRatingCount', 0),
                'formattedPrice': item.get('formattedPrice'),
                'primaryGenreName': item.get('primaryGenreName'),
            })

        logger.info(f"Found {len(results)} results")
        return results

    def get_app_details(self, app_id: Union[str, int]) -> Optional[Dict]:
        """Get comprehensive app details."""
        logger.info(f"Fetching details for app ID: {app_id}")

        params = {
            'id': str(app_id),
            'country': self.country,
            'entity': 'software'
        }

        data = self._make_request(self.LOOKUP_URL, params)
        results = data.get('results', [])

        if not results:
            logger.error("No app found")
            return None

        app = results[0]

        # Extract all available fields
        details = {
            # Basic Info
            'trackId': app.get('trackId'),
            'trackName': app.get('trackName'),
            'bundleId': app.get('bundleId'),
            'trackViewUrl': app.get('trackViewUrl'),

            # Developer
            'artistId': app.get('artistId'),
            'artistName': app.get('artistName'),
            'artistViewUrl': app.get('artistViewUrl'),
            'sellerName': app.get('sellerName'),

            # Categories
            'primaryGenreName': app.get('primaryGenreName'),
            'primaryGenreId': app.get('primaryGenreId'),
            'genres': app.get('genres', []),
            'genreIds': app.get('genreIds', []),

            # Ratings
            'averageUserRating': float(app.get('averageUserRating', 0)),
            'userRatingCount': int(app.get('userRatingCount', 0)),
            'averageUserRatingForCurrentVersion': float(app.get('averageUserRatingForCurrentVersion', 0)),
            'userRatingCountForCurrentVersion': int(app.get('userRatingCountForCurrentVersion', 0)),

            # Pricing
            'price': float(app.get('price', 0)),
            'formattedPrice': app.get('formattedPrice'),
            'currency': app.get('currency', 'USD'),
            'isVppDeviceBasedLicensingEnabled': app.get('isVppDeviceBasedLicensingEnabled', False),

            # Version
            'version': app.get('version'),
            'releaseNotes': app.get('releaseNotes', ''),
            'releaseDate': app.get('releaseDate'),
            'currentVersionReleaseDate': app.get('currentVersionReleaseDate'),

            # Content
            'description': app.get('description', '')[:1000],
            'shortDescription': app.get('shortDescription', '')[:300],
            'minimumOsVersion': app.get('minimumOsVersion'),
            'fileSizeBytes': int(app.get('fileSizeBytes', 0)),
            'contentAdvisoryRating': app.get('contentAdvisoryRating'),

            # Media
            'artworkUrl60': app.get('artworkUrl60'),
            'artworkUrl100': app.get('artworkUrl100'),
            'artworkUrl512': app.get('artworkUrl512'),
            'screenshotUrls': app.get('screenshotUrls', []),
            'ipadScreenshotUrls': app.get('ipadScreenshotUrls', []),
            # 'appletvScreenshotUrls': app.get('appletvScreenshotUrls', []),

            # Support
            # 'supportedDevices': app.get('supportedDevices', []),
            # 'advisoryRating': app.get('advisoryRating'),
            # 'isGameCenterEnabled': app.get('isGameCenterEnabled', False),
            'kind': app.get('kind'),
            'trackCensoredName': app.get('trackCensoredName'),

            # URLs
            'privacyViewUrl': app.get('privacyViewUrl'),
            'supportUrl': app.get('supportUrl'),
            'websiteUrl': app.get('websiteUrl'),
        }

        logger.info(f"Extracted {len([v for v in details.values() if v])} fields")
        return details

    def get_reviews_by_rating(
            self,
            app_id: Union[str, int],
            reviews_distribution: Dict[str, int]
    ) -> List[Dict]:
        """
        Fetch reviews with granular control per rating category.

        Args:
            app_id: App ID
            reviews_distribution: Dict with rating as key (1-5) and count as value
                Example: {'1': 20, '2': 20, '3': 20, '4': 30, '5': 50}

        Returns:
            List of reviews
        """
        logger.info(f"Fetching reviews for app ID: {app_id}")
        logger.info(f"Distribution: {reviews_distribution}")

        all_reviews = []
        reviews_metadata = {}

        for rating in range(1, 6):
            rating_str = str(rating)
            count = reviews_distribution.get(rating_str, 0)

            if count == 0:
                logger.debug(f"Skipping {rating}★ reviews (count: 0)")
                reviews_metadata[rating_str] = {"requested": 0, "fetched": 0}
                continue

            logger.info(f"Fetching {count} reviews for {rating}★ rating")

            # Calculate pages needed (each page has ~10 reviews typically)
            pages_needed = max(1, (count // 10) + 1)
            fetched_count = 0

            for page in range(1, pages_needed + 1):
                try:
                    url = f"{self.BASE_URL}/{self.country}/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"

                    if REQUESTS_AVAILABLE:
                        resp = requests.get(url, timeout=15)
                        data = resp.json()
                    else:
                        import urllib.request
                        with urllib.request.urlopen(url, timeout=15) as r:
                            data = json.loads(r.read().decode('utf-8'))

                    entries = data.get('feed', {}).get('entry', [])

                    if not entries:
                        break

                    for entry in entries:
                        if 'im:rating' not in entry:
                            continue

                        entry_rating = int(entry.get('im:rating', {}).get('label', 0))

                        # Only add if matches the desired rating
                        if entry_rating == rating:
                            review = {
                                'id': entry.get('id', {}).get('label', ''),
                                'author': entry.get('author', {}).get('name', {}).get('label', 'Anonymous'),
                                'rating': entry_rating,
                                'title': entry.get('title', {}).get('label', ''),
                                'content': entry.get('content', {}).get('label', '')[:1000],
                                'date': entry.get('updated', {}).get('label', ''),
                                'version': entry.get('im:version', {}).get('label', 'Unknown'),
                            }
                            all_reviews.append(review)
                            fetched_count += 1

                            # Stop if we've reached the desired count
                            if fetched_count >= count:
                                break

                    if fetched_count >= count:
                        break

                    logger.debug(f"Page {page} ({rating}★): {fetched_count}/{count}")

                except Exception as e:
                    logger.warning(f"Page {page} ({rating}★) failed: {e}")
                    break

            reviews_metadata[rating_str] = {
                "requested": count,
                "fetched": fetched_count
            }

            logger.info(f"{rating}★: Fetched {fetched_count}/{count} reviews")

        logger.info(f"Total reviews fetched: {len(all_reviews)}")

        return all_reviews, reviews_metadata

    def analyze_reviews(self, reviews: List[Dict]) -> Dict:
        """Analyze reviews for statistics."""
        if not reviews:
            return {'error': 'No reviews'}

        ratings = [r.get('rating', 0) for r in reviews if r.get('rating')]

        return {
            'total_reviews': len(reviews),
            'average_rating': round(sum(ratings) / len(ratings), 2) if ratings else 0,
            'rating_distribution': {
                '1': len([r for r in ratings if r == 1]),
                '2': len([r for r in ratings if r == 2]),
                '3': len([r for r in ratings if r == 3]),
                '4': len([r for r in ratings if r == 4]),
                '5': len([r for r in ratings if r == 5]),
            },
            'latest_review_date': reviews[0].get('date') if reviews else None,
            'oldest_review_date': reviews[-1].get('date') if reviews else None,
        }


def app_store(
        input_str: Optional[str] = None,
        reviews_distribution: Optional[Dict[str, int]] = None,
        reviews: int = 100,
        analyze: bool = False,
        country: str = "in",
        output: Optional[str] = None,
        interactive: bool = True,
        verbose: bool = True
) -> Dict:
    """
    Main App Store scraper function with granular review control.

    Args:
        input_str: App name or ID
        reviews_distribution: Dict like {'1': 20, '2': 20, '3': 20, '4': 30, '5': 50}
                            If provided, overrides 'reviews' parameter
        reviews: Total reviews to fetch (distributed evenly if reviews_distribution not provided)
        analyze: Run HF analysis
        country: Country code
        output: Output directory
        interactive: Interactive mode
        verbose: Print progress

    Returns:
        Complete app data with analysis

    Example:
        # Even distribution
        result = app_store("Instagram", reviews=100)

        # Custom distribution
        result = app_store(
            "Instagram",
            reviews_distribution={'1': 20, '2': 20, '3': 20, '4': 30, '5': 50}
        )
    """

    if verbose:
        logger.info("=" * 70)
        logger.info("APP STORE ADVANCED SCRAPER (iTunes API)")
        logger.info("=" * 70)

    # Get input
    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("APP STORE SCRAPER")
        print("=" * 60)
        input_str = input("\nEnter app name or ID: ").strip()

    if not input_str:
        return {"error": "No input provided", "status": "failed"}

    extraction_start = datetime.now()

    # Initialize client
    client = AppStoreAPIClient(country=country)

    # Resolve app ID
    app_id = None
    search_results = []

    if input_str.isdigit():
        app_id = int(input_str)
    else:
        # Search
        search_results = client.search(input_str, limit=10)

        if search_results:
            app_id = search_results[0]['trackId']

            if interactive and len(search_results) > 1:
                print(f"\n[FOUND] {len(search_results)} results:")
                for i, r in enumerate(search_results[:5], 1):
                    print(f"  {i}. {r['trackName']} - {r['artistName']}")

                choice = input("\nSelect (1-5) or Enter for first: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(search_results):
                    app_id = search_results[int(choice) - 1]['trackId']

    if not app_id:
        return {"error": "Could not resolve app", "status": "failed"}

    # Fetch data
    if verbose:
        logger.info("Fetching app details...")

    app_details = client.get_app_details(app_id)
    if not app_details:
        return {"error": "Failed to fetch app details", "status": "failed"}

    # Setup review distribution
    if reviews_distribution is None:
        # Default: even distribution across all ratings
        reviews_per_rating = reviews // 5
        reviews_distribution = {
            '1': reviews_per_rating,
            '2': reviews_per_rating,
            '3': reviews_per_rating,
            '4': reviews_per_rating,
            '5': reviews - (reviews_per_rating * 4),  # Give remainder to 5-star
        }

    if verbose:
        logger.info(f"Review distribution: {reviews_distribution}")

    if verbose:
        logger.info(f"Fetching reviews with custom distribution...")

    app_reviews, reviews_metadata = client.get_reviews_by_rating(app_id, reviews_distribution)

    # Build result
    extraction_time = (datetime.now() - extraction_start).total_seconds()

    extracted_data = {
        'metadata': app_details,
        'reviews': app_reviews,
        'review_analysis': client.analyze_reviews(app_reviews),
        'reviews_metadata': reviews_metadata,  # Track requested vs fetched
    }

    result = {
        'extraction_metadata': {
            'source': 'Apple App Store (iTunes API)',
            'extracted_at': extraction_start.isoformat(),
            'extraction_time_seconds': round(extraction_time, 2),
            'fields_extracted': len([v for v in app_details.values() if v]),
            'country': country,
            'reviews_distribution': reviews_distribution,
            'total_reviews_extracted': len(app_reviews),
            'status': 'success',
        },
        'extracted_data': extracted_data,
        'analysis': None,
    }

    # Optional analysis
    if analyze and HF_TOKEN:
        if verbose:
            logger.info("Running HF analysis...")

        try:
            from analyzer import analyzer as run_analyzer

            analysis_result = run_analyzer(
                data=extracted_data,
                mode="detailed",
                platform="app_store"
            )
            result['analysis'] = analysis_result.get('analysis')
            result['analysis_status'] = analysis_result.get('status')

        except Exception as e:
            logger.warning(f"Analysis failed: {e}")

    # Save
    if output:
        os.makedirs(output, exist_ok=True)

        app_name = app_details.get('trackName', 'app')
        safe_name = re.sub(r'[^a-z0-9_]', '', app_name.lower())
        filepath = os.path.join(output, f"app_store_{safe_name}.json")

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        if verbose:
            logger.info(f"Saved: {filepath}")

    if verbose:
        logger.info("=" * 70)
        logger.info(f"✓ SUCCESS")
        logger.info(f"  App: {app_details.get('trackName')}")
        logger.info(f"  Rating: {app_details.get('averageUserRating')}/5")
        logger.info(f"  Total Reviews: {len(app_reviews)}")
        logger.info(f"  Distribution: {reviews_metadata}")
        logger.info(f"  Time: {extraction_time:.2f}s")
        logger.info("=" * 70)

    return result


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="App Store Advanced Scraper with Granular Review Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app_store_2.py -u "Instagram"
  python app_store_2.py -u "Instagram" --reviews 100
  python app_store_2.py -u "Instagram" --reviews-per-rating 20
  python app_store_2.py -u "Instagram" --r1 10 --r2 15 --r3 20 --r4 30 --r5 50
  python app_store_2.py -u "Instagram" --custom-distribution 10,15,20,30,40
  python app_store_2.py -u "Instagram" --analyze
  python app_store_2.py --bulk apps.txt --r1 20 --r2 20 --r3 20 --r4 30 --r5 50
        """
    )

    parser.add_argument("-u", "--url", help="App name or ID")
    parser.add_argument("--reviews", type=int, default=100, help="Total reviews to fetch (default 100)")
    parser.add_argument("--reviews-per-rating", type=int, help="Reviews per rating category (overrides --reviews)")
    parser.add_argument("--r1", type=int, help="Reviews for 1-star rating")
    parser.add_argument("--r2", type=int, help="Reviews for 2-star rating")
    parser.add_argument("--r3", type=int, help="Reviews for 3-star rating")
    parser.add_argument("--r4", type=int, help="Reviews for 4-star rating")
    parser.add_argument("--r5", type=int, help="Reviews for 5-star rating")
    parser.add_argument("--custom-distribution",
                        help="Distribution as comma-separated: r1,r2,r3,r4,r5 (e.g., 10,15,20,30,40)")
    parser.add_argument("--country", default="in", help="Country code (default in)")
    parser.add_argument("--analyze", action="store_true", help="Run analysis")
    parser.add_argument("--bulk", help="Bulk file")
    parser.add_argument("--output", default="data", help="Output directory")
    parser.add_argument("--no-interactive", action="store_true", help="Non-interactive")

    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Parse review distribution
    reviews_distribution = None

    if args.custom_distribution:
        try:
            parts = [int(x) for x in args.custom_distribution.split(',')]
            if len(parts) == 5:
                reviews_distribution = {
                    '1': parts[0],
                    '2': parts[1],
                    '3': parts[2],
                    '4': parts[3],
                    '5': parts[4],
                }
            else:
                logger.error("custom-distribution must have exactly 5 values")
        except ValueError:
            logger.error("Invalid custom-distribution format")

    elif args.r1 or args.r2 or args.r3 or args.r4 or args.r5:
        reviews_distribution = {
            '1': args.r1 or 0,
            '2': args.r2 or 0,
            '3': args.r3 or 0,
            '4': args.r4 or 0,
            '5': args.r5 or 0,
        }

    elif args.reviews_per_rating:
        reviews_per = args.reviews_per_rating
        reviews_distribution = {
            '1': reviews_per,
            '2': reviews_per,
            '3': reviews_per,
            '4': reviews_per,
            '5': reviews_per,
        }

    # Single app
    if args.url:
        app_store(
            input_str=args.url,
            reviews_distribution=reviews_distribution,
            reviews=args.reviews,
            analyze=args.analyze,
            country=args.country,
            output=args.output,
            interactive=False
        )
        return

    # Bulk
    if args.bulk:
        with open(args.bulk) as f:
            apps = [line.strip() for line in f if line.strip()]

        logger.info(f"Processing {len(apps)} apps...")
        for i, app_name in enumerate(apps, 1):
            logger.info(f"\n[{i}/{len(apps)}] {app_name}")
            try:
                app_store(
                    input_str=app_name,
                    reviews_distribution=reviews_distribution,
                    reviews=args.reviews,
                    analyze=args.analyze,
                    country=args.country,
                    output=args.output,
                    interactive=False,
                    verbose=False
                )
                logger.info("✓ Done")
            except Exception as e:
                logger.error(f"✗ Error: {e}")
        return

    # Interactive
    app_store(
        reviews_distribution=reviews_distribution,
        reviews=args.reviews,
        analyze=args.analyze,
        country=args.country,
        output=args.output,
        interactive=not args.no_interactive
    )


if __name__ == "__main__":
    main()


"""
Command Line

# Default: 20 reviews per rating (100 total)
python app_store_2.py -u "Instagram"

# Total 50 reviews (10 per rating)
python app_store_2.py -u "Instagram" --reviews 50

# Same count for each rating
python app_store_2.py -u "Instagram" --reviews-per-rating 25

# Individual control per rating
python app_store_2.py -u "Instagram" --r1 10 --r2 15 --r3 20 --r4 30 --r5 50

# Using custom distribution string
python app_store_2.py -u "Instagram" --custom-distribution 10,15,20,30,40

# Bulk with custom distribution
python app_store_2.py --bulk apps.txt --r1 20 --r2 20 --r3 20 --r4 30 --r5 50
"""

""" Programmatic

from app_store_2 import app_store

# Default distribution
result = app_store("Instagram")

# Custom distribution
result = app_store(
    input_str="Instagram",
    reviews_distribution={
        '1': 20,  # 1-star reviews
        '2': 20,  # 2-star reviews
        '3': 20,  # 3-star reviews
        '4': 30,  # 4-star reviews
        '5': 50,  # 5-star reviews
    },
    analyze=True
)

# Check what was actually fetched
print("Reviews fetched:")
for rating, meta in result['extracted_data']['reviews_metadata'].items():
    print(f"  {rating}★: {meta['fetched']}/{meta['requested']}")

print(f"Total reviews: {result['extraction_metadata']['total_reviews_extracted']}")
"""