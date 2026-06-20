#!/bin/bash

case "$-" in *i*) ;; *) return 0 ;; esac

[ "$(id -u)" -ne 0 ] || return 0
[ "$(tty 2>/dev/null)" = "/dev/tty1" ] || return 0

export MANGOWM_CONFIG="${MANGOWM_CONFIG:-/etc/zerith/mango.conf}"

exec mango -c "$MANGOWM_CONFIG" -s "vibepanel & awww-daemon & veilad &" \
    >>"$HOME/.local/share/desktop-session.log" 2>&1

clear
