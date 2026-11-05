#!/bin/sh
# eth0-stable-watch.sh - keep eth0 locked to 100baseT-Full with EEE off.
#
# WHY: /etc/NetworkManager/dispatcher.d/50-eth0-stable only re-applies this fix on
# NetworkManager "up"/"hostname"/"dhcp4-change"/"connectivity-change" dispatcher
# events. On predator, repeated PHY autonegotiation flaps (a marginal gigabit link
# re-training itself) were NOT triggering any of those NetworkManager events, so
# eth0 kept resetting to full-gigabit/EEE-on after every flap and dropping again a
# few minutes later. This watcher listens directly to kernel link-state changes
# (independent of NetworkManager) and re-applies the fix within about a second of
# every link-up, so the restriction can never silently "fall off" again.
#
# Deployed directly on predator via SSH (see sofar.txt Chapter 7); tracked here
# too so it survives an SD card re-image.

IFACE="eth0"

apply() {
  /usr/sbin/ethtool -s "$IFACE" autoneg on advertise 0x008 2>&1 | logger -t eth0-stable-watch
  /usr/sbin/ethtool --set-eee "$IFACE" eee off 2>&1 | logger -t eth0-stable-watch
  logger -t eth0-stable-watch "applied 100-Full/EEE-off (speed now: $(cat /sys/class/net/$IFACE/speed 2>/dev/null)Mb/s)"
}

# Apply once immediately (covers this service (re)starting while the link is already up).
apply

# Then re-apply on every subsequent link-up transition, forever.
ip monitor link 2>/dev/null | while read -r line; do
  case "$line" in
    *"$IFACE"*"state UP"*) apply ;;
  esac
done
