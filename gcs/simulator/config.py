"""ANKEBUT Drone Simulatoru - Yapilandirma."""

# Yer istasyonu hedefi (Faz 2 loopback icin localhost)
TARGET_HOST = "192.168.1.124"

# Video portu (yer istasyonu config.py icindeki VIDEO_UDP_PORT ile ayni olmali)
VIDEO_PORT = 5600

# Video bitrate (kbps). Yuksek = kalite + gecikme, dusuk = bozuk gorunum
VIDEO_BITRATE_KBPS = 2000

# H.264 keyframe araligi (frame). Dusuk = bozulmadan hizli toparlanir
VIDEO_KEY_INT_MAX = 15
MAVLINK_PORT = 14550