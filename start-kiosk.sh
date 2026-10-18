#!/bin/bash
xset s off || true
xset s noblank || true
xset -dpms || true
unclutter -idle 2 >/dev/null 2>&1 &
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlr-randr --output HDMI-A-1 --transform 90 || true
until curl -fsS http://127.0.0.1:8000/ >/dev/null; do
  sleep 2
done
exec chromium --lang=da-DK --disable-translate --disable-features=Translate --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --password-store=basic --user-data-dir=/home/niller/.config/fuglestation-chromium --check-for-update-interval=31536000 http://127.0.0.1:8000/
