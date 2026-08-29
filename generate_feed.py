from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import html
import re
import xml.etree.ElementTree as ET

import requests


SOURCE_URL = "https://www.met.ie/Open_Data/xml/fcom.xml"
DOCS_DIR = Path("docs")
ARCHIVE_DIR = DOCS_DIR / "archive"
FEED_FILE = DOCS_DIR / "feed.xml"

DOCS_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)


def local_name(tag):
    """Remove an XML namespace from a tag name."""
    return tag.split("}", 1)[-1].lower()


def clean_text(value):
    """Convert whitespace and HTML entities into readable text."""
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_issue_time(root):
    """Find an issued/publication timestamp if one exists."""
    possible_names = {
        "issued",
        "issuedtime",
        "issuetime",
        "publisheddate",
        "publicationdate",
        "updated",
        "lastupdated",
    }

    for element in root.iter():
        if local_name(element.tag) in possible_names and element.text:
            value = clean_text(element.text)
            if value:
                return value

    return datetime.now(timezone.utc).isoformat()


def extract_forecast_text(root):
    """
    Collect readable text from the XML.
    This deliberately keeps the extraction generic so it continues
    to work if the feed's internal element names change.
    """
    lines = []

    for element in root.iter():
        if element.text:
            text = clean_text(element.text)

            # Ignore very short technical values and duplicate text.
            if len(text) >= 20 and text not in lines:
                lines.append(text)

    return "\n\n".join(lines)


def rss_escape(value):
    return html.escape(value or "", quote=True)


def make_item(item_id, title, description, issued, link):
    now = format_datetime(datetime.now(timezone.utc), usegmt=True)

    return f"""    <item>
      <title>{rss_escape(title)}</title>
      <description>{rss_escape(description)}</description>
      <link>{rss_escape(link)}</link>
      <guid isPermaLink="false">{rss_escape(item_id)}</guid>
      <pubDate>{now}</pubDate>
    </item>"""


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

        return items[:20]

    except Exception:
        return []


response = requests.get(
    SOURCE_URL,
    timeout=60,
    headers={"User-Agent": "MetEireannRSSArchive/1.0"}
)
response.raise_for_status()

xml_data = response.content
root = ET.fromstring(xml_data)

issued = extract_issue_time(root)
forecast_text = extract_forecast_text(root)

content_hash = hashlib.sha256(xml_data).hexdigest()
item_id = f"met-eireann-farming-{content_hash}"

archive_file = ARCHIVE_DIR / f"{content_hash}.xml"

# Preserve each distinct response.
if not archive_file.exists():
    archive_file.write_bytes(xml_data)

existing_items = read_existing_items()

# Do not add the same forecast twice.
if not any(item_id in item for item in existing_items):
    title = f"Met Éireann farming forecast — issued {issued}"

    description = (
        f"<p><strong>Issued:</strong> {html.escape(issued)}</p>"
        f"<p>{html.escape(forecast_text).replace(chr(10), '<br>')}</p>"
    )

    item = make_item(
        item_id=item_id,
        title=title,
        description=description,
        issued=issued,
        link=SOURCE_URL,
    )

    existing_items.insert(0, item)

rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Met Éireann Farming Forecast</title>
    <link>{SOURCE_URL}</link>
    <description>Archived Met Éireann farming forecasts</description>
    <language>en-ie</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc), usegmt=True)}</lastBuildDate>
{chr(10).join(existing_items)}
  </channel>
</rss>
"""

FEED_FILE.write_text(rss, encoding="utf-8")
print(f"Feed updated: {FEED_FILE}")
print(f"Archive file: {archive_file}")
