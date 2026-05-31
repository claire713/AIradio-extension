#!/bin/bash
amixer set PCM 100%




if ! pgrep -x "librespot" > /dev/null; then
    echo "librespot not running. Starting it..."
        
    # Start librespot in the background
    sudo /usr/bin/librespot \
        --name "VradioPlayer" \
        --cache "/var/cache/raspotify" \
        --backend "alsa" \
        --initial-volume 80 \
        --quiet &

    # Optional: wait a bit to ensure librespot is initialized
    sleep 2
else
    echo "librespot is already running."
fi




cd /home/pi/vradio
/home/pi/vradio/myenv/bin/python /home/pi/vradio/main.py
