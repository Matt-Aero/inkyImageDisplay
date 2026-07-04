# NASA Image of the Day Inky Display

NASA Image of the Day display for a Raspberry Pi connected to a Pimoroni Inky e-ink display.

The script:

- fetches recent NASA Image of the Day feed entries
- displays each image full-screen on the Inky display
- overlays the image date and title in a small single-line caption
- advances every 30 seconds
- advances early when button A is pressed

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
printf 'REFRESH_SECONDS=30\nINKY_BUTTON_GPIO_PINS=5\n' > ~/.config/inky-image-display.env
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
- `REFRESH_SECONDS`: image rotation interval, defaults to `30`
- `INKY_BUTTON_GPIO_PINS`: comma-separated BCM GPIO pins that advance the image, defaults to `5`
- `INKY_BUTTON_A_GPIO`: legacy single BCM GPIO pin setting, used only if `INKY_BUTTON_GPIO_PINS` is unset
- `CAPTION_MAX_CHARS`: maximum title characters in the caption, defaults to `80`
