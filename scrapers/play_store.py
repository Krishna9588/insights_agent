"""
play_store_2.py - Advanced Google Play Store Scraper
With granular control over reviews per rating category and comprehensive data extraction.

Installation:
    pip install google-play-scraper

Usage:
    python play_store_2.py -u "Instagram"
    python play_store_2.py -u com.instagram.android
    python play_store_2.py -u "Instagram" --reviews 100
"""

import os
import sys
import json
import argparse
import re
import logging
from typing import Optional, List, Dict, Union, Tuple
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Check dependencies
try:
    from google_play_scraper import app as gp_app, reviews as gp_reviews, search as gp_search, Sort
    SCRAPER_AVAILABLE = True
except ImportError:
    SCRAPER_AVAILABLE = False
    logger.error("google-play-scraper not installed. Run: pip install google-play-scraper")
    sys.exit(1)


class PlayStoreAPIClient:
    """Google Play Store API client for comprehensive data extraction."""

    COUNTRIES = {
        "in": "India",
        "us": "United States",
        "uk": "United Kingdom",
        "ca": "Canada",
        "au": "Australia",
        "de": "Germany",
        "fr": "France",
        "jp": "Japan",
    }

    LANGUAGES = ["en", "es", "fr", "de", "ja", "zh", "pt", "hi", "ar"]

    def __init__(self, country: str = "in", language: str = "en"):
        """Initialize Play Store client."""
        self.country = country.lower()
        self.language = language.lower()

    def _extract_package_from_url(self, url: str) -> Optional[str]:
        """Extract package name from Play Store URL."""
        try:
            # URL format: https://play.google.com/store/apps/details?id=com.package.name
            parsed = urlparse(url)
            if 'play.google.com' in parsed.netloc:
                qs = parse_qs(parsed.query)
                package = qs.get('id', [None])[0]
                if package:
                    logger.info(f"Extracted package from URL: {package}")
                    return package
        except Exception as e:
            logger.debug(f"Failed to extract package from URL: {e}")
        return None

    def _convert_datetime_to_string(self, obj):
        """Convert datetime objects to ISO format strings recursively."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._convert_datetime_to_string(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_datetime_to_string(item) for item in obj]
        return obj

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search for apps by name.

        Args:
            query: App name to search
            limit: Max results

        Returns:
            List of app results with extracted package names
        """
        logger.info(f"Searching: {query}")

        try:
            results = gp_search(query, lang=self.language, country=self.country, n_hits=limit)

            formatted_results = []
            for app in results:
                # Try to extract package ID from URL
                url = app.get('url', '')
                package_id = self._extract_package_from_url(url)

                if not package_id:
                    # Fallback: try direct field access
                    package_id = (
                        app.get('appId') or
                        app.get('app_id') or
                        app.get('packageName') or
                        app.get('id')
                    )

                if package_id:
                    formatted_results.append({
                        'appId': package_id,
                        'title': app.get('title'),
                        'developer': app.get('developer'),
                        'score': float(app.get('score', 0)) if app.get('score') else 0,
                        'ratings': int(app.get('ratings', 0)) if app.get('ratings') else 0,
                        'installs': app.get('installs'),
                        'icon': app.get('icon'),
                        'url': url,
                    })

            logger.info(f"Found {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            logger.error(f"Search failed: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_app_details(self, package_id: str) -> Optional[Dict]:
        """
        Extract comprehensive app metadata (50+ fields).

        Args:
            package_id: Package name

        Returns:
            Dictionary with all extracted metadata or None
        """
        logger.info(f"Extracting metadata for package: {package_id}")

        try:
            app_data = gp_app(package_id, lang=self.language, country=self.country)

            # Parse installs range
            installs_min = 0
            installs_max = 0
            if app_data.get('installs'):
                installs_str = app_data.get('installs', '0').replace('+', '').replace(',', '')
                try:
                    installs_min = int(installs_str)
                    installs_max = installs_min * 10
                except:
                    pass

            # Parse price
            price = 0.0
            if app_data.get('price'):
                try:
                    price = float(str(app_data.get('price', '0')).replace('$', '').replace(',', ''))
                except:
                    pass

            # Convert datetime fields to strings
            released = app_data.get('released')
            if isinstance(released, datetime):
                released = released.isoformat()

            updated = app_data.get('updated')
            if isinstance(updated, datetime):
                updated = updated.isoformat()

            # Extract all available fields
            metadata = {
                # Basic Information
                'appId': app_data.get('appId'),
                'title': app_data.get('title'),
                'url': app_data.get('url'),

                # Developer
                'developer': app_data.get('developer'),
                'developerId': app_data.get('developerId'),
                'developer_email': app_data.get('email'),
                'developer_website': app_data.get('websiteUrl'),
                'developer_address': app_data.get('address'),
                'developer_privacy_policy': app_data.get('privacyPolicy'),

                # Categories & Genre
                'category': app_data.get('category'),
                'genre': app_data.get('genre'),
                'genreId': app_data.get('genreId'),
                'content_rating': app_data.get('contentRating'),
                'content_rating_description': app_data.get('contentRatingDescription'),

                # Ratings & Reviews
                'score': float(app_data.get('score', 0)) if app_data.get('score') else 0,
                'ratings': int(app_data.get('ratings', 0)) if app_data.get('ratings') else 0,
                'reviews': int(app_data.get('reviews', 0)) if app_data.get('reviews') else 0,

                # Downloads & Users
                'installs': app_data.get('installs'),
                'installs_min': installs_min,
                'installs_max': installs_max,
                'free': app_data.get('free', True),

                # Pricing
                'price': price,
                'currency': app_data.get('currency'),
                'iap': app_data.get('iap', False),
                'iap_price_range': app_data.get('iapPrice'),

                # Version Information
                'version': app_data.get('version'),
                'version_update_date': updated,
                'min_android_version': app_data.get('androidVersion'),
                'target_android_version': app_data.get('androidVersionText'),

                # Description & Documentation
                'description': app_data.get('description', ''),
                'description_html': app_data.get('descriptionHTML', ''),
                'summary': (app_data.get('description', '')[:200] if app_data.get('description') else ''),

                # Media
                'icon': app_data.get('icon'),
                'screenshots': app_data.get('screenshots', []),
                'video': app_data.get('video'),
                'feature_graphic': app_data.get('featureGraphic'),

                # Permissions
                'permissions': app_data.get('permissions', []),
                'permissions_count': len(app_data.get('permissions', [])),

                # Risk Analysis
                'high_risk_permissions': self._analyze_permissions(app_data.get('permissions', [])),

                # Additional Information
                'released': released,
                'size': app_data.get('size'),
                'is_early_access': app_data.get('isEarlyAccess', False),
                'is_editor_choice': app_data.get('isEditorChoice', False),
                'offers_in_app_purchases': app_data.get('offersIAP', False),

                # Extraction metadata
                'extraction_country': self.country,
                'extraction_language': self.language,
                'extracted_at': datetime.now().isoformat(),
            }

            logger.info(f"Extracted {len([v for v in metadata.values() if v])} fields")
            return metadata

        except Exception as e:
            logger.error(f"Metadata extraction failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _analyze_permissions(self, permissions: List[str]) -> Dict:
        """Analyze permissions for privacy/security risks."""
        high_risk = [
            'LOCATION', 'CAMERA', 'MICROPHONE', 'CONTACTS',
            'CALENDAR', 'SMS', 'CALL_LOG', 'CLIPBOARD'
        ]

        medium_risk = [
            'PHONE', 'INTERNET', 'WIFI', 'BLUETOOTH'
        ]

        high_risk_perms = [p for p in permissions if any(x in p.upper() for x in high_risk)]
        medium_risk_perms = [p for p in permissions if any(x in p.upper() for x in medium_risk)]

        risk_level = "Low"
        if len(high_risk_perms) > 3:
            risk_level = "High"
        elif len(high_risk_perms) > 0 or len(medium_risk_perms) > 5:
            risk_level = "Medium"

        return {
            'risk_level': risk_level,
            'high_risk_count': len(high_risk_perms),
            'medium_risk_count': len(medium_risk_perms),
            'high_risk_permissions': high_risk_perms[:10],
            'medium_risk_permissions': medium_risk_perms[:10],
        }

    def get_reviews_by_rating(
        self,
        package_id: str,
        reviews_distribution: Dict[str, int]
    ) -> Tuple[List[Dict], Dict]:
        """Fetch reviews with granular control per rating category."""
        logger.info(f"Fetching reviews for package: {package_id}")
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

            fetched_count = 0

            try:
                result, _ = gp_reviews(
                    package_id,
                    lang=self.language,
                    country=self.country,
                    sort=Sort.MOST_RELEVANT,
                    count=count,
                    filter_score_with=rating,
                )

                for review in result:
                    fetched_count += 1

                    # Convert datetime to ISO string
                    review_date = review.get('at')
                    if isinstance(review_date, datetime):
                        review_date = review_date.isoformat()

                    reply_date = review.get('replyDate')
                    if isinstance(reply_date, datetime):
                        reply_date = reply_date.isoformat()

                    all_reviews.append({
                        'id': review.get('reviewId'),
                        'author': review.get('userName'),
                        'rating': int(review.get('score', 0)),
                        'title': review.get('reviewTitle', ''),
                        'content': review.get('content', '')[:1000],
                        'date': review_date,
                        'version': review.get('appVersion'),
                        'helpful_count': int(review.get('likeCount', 0)) if review.get('likeCount') else 0,
                        'reply_text': review.get('replyText'),
                        'reply_date': reply_date,
                    })

                logger.info(f"{rating}★: Fetched {fetched_count}/{count}")

            except Exception as e:
                logger.warning(f"Failed to fetch {rating}★ reviews: {e}")

            reviews_metadata[rating_str] = {
                "requested": count,
                "fetched": fetched_count
            }

        logger.info(f"Total reviews fetched: {len(all_reviews)}")
        return all_reviews, reviews_metadata

    def analyze_reviews(self, reviews: List[Dict]) -> Dict:
        """Analyze reviews for statistics."""
        if not reviews:
            return {'error': 'No reviews'}

        ratings = [r.get('rating', 0) for r in reviews if r.get('rating')]
        helpful_counts = [r.get('helpful_count', 0) for r in reviews if r.get('helpful_count')]

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
            'average_helpfulness': round(sum(helpful_counts) / len(helpful_counts), 2) if helpful_counts else 0,
            'reviews_with_replies': len([r for r in reviews if r.get('reply_text')]),
            'latest_review_date': reviews[0].get('date') if reviews else None,
            'oldest_review_date': reviews[-1].get('date') if reviews else None,
        }


def play_store(
    input_str: Optional[str] = None,
    reviews_distribution: Optional[Dict[str, int]] = None,
    reviews: int = 100,
    analyze: bool = False,
    country: str = "in",
    language: str = "en",
    output: Optional[str] = None,
    interactive: bool = True,
    verbose: bool = True
) -> Dict:
    """Main Play Store scraper function with granular review control."""

    if verbose:
        logger.info("="*70)
        logger.info("PLAY STORE ADVANCED SCRAPER")
        logger.info("="*70)

    # Get input
    if not input_str and interactive:
        print("\n" + "="*60)
        print("GOOGLE PLAY STORE SCRAPER")
        print("="*60)
        input_str = input("\nEnter app name or package ID: ").strip()

    if not input_str:
        return {"error": "No input provided", "status": "failed"}

    extraction_start = datetime.now()

    # Initialize client
    client = PlayStoreAPIClient(country=country, language=language)

    # Resolve package ID
    package_id = None
    search_results = []

    # Check if valid package format (e.g., com.instagram.android)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$", input_str):
        package_id = input_str
        logger.info(f"Using provided package ID: {package_id}")
    else:
        # Search by name
        if verbose:
            logger.info(f"Searching for: {input_str}")

        search_results = client.search(input_str, limit=10)

        if search_results:
            # Default to first result
            package_id = search_results[0]['appId']
            logger.info(f"Default selected: {search_results[0]['title']} ({package_id})")

            if interactive and len(search_results) > 1:
                print(f"\n[FOUND] {len(search_results)} results:")
                for i, r in enumerate(search_results[:5], 1):
                    print(f"  {i}. {r['title']} by {r['developer']} ({r['score']}/5)")

                choice = input("\nSelect (1-5) or press Enter for first: ").strip()
                if choice.isdigit():
                    choice_int = int(choice)
                    if 1 <= choice_int <= len(search_results):
                        selected_app = search_results[choice_int - 1]
                        package_id = selected_app['appId']
                        logger.info(f"User selected: {selected_app['title']} ({package_id})")
                        print(f"\n✓ Selected: {selected_app['title']}")

    if not package_id:
        error_msg = f"Could not resolve package ID from: {input_str}"
        logger.error(error_msg)
        if search_results:
            logger.error(f"Search results were: {json.dumps(search_results, indent=2)}")
        return {"error": error_msg, "status": "failed", "search_results": search_results}

    # Fetch data
    if verbose:
        logger.info(f"Fetching app details for: {package_id}")

    app_details = client.get_app_details(package_id)
    if not app_details:
        return {"error": f"Failed to fetch app details for: {package_id}", "status": "failed"}

    # Setup review distribution
    if reviews_distribution is None:
        reviews_per_rating = reviews // 5
        reviews_distribution = {
            '1': reviews_per_rating,
            '2': reviews_per_rating,
            '3': reviews_per_rating,
            '4': reviews_per_rating,
            '5': reviews - (reviews_per_rating * 4),
        }

    if verbose:
        logger.info(f"Review distribution: {reviews_distribution}")

    app_reviews, reviews_metadata = client.get_reviews_by_rating(package_id, reviews_distribution)

    # Build result
    extraction_time = (datetime.now() - extraction_start).total_seconds()

    extracted_data = {
        'metadata': app_details,
        'reviews': app_reviews,
        'review_analysis': client.analyze_reviews(app_reviews),
        'reviews_metadata': reviews_metadata,
    }

    result = {
        'extraction_metadata': {
            'source': 'Google Play Store',
            'extracted_at': extraction_start.isoformat(),
            'extraction_time_seconds': round(extraction_time, 2),
            'fields_extracted': len([v for v in app_details.values() if v]),
            'country': country,
            'language': language,
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
                platform="play_store"
            )
            result['analysis'] = analysis_result.get('analysis')
            result['analysis_status'] = analysis_result.get('status')

        except Exception as e:
            logger.warning(f"Analysis failed: {e}")

    # Save - Convert datetime objects to strings before saving
    if output:
        os.makedirs(output, exist_ok=True)

        app_name = app_details.get('title', 'app')
        safe_name = re.sub(r'[^a-z0-9_]', '', app_name.lower())
        filepath = os.path.join(output, f"play_store_{safe_name}.json")

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)

            if verbose:
                logger.info(f"✓ Saved: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save JSON: {e}")
            # Try with datetime conversion
            result = client._convert_datetime_to_string(result)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"✓ Saved (with datetime conversion): {filepath}")

    if verbose:
        logger.info("="*70)
        logger.info(f"✓ SUCCESS")
        logger.info(f"  App: {app_details.get('title')}")
        logger.info(f"  Rating: {app_details.get('score')}/5")
        logger.info(f"  Total Reviews: {len(app_reviews)}")
        logger.info(f"  Time: {extraction_time:.2f}s")
        logger.info("="*70)

    return result


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Play Store Advanced Scraper with Granular Review Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python play_store_2.py -u "Instagram"
  python play_store_2.py -u com.instagram.android
  python play_store_2.py -u "Instagram" --reviews 50
  python play_store_2.py -u "Instagram" --r1 10 --r2 15 --r3 20 --r4 30 --r5 50
        """
    )

    parser.add_argument("-u", "--url", help="App name or package ID")
    parser.add_argument("--reviews", type=int, default=100, help="Total reviews")
    parser.add_argument("--reviews-per-rating", type=int, help="Reviews per rating")
    parser.add_argument("--r1", type=int, help="1-star reviews")
    parser.add_argument("--r2", type=int, help="2-star reviews")
    parser.add_argument("--r3", type=int, help="3-star reviews")
    parser.add_argument("--r4", type=int, help="4-star reviews")
    parser.add_argument("--r5", type=int, help="5-star reviews")
    parser.add_argument("--custom-distribution", help="r1,r2,r3,r4,r5")
    parser.add_argument("--country", default="in", help="Country code")
    parser.add_argument("--language", default="en", help="Language code")
    parser.add_argument("--analyze", action="store_true", help="Run HF analysis")
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
                    '1': parts[0], '2': parts[1], '3': parts[2], '4': parts[3], '5': parts[4],
                }
        except ValueError:
            logger.error("Invalid custom-distribution format")

    elif args.r1 or args.r2 or args.r3 or args.r4 or args.r5:
        reviews_distribution = {
            '1': args.r1 or 0, '2': args.r2 or 0, '3': args.r3 or 0, '4': args.r4 or 0, '5': args.r5 or 0,
        }

    elif args.reviews_per_rating:
        reviews_per = args.reviews_per_rating
        reviews_distribution = {
            '1': reviews_per, '2': reviews_per, '3': reviews_per, '4': reviews_per, '5': reviews_per,
        }

    # Single app
    if args.url:
        result = play_store(
            input_str=args.url,
            reviews_distribution=reviews_distribution,
            reviews=args.reviews,
            analyze=args.analyze,
            country=args.country,
            language=args.language,
            output=args.output,
            interactive=False
        )

        if result.get('status') == 'failed':
            print(f"\n[ERROR] {result.get('error')}")
            sys.exit(1)
        return

    # Bulk
    if args.bulk:
        with open(args.bulk) as f:
            apps = [line.strip() for line in f if line.strip()]

        logger.info(f"Processing {len(apps)} apps...")
        for i, app_name in enumerate(apps, 1):
            logger.info(f"[{i}/{len(apps)}] {app_name}")
            try:
                result = play_store(
                    input_str=app_name,
                    reviews_distribution=reviews_distribution,
                    reviews=args.reviews,
                    analyze=args.analyze,
                    country=args.country,
                    language=args.language,
                    output=args.output,
                    interactive=False,
                    verbose=False
                )
                if result.get('status') != 'failed':
                    logger.info("✓ Done")
            except Exception as e:
                logger.error(f"✗ Error: {e}")
        return

    # Interactive
    result = play_store(
        reviews_distribution=reviews_distribution,
        reviews=args.reviews,
        analyze=args.analyze,
        country=args.country,
        language=args.language,
        output=args.output,
        interactive=not args.no_interactive
    )

    # Print results summary
    if result.get('status') != 'failed':
        print("\n" + "="*70)
        print("EXTRACTION SUMMARY")
        print("="*70)
        ed = result.get('extracted_data', {})
        meta = ed.get('metadata', {})
        review_analysis = ed.get('review_analysis', {})

        print(f"\nApp: {meta.get('title')}")
        print(f"Developer: {meta.get('developer')}")
        print(f"Package ID: {meta.get('appId')}")
        print(f"Rating: {meta.get('score')}/5 ({meta.get('ratings'):,} ratings)")
        print(f"Installs: {meta.get('installs')}")
        print(f"Size: {meta.get('size')}")
        print(f"Version: {meta.get('version')}")
        print(f"Category: {meta.get('category')}")

        print(f"\nReviews Extracted: {review_analysis.get('total_reviews')}")
        print(f"Average Rating: {review_analysis.get('average_rating')}/5")
        print(f"Distribution: {review_analysis.get('rating_distribution')}")

        app_title = meta.get('title', 'app').lower().replace(' ', '_')
        print(f"\nOutput: data/play_store_{app_title}.json")
        print("="*70)


if __name__ == "__main__":
    main()