#!/usr/bin/env python3
import io
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps
from inky.auto import auto

NASA_APP_NAME = os.environ.get("NASA_APP_NAME", "raspberrypi-image-frame")
NASA_IMAGE_OF_DAY_FEED = os.environ.get(
    "NASA_IMAGE_OF_DAY_FEED",
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
)
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "30"))
MAX_ATTEMPTS = 10
BUTTON_GPIO_PINS = os.environ.get(
    "INKY_BUTTON_GPIO_PINS",
    os.environ.get("INKY_BUTTON_A_GPIO", "5"),
)
CAPTION_MAX_CHARS = int(os.environ.get("CAPTION_MAX_CHARS", "80"))

advance_requested = threading.Event()

display = auto()
WIDTH, HEIGHT = display.resolution


def make_request(url: str):
    req = Request(
        url,
        headers={
            "Accept": "application/rss+xml,application/xml,text/xml,image/jpeg,image/png,*/*",
            "User-Agent": NASA_APP_NAME,
        },
    )
    return urlopen(req, timeout=60)


def fetch_text(url: str) -> str:
    with make_request(url) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace")


def fetch_image(url: str) -> Image.Image:
    with make_request(url) as response:
        data = io.BytesIO(response.read())

    img = Image.open(data)
    img.draft("RGB", (WIDTH * 2, HEIGHT * 2))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((WIDTH, HEIGHT), Image.LANCZOS)
    return img.convert("RGB")


def first_text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    return (child.text or "").strip() if child is not None else ""


def format_feed_date(value: str) -> str:
    if not value:
        return "unknown date"
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return value[:16]


def image_url_from_item(item: ET.Element) -> str | None:
    thumbnail_url = None
    fallback_url = None

    for element in item.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        url = element.attrib.get("url")
        if not url:
            continue
        if tag == "thumbnail":
            thumbnail_url = url
        elif re.search(r"\.(jpe?g|png)(\?|$)", url, re.IGNORECASE):
            fallback_url = fallback_url or url

    if thumbnail_url:
        return thumbnail_url

    for enclosure in item.findall("enclosure"):
        url = enclosure.attrib.get("url")
        mime_type = enclosure.attrib.get("type", "")
        if url and mime_type.startswith("image/"):
            return url

    if fallback_url:
        return fallback_url

    html = first_text(item, "description")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def fetch_recent_images():
    print(f"NASA Image of the Day feed: {NASA_IMAGE_OF_DAY_FEED}")
    root = ET.fromstring(fetch_text(NASA_IMAGE_OF_DAY_FEED))
    images = []

    for item in root.findall(".//item"):
        image_url = image_url_from_item(item)
        if not image_url:
            continue

        images.append(
            {
                "date": format_feed_date(first_text(item, "pubDate")),
                "title": first_text(item, "title") or "NASA Image of the Day",
                "image_url": image_url,
            }
        )

    if not images:
        raise RuntimeError("NASA returned no Image of the Day entries with image URLs.")

    return images


def fit_image_contain(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    x = (target_w - img.width) // 2
    y = (target_h - img.height) // 2
    canvas.paste(img, (x, y))
    return canvas


def load_caption_font(size: int):
    for font_path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def add_caption(img: Image.Image, apod) -> Image.Image:
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    font_size = max(12, min(18, WIDTH // 48))
    font = load_caption_font(font_size)
    padding = max(8, WIDTH // 80)
    max_text_width = WIDTH - (padding * 2)

    date_text = str(apod["date"])
    title = " ".join(apod.get("title", "NASA Image of the Day").split())
    if len(title) > CAPTION_MAX_CHARS:
        title = title[: CAPTION_MAX_CHARS - 3].rstrip(". ,;:") + "..."
    caption = f"{date_text} - {title}"

    while text_size(draw, caption, font)[0] > max_text_width and len(caption) > len(date_text) + 6:
        caption = caption[:-4].rstrip(". ,;:-") + "..."

    text_height = text_size(draw, "Ag", font)[1]
    box_height = text_height + (padding * 2)
    x0 = 0
    y0 = max(0, HEIGHT - box_height)
    x1 = WIDTH - 1
    y1 = HEIGHT - 1

    draw.rectangle((x0, y0, x1, y1), fill=(0, 0, 0))
    draw.line((x0, y0, x1, y0), fill=(255, 255, 255), width=1)

    text_y = y0 + padding
    draw.text((x0 + padding, text_y), caption, fill=(255, 255, 255), font=font)

    return img


def parse_button_pins() -> list[int]:
    pins = []
    for raw_pin in BUTTON_GPIO_PINS.split(","):
        raw_pin = raw_pin.strip()
        if not raw_pin:
            continue
        pins.append(int(raw_pin))
    return pins


def setup_buttons():
    try:
        from gpiozero import Button
    except Exception as exc:
        print(f"Buttons disabled: {exc}")
        return []

    buttons = []
    enabled_pins = []
    for pin in parse_button_pins():
        try:
            button = Button(pin, pull_up=True, bounce_time=0.08)
            button.when_pressed = lambda pressed_pin=pin: request_advance(pressed_pin)
            buttons.append(button)
            enabled_pins.append(pin)
        except Exception as exc:
            print(f"Button on BCM GPIO {pin} disabled: {exc}")

    if enabled_pins:
        print(f"Buttons enabled on BCM GPIO pins: {', '.join(str(pin) for pin in enabled_pins)}")
    else:
        print("No buttons enabled.")

    return buttons


def request_advance(pin: int) -> None:
    print(f"Button press detected on BCM GPIO {pin}")
    advance_requested.set()


def wait_for_next_refresh() -> None:
    deadline = time.monotonic() + REFRESH_SECONDS
    advance_requested.clear()

    while time.monotonic() < deadline:
        if advance_requested.is_set():
            return
        time.sleep(0.05)


def display_nasa_image(nasa_image):
    print(f"Fetching: {nasa_image['date']} - {nasa_image['title']}")
    img = fetch_image(nasa_image["image_url"])
    img = fit_image_contain(img, WIDTH, HEIGHT)
    img = add_caption(img, nasa_image)

    display.set_image(img.convert("RGB"))
    display.show()
    print(f"Displayed: {nasa_image['date']} - {nasa_image['title']}")


def render_with_retries(images, index: int) -> int:
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        nasa_image = images[index % len(images)]
        try:
            display_nasa_image(nasa_image)
            return (index + 1) % len(images)
        except (HTTPError, URLError, OSError) as exc:
            last_error = exc
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            index = (index + 1) % len(images)
            time.sleep(1)

    raise RuntimeError(f"Could not fetch a valid NASA Image of the Day image: {last_error}")


def main():
    buttons = setup_buttons()
    images = fetch_recent_images()
    index = 0

    while True:
        try:
            index = render_with_retries(images, index)
            wait_for_next_refresh()

            if index == 0:
                images = fetch_recent_images()
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
