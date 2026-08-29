from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin, urlparse
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET

import requests


# Public GitHub Pages address for this repository
PUBLIC_BASE_URL = (
    "https://jshanley-RCC.github.io/"
    "met-eireann-farming-feed"
)

DOCS_DIR = Path("docs")
ARCHIVE_DIR = DOCS_DIR / "archive"
IMAGE_DIR = DOCS_DIR / "images"
FEED_FILE = DOCS_DIR / "feed.xml"

for directory in [DOCS_DIR, ARCHIVE_DIR, IMAGE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


FEEDS = [
    {
        "name": "Roscommon County forecast",
        "url": "https://www.met.ie/Open_Data/xml/county_forecast.xml",
        "format": "xml",
        "filter_text": "Roscommon",
    },
    {
        "name": "Connacht regional forecast",
        "url": "https://www.met.ie/Open_Data/xml/xConnacht.xml",
        "format": "xml",
        "filter_text": None,
    },
    {
        "name": "Three-day forecast",
        "url": "https://www.met.ie/Open_Data/xml/web-3Dayforecast.xml",
        "format": "xml",
        "filter_text": None,
    },
    {
        "name": "Agricultural Data Report",
        "url": "https://prodapi.metweb.ie/agriculture/report",
        "format": "json",
        "filter_text": None,
    },
]


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def local_name(tag):
    return tag.split("}", 1)[-1].lower()


def cdata(value):
    """
    Safely place HTML/text inside an RSS CDATA block.
    """
    value = str(value or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{value}]]>"


def safe_filename(value):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value)


def extract_xml_text(root, filter_text=None):
    """
    Extract readable text from an XML response.

    For the county forecast, this attempts to retain text associated
    with Roscommon. If the XML structure does not identify county
    sections clearly, it falls back to all meaningful text.
    """
    all_lines = []

    for element in root.iter():
        if element.text:
            text = clean_text(element.text)

            if len(text) >= 15 and text not in all_lines:
                all_lines.append(text)

    if filter_text:
        matching_lines = [
            line for line in all_lines
            if filter_text.lower() in line.lower()
        ]

        if matching_lines:
            return "\n\n".join(matching_lines)

    return "\n\n".join(all_lines)


def extract_json_text(data):
    """
    Convert JSON into readable text for the RSS description.
    """
    return json.dumps(data, indent=2, ensure_ascii=False)


def find_image_urls(content_text, source_url):
    """
    Find image references in XML, JSON, HTML-like text, or plain text.
    """
    candidates = set()

    # Absolute image URLs
    absolute_pattern = (
        r"""https?://[^"'<>\\\s]+?\.(?:png|jpg|jpeg|gif|webp)"""
        r"""(?:\?[^"'<>\\\s]*)?"""
    )

    for match in re.findall(absolute_pattern, content_text, re.IGNORECASE):
        candidates.add(match.rstrip(".,);"))

    # Relative image URLs such as /images/map.png or map.png
    relative_pattern = (
        r"""(?:"|')([^"'<>]+?\.(?:png|jpg|jpeg|gif|webp)"""
        r"""(?:\?[^"'<>]*)?)(?:"|')"""
    )

    for match in re.findall(relative_pattern, content_text, re.IGNORECASE):
        candidates.add(urljoin(source_url, match))

    # Also look for common image attributes
    attribute_pattern = (
        r"""(?:src|href|image|imageUrl|image_url)\s*[:=]\s*"""
        r"""["']([^"']+)["']"""
    )

    for match in re.findall(attribute_pattern, content_text, re.IGNORECASE):
        if re.search(r"\.(png|jpg|jpeg|gif|webp)(\?|$)", match, re.IGNORECASE):
            candidates.add(urljoin(source_url, match))

    return sorted(candidates)


def image_extension(image_url, response):
    content_type = response.headers.get("Content-Type", "").lower()

    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "gif" in content_type:
        return ".gif"
    if "webp" in content_type:
        return ".webp"

    suffix = Path(urlparse(image_url).path).suffix.lower()

    if suffix in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
        return suffix

    return ".png"


def download_image(image_url):
    """
    Download an image once and return its public GitHub Pages URL.
    """
    response = requests.get(
        image_url,
        timeout=60,
        headers={"User-Agent": "MetEireannRSSArchive/1.0"},
    )
    response.raise_for_status()

    image_hash = hashlib.sha256(response.content).hexdigest()[:20]
    extension = image_extension(image_url, response)

    filename = f"{image_hash}{extension}"
    local_file = IMAGE_DIR / filename

    if not local_file.exists():
        local_file.write_bytes(response.content)
        print(f"Downloaded image: {image_url}")

    return f"{PUBLIC_BASE_URL}/images/{filename}"


def read_existing_items():
    if not FEED_FILE.exists():
        return []

    try:
        root = ET.parse(FEED_FILE).getroot()
        channel = next(
            element for element in root.iter()
            if local_name(element.tag) == "channel"
        )

        items = []

        for item in channel:
            if local_name(item.tag) == "item":
                items.append(ET.tostring(item, encoding="unicode"))

        return items[:100]

    except Exception as error:
        print(f"Could not read existing RSS feed: {error}")
        return []


def make_rss_item(item_id, title, description, source_url, image_urls):
    now = format_datetime(datetime.now(timezone.utc), usegmt=True)

    description_html = (
        f"<p>{html.escape(description).replace(chr(10), '<br>')}</p>"
    )

    for image_url in image_urls:
        description_html += (
            f'<p><img src="{html.escape(image_url, quote=True)}" '
            f'alt="{html.escape(title, quote=True)}" '
            f'style="max-width:100%;"></p>'
        )

    return f"""    <item>
      <title>{cdata(title)}</title>
      <description>{cdata(description_html)}</description>
      <link>{html.escape(source_url, quote=True)}</link>
      <guid isPermaLink="false">{html.escape(item_id, quote=True)}</guid>
      <pubDate>{now}</pubDate>
    </item>"""


existing_items = []
new_items = []

try:
    existing_items = read_existing_items()

    for feed in FEEDS:
        name = feed["name"]
        source_url = feed["url"]

        print(f"Downloading: {name}")

        response = requests.get(
            source_url,
            timeout=60,
            headers={"User-Agent": "MetEireannRSSArchive/1.0"},
        )
        response.raise_for_status()

        content = response.content
        content_hash = hashlib.sha256(content).hexdigest()
        item_id = f"{name}-{content_hash}"

        extension = ".json" if feed["format"] == "json" else ".xml"
        archive_name = (
            f"{safe_filename(name.lower())}_"
            f"{content_hash[:20]}{extension}"
        )
        archive_file = ARCHIVE_DIR / archive_name

        if not archive_file.exists():
            archive_file.write_bytes(content)
            print(f"Archived: {archive_file}")

        # Check whether this exact version is already in the RSS feed.
        already_exists = any(item_id in item for item in existing_items)

        if already_exists:
            print(f"No new version for: {name}")
            continue

        if feed["format"] == "xml":
            root = ET.fromstring(content)
            description = extract_xml_text(
                root,
                filter_text=feed.get("filter_text"),
            )
            searchable_text = content.decode("utf-8", errors="replace")

        else:
            data = response.json()
            description = extract_json_text(data)
            searchable_text = response.text

        source_images = find_image_urls(
            searchable_text,
            source_url,
        )

        archived_images = []

        for image_url in source_images:
            try:
                archived_url = download_image(image_url)
                archived_images.append(archived_url)
            except Exception as error:
                print(f"Could not download image {image_url}: {error}")

        title = (
            f"{name} — "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

        new_items.append(
            make_rss_item(
                item_id=item_id,
                title=title,
                description=description,
                source_url=source_url,
                image_urls=archived_images,
            )
        )

    # New entries appear first; retain the latest 100.
    all_items = (new_items + existing_items)[:100]

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Met Éireann Forecasts and Agricultural Data</title>
    <link>https://www.met.ie/</link>
    <description>Archived Met Éireann forecasts and agricultural data</description>
    <language>en-ie</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc), usegmt=True)}</lastBuildDate>
{chr(10).join(all_items)}
  </channel>
</rss>
"""

    FEED_FILE.write_text(rss, encoding="utf-8")

    print(f"RSS feed written to: {FEED_FILE}")
    print(f"New RSS items: {len(new_items)}")

except Exception as error:
    print(f"ERROR: {error}")
    raise
