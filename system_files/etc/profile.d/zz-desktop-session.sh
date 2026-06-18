#!/bin/bash

case "$-" in *i*) ;; *) return 0 ;; esac

[ "$(id -u)" -ne 0 ] || return 0
[ "$(tty 2>/dev/null)" = "/dev/tty1" ] || return 0

exec mango -s "vibepanel & awww-daemon & veilad &"
