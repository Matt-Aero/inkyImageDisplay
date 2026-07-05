#!/usr/bin/env python3
import gc
import html
import json
import os
import random
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps
from inky.auto import auto

NASA_FEED = os.environ.get(
    "NASA_IMAGE_OF_DAY_FEED",
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
)
APP_NAME = os.environ.get("NASA_APP_NAME", "raspberrypi-image-frame")
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "1200"))
BUTTON_GPIO_PINS = os.environ.get("INKY_BUTTON_GPIO_PINS", "5")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "10"))

advance_requested = threading.Event()
display = auto()
WIDTH, HEIGHT = display.resolution
Image.MAX_IMAGE_PIXELS = None


def log(message):
    print(message, flush=True)


def get_url(url):
    return urlopen(
        Request(url, headers={"User-Agent": APP_NAME}),
        timeout=60,
    )


def text_from(item, tag):
    child = item.find(tag)
    return (child.text or "").strip() if child is not None else ""


def clean_description(value):
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def feed_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        return value[:16] if value else "unknown date"


def image_url(item):
    for element in item.iter():
        url = element.attrib.get("url", "")
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if url and tag == "thumbnail":
            return url

    for enclosure in item.findall("enclosure"):
        url = enclosure.attrib.get("url", "")
        if url and enclosure.attrib.get("type", "").startswith("image/"):
            return url

    description = text_from(item, "description")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description, re.I)
    return match.group(1) if match else None


def fetch_feed_images():
    log(f"NASA feed: {NASA_FEED}")
    with get_url(NASA_FEED) as response:
        data = response.read()

    images = []
    root = ET.fromstring(data)
    for item in root.findall(".//item"):
        url = image_url(item)
        if not url:
            continue

        title = text_from(item, "title") or "NASA Image of the Day"
        description = clean_description(text_from(item, "description")) or title
        images.append(
            {
                "date": feed_date(text_from(item, "pubDate")),
                "title": title,
                "description": description,
                "url": url,
            }
        )

    if not images:
        raise RuntimeError("NASA feed had no usable images.")

    log(f"Feed images: {len(images)}")
    return images


def make_random_queue(images):
    indexes = list(range(len(images)))
    random.SystemRandom().shuffle(indexes)
    return indexes


def download_image(url):
    log(f"Image URL: {url}")
    with tempfile.TemporaryFile() as image_file:
        with get_url(url) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                image_file.write(chunk)

        image_file.seek(0)
        img = Image.open(image_file)
        img.draft("RGB", (WIDTH * 2, HEIGHT * 2))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((WIDTH, HEIGHT), Image.LANCZOS)
        return img.convert("RGB")


def fit_to_screen(img):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    canvas.paste(img, ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2))
    return canvas


def openai_caption(description):
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAPI_API_KEY")
    if not api_key:
        log("OpenAI caption skipped: OPENAI_API_KEY is not set")
        return None

    prompt = (
        "Write a vivid image caption. Use no more than 8 words. "
        "Return only the caption, with no quotes or punctuation-only lines.\n\n"
        f"Image description: {description}"
    )
    body = json.dumps(
        {
            "model": OPENAI_MODEL,
            "input": prompt,
            "max_output_tokens": 24,
            "temperature": 0.7,
        }
    ).encode("utf-8")

    request = Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": APP_NAME,
        },
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    caption = response_text(result).strip().strip('"') or None
    if caption:
        log(f"OpenAI caption: {caption}")
    return caption


def response_text(result):
    if result.get("output_text"):
        return result["output_text"]

    for item in result.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")

    return ""


def caption_for(image):
    try:
        caption = openai_caption(image["description"])
    except Exception as exc:
        log(f"OpenAI caption failed, using feed title: {exc}")
        caption = None

    if not caption:
        caption = image["title"]

    return caption


def caption_font():
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, max(10, min(15, WIDTH // 60)))
        except OSError:
            pass
    return ImageFont.load_default()


def add_caption(img, text):
    draw = ImageDraw.Draw(img)
    font = caption_font()
    padding = max(8, WIDTH // 80)
    text = f"{text}"

    while draw.textbbox((0, 0), text, font=font)[2] > WIDTH - padding * 2 and len(text) > 4:
        text = text[:-4].rstrip() + "..."

    text_box = draw.textbbox((0, 0), "Ag", font=font)
    text_height = text_box[3] - text_box[1]
    box_height = text_height + padding * 2
    draw.rectangle((0, HEIGHT - box_height, WIDTH, HEIGHT), fill=(0, 0, 0))
    draw.text((padding, HEIGHT - box_height + padding), text, fill=(255, 255, 255), font=font)
    return img


def display_image(image):
    log(f"Fetching: {image['date']} - {image['title']}")
    img = fit_to_screen(download_image(image["url"]))
    img = add_caption(img, caption_for(image))

    display.set_image(img)
    del img
    gc.collect()
    display.show()
    log(f"Displayed: {image['date']} - {image['title']}")


def setup_buttons():
    try:
        from gpiozero import Button
    except Exception as exc:
        log(f"Buttons disabled: {exc}")
        return []

    buttons = []
    for raw_pin in BUTTON_GPIO_PINS.split(","):
        raw_pin = raw_pin.strip()
        if not raw_pin:
            continue

        pin = int(raw_pin)
        button = Button(pin, pull_up=True, bounce_time=0.08)
        button.when_pressed = lambda pressed_pin=pin: request_advance(pressed_pin)
        buttons.append(button)

    if buttons:
        log(f"Buttons enabled on BCM GPIO pins: {BUTTON_GPIO_PINS}")
    return buttons


def request_advance(pin):
    log(f"Button press on BCM GPIO {pin}")
    advance_requested.set()


def wait_for_refresh():
    advance_requested.clear()
    deadline = time.monotonic() + REFRESH_SECONDS
    while time.monotonic() < deadline and not advance_requested.is_set():
        time.sleep(0.05)


def main():
    setup_buttons()
    images = fetch_feed_images()
    queue = make_random_queue(images)

    while True:
        try:
            if not queue:
                images = fetch_feed_images()
                queue = make_random_queue(images)

            last_error = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if not queue:
                    queue = make_random_queue(images)

                image = images[queue.pop(0)]
                try:
                    display_image(image)
                    break
                except (HTTPError, URLError, OSError, MemoryError) as exc:
                    last_error = exc
                    log(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
                    gc.collect()
                    time.sleep(1)
            else:
                raise RuntimeError(f"Could not display a NASA image: {last_error}")

            wait_for_refresh()
        except Exception as exc:
            log(f"Error: {exc}")
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
