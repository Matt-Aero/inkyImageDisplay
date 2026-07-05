# NASA Image of the Day Inky Display

NASA Image of the Day display for a Raspberry Pi connected to a Pimoroni Inky e-ink display.

The script:

- fetches recent NASA Image of the Day feed entries
- displays each image full-screen on the Inky display
- asks OpenAI for a short caption from the image description
- advances every 20 minutes
- advances early when button A is pressed
- streams image downloads to a temp file before resizing for the display
- shuffles the feed and cycles through every entry before reshuffling

## Raspberry Pi Setup

Create and activate a virtual environment:

```bash
python3 -m venv --system-site-packages ~/.virtualenvs/pimoroni
source ~/.virtualenvs/pimoroni/bin/activate
pip install --upgrade pip
pip install inky gpiozero
```

Enable the interfaces required by Inky:

```bash
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0
```

If Inky reports that GPIO8 is already claimed by SPI chip select, add this to `/boot/firmware/config.txt` and reboot:

```bash
dtoverlay=spi0-0cs
```

## Run

```bash
source ~/.virtualenvs/pimoroni/bin/activate
python ~/inky_image_display.py
```

## Start on Boot

Copy the script and service file onto the Pi:

```bash
scp inky_image_display.py misterlamp@192.168.87.214:~/inky_image_display.py
scp systemd/inky-image-display.service misterlamp@192.168.87.214:~/inky-image-display.service
```

Then SSH into the Pi and install the service:

```bash
mkdir -p ~/.config
chmod 700 ~/.config
printf 'REFRESH_SECONDS=1200\nINKY_BUTTON_GPIO_PINS=5\nOPENAI_API_KEY=your_api_key_here\n' > ~/.config/inky-image-display.env
chmod 600 ~/.config/inky-image-display.env

sudo mv ~/inky-image-display.service /etc/systemd/system/inky-image-display.service
sudo systemctl daemon-reload
sudo systemctl enable --now inky-image-display.service
```

Check the service:

```bash
systemctl status inky-image-display.service
journalctl -u inky-image-display.service -f
```

Optional environment variables:

- `NASA_IMAGE_OF_DAY_FEED`: NASA Image of the Day RSS feed URL
- `REFRESH_SECONDS`: image rotation interval, defaults to `1200`
- `INKY_BUTTON_GPIO_PINS`: comma-separated BCM GPIO pins that advance the image, defaults to `5`
- `OPENAI_API_KEY`: API key used to generate captions from the feed description
- `OPENAI_MODEL`: OpenAI model used for captions, defaults to `gpt-4.1-mini`
- `MAX_ATTEMPTS`: failed image downloads before sleeping, defaults to `10`
