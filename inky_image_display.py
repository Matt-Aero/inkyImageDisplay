#!/usr/bin/env python3
import io
import json
import os
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont
from inky.auto import auto

NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
NASA_APP_NAME = os.environ.get("NASA_APP_NAME", "raspberrypi-apod-frame")
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "30"))
APOD_LOOKBACK_DAYS = int(os.environ.get("APOD_LOOKBACK_DAYS", "45"))
MAX_ATTEMPTS = 10
BUTTON_A_GPIO = int(os.environ.get("INKY_BUTTON_A_GPIO", "5"))
CAPTION_MAX_CHARS = int(os.environ.get("CAPTION_MAX_CHARS", "220"))

display = auto()
WIDTH, HEIGHT = display.resolution


def make_request(url: str):
    req = Request(
        url,
        headers={
            "Accept": "application/json,image/jpeg,image/png,*/*",
            "User-Agent": NASA_APP_NAME,
        },
    )
    return urlopen(req, timeout=60)


def fetch_json(url: str):
    with make_request(url) as response:
        return json.load(response)


def fetch_image(url: str) -> Image.Image:
    with make_request(url) as response:
        return Image.open(io.BytesIO(response.read())).convert("RGBA")


def fetch_recent_apods():
    end = date.today()
    start = end - timedelta(days=APOD_LOOKBACK_DAYS)
    params = {
        "api_key": NASA_API_KEY,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "thumbs": "true",
    }
    api_url = "https://api.nasa.gov/planetary/apod?" + urlencode(params)
    print(f"NASA APOD URL: {api_url.replace(NASA_API_KEY, '***')}")

    data = fetch_json(api_url)
    if isinstance(data, dict):
        data = [data]

    apods = []
    for item in data:
        if item.get("media_type") != "image":
            continue

        image_url = item.get("hdurl") or item.get("url")
        if not image_url:
            continue

        apods.append(
            {
                "date": item.get("date", "unknown date"),
                "title": item.get("title", "NASA Astronomy Picture of the Day"),
                "description": item.get("explanation", ""),
                "image_url": image_url,
            }
        )

    if not apods:
        raise RuntimeError("NASA returned no recent APOD image entries.")

    apods.sort(key=lambda item: item["date"], reverse=True)
    return apods


def fit_image_contain(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((target_w, target_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 255))
    x = (target_w - img.width) // 2
    y = (target_h - img.height) // 2
    canvas.paste(img, (x, y), img)
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


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines = []

    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if text_size(draw, candidate, font)[0] <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)

    return lines


def truncate_to_fit(lines: list[str], max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines

    visible = lines[:max_lines]
    visible[-1] = visible[-1].rstrip(". ") + "..."
    return visible


def add_caption(img: Image.Image, apod) -> Image.Image:
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)

    font_size = max(14, min(22, WIDTH // 38))
    font = load_caption_font(font_size)
    padding = max(10, WIDTH // 55)
    line_gap = max(4, font_size // 4)
    box_width = min(WIDTH - (padding * 2), max(WIDTH // 2, 360))
    max_text_width = box_width - (padding * 2)
    max_box_height = max(90, HEIGHT // 3)

    date_line = str(apod["date"])
    description = apod.get("description") or apod.get("title", "")
    description = " ".join(description.split())
    if len(description) > CAPTION_MAX_CHARS:
        description = description[: CAPTION_MAX_CHARS - 3].rstrip(". ,;:") + "..."

    lines = [date_line] + wrap_text(draw, description, font, max_text_width)
    line_height = text_size(draw, "Ag", font)[1] + line_gap
    max_lines = max(2, (max_box_height - (padding * 2)) // line_height)
    lines = truncate_to_fit(lines, max_lines)

    text_height = (len(lines) * line_height) - line_gap
    box_height = text_height + (padding * 2)
    x0 = padding
    y0 = HEIGHT - box_height - padding
    x1 = x0 + box_width
    y1 = y0 + box_height

    draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 255, 235))
    draw.rectangle((x0, y0, x1, y1), outline=(0, 0, 0, 255), width=2)

    text_y = y0 + padding
    for line in lines:
        draw.text((x0 + padding, text_y), line, fill=(0, 0, 0, 255), font=font)
        text_y += line_height

    return img


def setup_button_a():
    try:
        from gpiozero import Button

        button = Button(BUTTON_A_GPIO, pull_up=True, bounce_time=0.05)
        print(f"Button A enabled on BCM GPIO {BUTTON_A_GPIO}")
        return button
    except Exception as exc:
        print(f"Button A disabled: {exc}")
        return None


def button_pressed(button) -> bool:
    if button is None:
        return False
    return bool(button.is_pressed)


def wait_for_next_refresh(button) -> None:
    deadline = time.monotonic() + REFRESH_SECONDS
    was_pressed = False

    while time.monotonic() < deadline:
        pressed = button_pressed(button)
        if pressed and not was_pressed:
            return
        was_pressed = pressed
        time.sleep(0.05)


def display_apod(apod):
    print(f"Fetching: {apod['date']} - {apod['title']}")
    img = fetch_image(apod["image_url"])
    img = fit_image_contain(img, WIDTH, HEIGHT)
    img = add_caption(img, apod)

    display.set_image(img)
    display.show()
    print(f"Displayed: {apod['date']} - {apod['title']}")


def render_with_retries(apods, index: int) -> int:
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        apod = apods[index % len(apods)]
        try:
            display_apod(apod)
            return (index + 1) % len(apods)
        except (HTTPError, URLError, OSError) as exc:
            last_error = exc
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            index = (index + 1) % len(apods)
            time.sleep(1)

    raise RuntimeError(f"Could not fetch a valid APOD image: {last_error}")


def main():
    button_a = setup_button_a()
    apods = fetch_recent_apods()
    index = 0

    while True:
        try:
            index = render_with_retries(apods, index)
            wait_for_next_refresh(button_a)

            if index == 0:
                apods = fetch_recent_apods()
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
