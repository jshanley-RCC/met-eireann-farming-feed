from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin, urlparse
import hashlib
import html
import json
import mimetypes
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


PUBLIC_BASE_URL = (
    "https://jshanley-RCC.github.io/"
    "met-eireann-farming-feed"
)

DOCS_DIR = Path("docs")
ARCHIVE_DIR = DOCS_DIR / "archive"
IMAGE_DIR = DOCS_DIR / "images"
FEED_FILE = DOCS_DIR / "feed.xml"

for directory in (DOCS_DIR, ARCHIVE_DIR, IMAGE_DIR):
    directory.mkdir(parents=True, exist_ok=True)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; MetEireannFarmingRSS/1.0)"
    )
}


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
        "url": (
            "https://www.met.ie/forecasts/farming/"
            "agricultural-data-report"
        ),
        "format": "html",
    },
    {
        "name": "Latest Farming Commentary",
        "url": "https://www.met.ie/forecasts/farming",
        "format": "html",
    },
]


def clean_text(value):
    """Convert text to readable single-spaced text."""
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def local_name(tag):
    """Return an XML tag name without its namespace."""
    return tag.split("}", 1)[-1].lower()


def cdata(value):
    """Safely place HTML inside an RSS CDATA block."""
    value = str(value or "").replace(
        "]]>",
        "]]]]><![CDATA[>"
    )
    return f"<![CDATA[{value}]]>"


def escape_xml(value):
    return html.escape(str(value or ""), quote=True)


def safe_filename(value):
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value)
    return value.strip("-") or "file"


def make_absolute_url(value, page_url):
    value = html.unescape(str(value or "")).strip()

    if not value:
        return None

    if value.startswith(("data:", "javascript:", "#")):
        return None

    return urljoin(page_url, value)


def is_image_url(url):
    """
    Detect normal image URLs. This also accepts URLs without an
    extension because some websites use image-serving endpoints.
    """
    if not url:
        return False

    path = urlparse(url).path.lower()
    query = urlparse(url).query.lower()

    known_extension = re.search(
        r"\.(png|jpg|jpeg|gif|webp|svg)(?:$|[?#])",
        path,
        re.IGNORECASE,
    )

    image_parameter = any(
        term in query
        for term in (
            "format=png",
            "format=jpg",
            "format=jpeg",
            "format=webp",
            "fm=png",
            "fm=jpg",
            "fm=webp",
        )
    )

    image_word = any(
        term in url.lower()
        for term in (
            "image",
            "img",
            "chart",
            "graph",
            "map",
            "forecast",
            "rainfall",
            "temperature",
            "agricultur",
        )
    )

    return bool(known_extension or image_parameter or image_word)


def image_extension(image_url, response):
    content_type = response.headers.get("Content-Type", "").lower()

    extension_by_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }

    for mime_type, extension in extension_by_type.items():
        if mime_type in content_type:
            return extension

    suffix = Path(urlparse(image_url).path).suffix.lower()

    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(content_type.split(";")[0])

    if guessed:
        return guessed

    return ".png"


def download_image(image_url):
    """
    Download an external image into docs/images and return the
    public GitHub Pages URL.
    """
    response = requests.get(
        image_url,
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()

    if not content_type.startswith("image/"):
        raise ValueError(
            f"URL did not return an image: {content_type}"
        )

    image_hash = hashlib.sha256(response.content).hexdigest()[:20]
    extension = image_extension(image_url, response)
    filename = f"{image_hash}{extension}"
    local_file = IMAGE_DIR / filename

    if not local_file.exists():
        local_file.write_bytes(response.content)
        print(f"Downloaded image: {image_url}")
    else:
        print(f"Image already exists: {local_file}")

    return f"{PUBLIC_BASE_URL}/images/{filename}"


def extract_xml_html(root, filter_text=None):
    """
    Convert XML text into readable HTML.
    """
    lines = []

    for element in root.iter():
        text = clean_text(element.text)

        if len(text) < 2:
            continue

        if filter_text:
            # Keep Roscommon-related content where possible.
            parent_text = ""
            if element is not root:
                parent_text = clean_text(
                    "".join(element.itertext())
                )

            if (
                filter_text.lower() not in text.lower()
                and filter_text.lower() not in parent_text.lower()
            ):
                continue

        if text not in lines:
            lines.append(text)

    if not lines:
        return "<p>No forecast text was available.</p>"

    output = []

    for index, line in enumerate(lines):
        if index == 0:
            output.append(f"<h2>{html.escape(line)}</h2>")
        else:
            output.append(f"<p>{html.escape(line)}</p>")

    return "\n".join(output)


def extract_xml_images(root, source_url):
    """
    Extract image URLs from XML text and attributes.
    """
    candidates = set()

    for element in root.iter():
        for attribute_value in element.attrib.values():
            absolute = make_absolute_url(
                attribute_value,
                source_url,
            )

            if is_image_url(absolute):
                candidates.add(absolute)

        if element.text:
            matches = re.findall(
                r"""https?://[^"'<>\\\s]+""",
                element.text,
                re.IGNORECASE,
            )

            for match in matches:
                match = match.rstrip(".,);")
                if is_image_url(match):
                    candidates.add(match)

    return sorted(candidates)


def html_table_to_html(table):
    """
    Rebuild a webpage table as simple RSS-compatible HTML.
    """
    rows = []

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])

        if not cells:
            continue

        row = []

        for cell in cells:
            text = clean_text(cell.get_text(" ", strip=True))
            tag = "th" if cell.name.lower() == "th" else "td"
            row.append(
                f"<{tag}>{html.escape(text)}</{tag}>"
            )

        rows.append(f"<tr>{''.join(row)}</tr>")

    if not rows:
        return ""

    first_row_is_header = "<th>" in rows[0]

    if first_row_is_header:
        header = rows[0]
        body = rows[1:]
        return (
            '<table border="1" cellpadding="4" cellspacing="0">'
            f"<thead>{header}</thead>"
            f"<tbody>{''.join(body)}</tbody>"
            "</table>"
        )

    return (
        '<table border="1" cellpadding="4" cellspacing="0">'
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def extract_html_page(page_html, page_url, page_type):
    """
    Extract readable content, tables, and image URLs from a webpage.
    """
    soup = BeautifulSoup(page_html, "lxml")

    for unwanted in soup.find_all(
        ["script", "style", "noscript", "svg", "form"]
    ):
        unwanted.decompose()

    image_candidates = set()

    for image in soup.find_all("img"):
        for attribute in (
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
        ):
            value = image.get(attribute)
            absolute = make_absolute_url(value, page_url)

            if absolute and is_image_url(absolute):
                image_candidates.add(absolute)

        srcset = image.get("srcset", "")

        for entry in srcset.split(","):
            value = entry.strip().split(" ")[0]
            absolute = make_absolute_url(value, page_url)

            if absolute and is_image_url(absolute):
                image_candidates.add(absolute)

    for meta in soup.find_all("meta"):
        value = (
            meta.get("content")
            or meta.get("data-src")
            or meta.get("value")
        )

        absolute = make_absolute_url(value, page_url)

        if absolute and is_image_url(absolute):
            image_candidates.add(absolute)

    content = soup.find("main") or soup.find("article") or soup.body

    if content is None:
        return "<p>No webpage content was available.</p>", []

    output = []

    if page_type == "agricultural_report":
        heading = content.find(
            ["h1", "h2"],
            string=re.compile(
                "agricultural data report",
                re.IGNORECASE,
            ),
        )

        if heading:
            output.append(
                f"<h2>{html.escape(clean_text(heading.get_text()))}</h2>"
            )

        for table in content.find_all("table"):
            table_html = html_table_to_html(table)

            if table_html:
                output.append(table_html)

    else:
        for element in content.find_all(
            ["h1", "h2", "h3", "h4", "p", "ul", "ol", "table"]
        ):
            if element.name == "table":
                table_html = html_table_to_html(element)

                if table_html:
                    output.append(table_html)

            elif element.name in ("ul", "ol"):
                list_items = []

                for item in element.find_all("li", recursive=False):
                    text = clean_text(item.get_text(" ", strip=True))

                    if text:
                        list_items.append(
                            f"<li>{html.escape(text)}</li>"
                        )

                if list_items:
                    output.append(
                        f"<{element.name}>"
                        f"{''.join(list_items)}"
                        f"</{element.name}>"
                    )

            else:
                text = clean_text(element.get_text(" ", strip=True))

                if not text:
                    continue

                if element.name.startswith("h"):
                    output.append(
                        f"<{element.name}>{html.escape(text)}</{element.name}>"
                    )
                else:
                    output.append(
                        f"<p>{html.escape(text)}</p>"
                    )

    if not output:
        text = clean_text(content.get_text(" ", strip=True))

        if text:
            output.append(f"<p>{html.escape(text)}</p>")
        else:
            output.append("<p>No webpage content was available.</p>")

    return "\n".join(output), sorted(image_candidates)


def extract_json_html(data):
    """
    Display JSON in a readable, scrollable RSS section.
    """
    pretty_json = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
    )

    return (
        "<h2>Agricultural Data Report</h2>"
        "<pre style=\"white-space:pre-wrap;\">"
        f"{html.escape(pretty_json)}"
        "</pre>"
    )


def read_existing_items():
    if not FEED_FILE.exists():
        return []

    try:
        root = ET.parse(FEED_FILE).getroot()
        channel = next(
            element
            for element in root.iter()
            if local_name(element.tag) == "channel"
        )

        items = []

        for item in channel:
            if local_name(item.tag) == "item":
                items.append(
                    ET.tostring(item, encoding="unicode")
                )

        return items[:100]

    except Exception as error:
        print(f"Could not read existing RSS feed: {error}")
        return []


def make_rss_item(
    item_id,
    title,
    description_html,
    source_url,
    image_urls,
):
    now = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    image_html = []

    for image_url in image_urls:
        image_html.append(
            "<p>"
            f'<img src="{escape_xml(image_url)}" '
            f'alt="{escape_xml(title)}" '
            'style="max-width:100%;height:auto;">'
            "</p>"
        )

    full_description = description_html

    if image_html:
        full_description += (
            "<h3>Images</h3>"
            + "\n".join(image_html)
        )

    enclosures = []

    for image_url in image_urls:
        image_name = Path(urlparse(image_url).path).name
        extension = Path(image_name).suffix.lower()

        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
        }.get(extension, "image/png")

        enclosures.append(
            f'      <enclosure url="{escape_xml(image_url)}" '
            f'type="{mime_type}" length="0" />'
        )

    enclosure_text = "\n".join(enclosures)

    return f"""    <item>
      <title>{cdata(title)}</title>
      <description>{cdata(full_description)}</description>
      <link>{escape_xml(source_url)}</link>
      <guid isPermaLink="false">{escape_xml(item_id)}</guid>
      <pubDate>{now}</pubDate>
{enclosure_text}
    </item>"""


def fetch_url(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response


def archive_response(feed, content, content_hash):
    extension = ".xml"

    if feed["format"] == "json":
        extension = ".json"
    elif feed["format"] == "html":
        extension = ".html"

    archive_name = (
        f"{safe_filename(feed['name'].lower())}_"
        f"{content_hash[:20]}{extension}"
    )

    archive_file = ARCHIVE_DIR / archive_name

    if not archive_file.exists():
        archive_file.write_bytes(content)
        print(f"Archived: {archive_file}")


existing_items = []
new_items = []

try:
    existing_items = read_existing_items()

    for feed in FEEDS:
        name = feed["name"]
        source_url = feed["url"]

        print(f"Downloading: {name}")

        response = fetch_url(source_url)
        content = response.content
        content_hash = hashlib.sha256(content).hexdigest()
        item_id = f"{safe_filename(name)}-{content_hash}"

        archive_response(feed, content, content_hash)

        already_exists = any(
            item_id in item
            for item in existing_items
        )

        if already_exists:
            print(f"No new version for: {name}")
            continue

        image_urls = []
        description_html = ""

        if feed["format"] == "xml":
            root = ET.fromstring(content)

            description_html = extract_xml_html(
                root,
                filter_text=feed.get("filter_text"),
            )

            image_urls = extract_xml_images(
                root,
                source_url,
            )

        elif feed["format"] == "json":
            data = response.json()
            description_html = extract_json_html(data)

        elif feed["format"] == "html":
            page_type = "farming_page"

            if name == "Agricultural Data Report":
                page_type = "agricultural_report"

            description_html, image_urls = extract_html_page(
                content,
                source_url,
                page_type,
            )

        archived_images = []

        for image_url in image_urls:
            try:
                archived_url = download_image(image_url)
                archived_images.append(archived_url)
            except Exception as error:
                print(
                    f"Could not download image "
                    f"{image_url}: {error}"
                )

        title = (
            f"{name} — "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

        new_items.append(
            make_rss_item(
                item_id=item_id,
                title=title,
                description_html=description_html,
                source_url=source_url,
                image_urls=archived_images,
            )
        )

    all_items = (new_items + existing_items)[:100]

    build_time = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Met Éireann Forecasts and Agricultural Data</title>
    <link>{PUBLIC_BASE_URL}/</link>
    <description>
      Archived Met Éireann forecasts and agricultural data
    </description>
    <language>en-ie</language>
    <lastBuildDate>{build_time}</lastBuildDate>
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
