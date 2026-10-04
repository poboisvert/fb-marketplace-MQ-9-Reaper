import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MarketplaceListing:
    id: str
    title: str
    price: str
    location: str
    image_url: str
    seller_name: str
    posted_date: str
    url: str
    is_pending: bool
    slug: str = ""
    previous_price: str = ""
    price_changed: bool = False
    original_price: str = ""
    price_dropped: bool = False
    created_at: str = ""
    updated_at: str = ""
    query: str = ""


@dataclass
class MarketplaceListingDetail(MarketplaceListing):
    description: str = ""
    images: list[str] = field(default_factory=list)
    condition: str = ""
    seller_profile_url: str = ""


def listing_image_url(chunk: str) -> str:
    """Nearest listing photo URI in a search-card fragment."""
    matches = re.findall(r'"uri":"((?:\\.|[^"\\])*)"', chunk)
    for raw in reversed(matches):
        url = decode_js(raw).replace("\\/", "/")
        if url.startswith("https://") and (
            "fbcdn" in url or "scontent" in url or re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", url)
        ):
            return url
    return ""


def decode_js(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", repl, value).replace('\\"', '"')
    # Facebook emits emoji as UTF-16 surrogate pairs. Join them so the text can be saved as UTF-8.
    return decoded.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def decode_html_entities(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .replace("&#39;", "'")
    )


def parse_search_html(
    html: str,
    limit: int,
    category: str | None = None,
) -> tuple[list[MarketplaceListing], bool]:
    """Read embedded Marketplace search cards.

    Mileage subtitles are optional and never required. Returns listings and
    whether more cards were present beyond ``limit``.
    """
    marker = '"marketplace_listing_title":"'
    listings: list[MarketplaceListing] = []
    seen: set[str] = set()
    idx = 0
    extra = False

    while True:
        idx = html.find(marker, idx)
        if idx < 0:
            break

        before = html[max(0, idx - 4000) : idx]
        next_title = html.find(marker, idx + len(marker))
        after_end = next_title if next_title > idx else idx + 8000
        after = html[idx:after_end]
        end = html.find('"', idx + len(marker))
        title = decode_js(html[idx + len(marker) : end]) if end > idx else ""

        story_keys = re.findall(r'"story_key":"(\d+)"', before)
        story = story_keys[-1] if story_keys else ""
        prices = re.findall(r'"formatted_amount":"([^"]+)"', before)
        price = decode_js(prices[-1]) if prices else "N/A"
        cities = re.findall(r'"display_name":"((?:\\.|[^"\\])*)"', before)
        city = decode_js(cities[-1]) if cities else "Unknown"
        categories = re.findall(r'"marketplace_listing_category_id":"([^"]+)"', before)
        listing_category = categories[-1] if categories else ""
        seller_match = re.search(
            r'"marketplace_listing_seller":\{"__typename":"User","name":"((?:\\.|[^"\\])*)"',
            after,
        )
        seller = decode_js(seller_match.group(1)) if seller_match else "Unknown"
        pending_flags = re.findall(r'"is_pending":(true|false)', before)
        is_pending = pending_flags[-1] == "true" if pending_flags else False
        created = re.findall(r'"creation_time":(\d+)', before)
        posted = ""
        if created:
            posted = datetime.fromtimestamp(int(created[-1]), tz=timezone.utc).isoformat()

        idx += len(marker)
        if category and listing_category != category:
            continue
        if not story or story in seen:
            continue
        if len(listings) >= limit:
            extra = True
            break

        seen.add(story)
        listings.append(
            MarketplaceListing(
                id=story,
                title=title,
                price=price,
                location=city,
                image_url=listing_image_url(before) or listing_image_url(after),
                seller_name=seller,
                posted_date=posted,
                url=f"https://www.facebook.com/marketplace/item/{story}/",
                is_pending=is_pending,
            )
        )

    return listings, extra


def parse_listing_detail_from_page(html: str, listing_id: str) -> MarketplaceListingDetail:
    detail = MarketplaceListingDetail(
        id=listing_id,
        title="",
        price="",
        location="",
        image_url="",
        seller_name="",
        posted_date="",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        is_pending=False,
    )

    title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
    if title_match:
        detail.title = decode_html_entities(title_match.group(1))

    desc_match = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', html)
    if desc_match:
        detail.description = decode_html_entities(desc_match.group(1))

    image_match = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', html)
    if image_match:
        detail.image_url = decode_html_entities(image_match.group(1))
        detail.images.append(detail.image_url)

    price_match = (
        re.search(r'"formatted_amount"\s*:\s*"([^"]+)"', html)
        or re.search(r'"price"\s*:\s*"([^"]+)"', html)
        or re.search(r'"amount"\s*:\s*"([^"]+)"', html)
    )
    if price_match:
        detail.price = price_match.group(1)

    for img_match in re.finditer(r'marketplace_listing_photos.*?"uri"\s*:\s*"([^"]+)"', html):
        url = img_match.group(1).replace("\\/", "/")
        if url not in detail.images:
            detail.images.append(url)

    seller_match = re.search(
        r'"marketplace_listing_seller"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
        html,
    )
    if seller_match:
        detail.seller_name = seller_match.group(1)

    condition_match = re.search(r'"condition_text"\s*:\s*"([^"]+)"', html) or re.search(
        r'"condition"\s*:\s*"([^"]+)"', html
    )
    if condition_match:
        detail.condition = condition_match.group(1)

    location_match = re.search(
        r'"location_text"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html
    ) or re.search(r'"reverse_geocode_city"\s*:\s*"([^"]+)"', html)
    if location_match:
        detail.location = location_match.group(1)

    return detail
