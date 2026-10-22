"""Linux platform layer for the EZWipe boot USB.

Drive access goes through standard tools shipped on the live image
(lsblk, hdparm, nvme-cli, smartctl, rtcwake) so every command sent to a
drive is a plain command line that can be logged, reviewed and repeated.
"""
