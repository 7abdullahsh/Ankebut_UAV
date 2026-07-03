"""
ANKEBUT IHA - Drone Video Simulator (Faz 2 Gun 2)
Webcam'i MPEG-TS over UDP olarak yer istasyonuna yayinlar.
Pipeline: ksvideosrc -> videoconvert -> x264enc -> mpegtsmux -> udpsink
"""
import os
import sys
import subprocess

# simulator/config.py'i bulabilmek icin
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def build_pipeline() -> str:
    """Low-latency MPEG-TS pipeline."""
    return (
        "gst-launch-1.0 "
        "ksvideosrc ! "
        "videoconvert ! "
        "queue max-size-buffers=1 leaky=downstream ! "
        "x264enc "
        "tune=zerolatency "
        "speed-preset=ultrafast "
        f"bitrate={config.VIDEO_BITRATE_KBPS} "
        f"key-int-max={config.VIDEO_KEY_INT_MAX} ! "
        "queue max-size-buffers=1 leaky=downstream ! "
        "mpegtsmux alignment=7 ! "
        f"udpsink host={config.TARGET_HOST} port={config.VIDEO_PORT} "
        "sync=false buffer-size=65535"
    )


def kill_tree(pid: int) -> None:
    """Windows: shell=True ile baslayan cmd.exe ve onun cocugu gst-launch'i birlikte oldur."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def main() -> int:
    cmd = build_pipeline()

    print("=" * 60)
    print("ANKEBUT Drone Video Simulator")
    print(f"  Hedef       : udp://{config.TARGET_HOST}:{config.VIDEO_PORT}")
    print(f"  Bitrate     : {config.VIDEO_BITRATE_KBPS} kbps")
    print(f"  Key-int-max : {config.VIDEO_KEY_INT_MAX} kare")
    print(f"  Format      : MPEG-TS / H.264")
    print("=" * 60)
    print("Yayin basliyor... (Ctrl+C ile durdur)\n")

    proc = None
    try:
        proc = subprocess.Popen(cmd, shell=True)
        proc.wait()
        return proc.returncode or 0
    except KeyboardInterrupt:
        print("\n[!] Durduruluyor...")
        if proc and proc.poll() is None:
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        print("[+] Kapatildi.")
        return 0
    except Exception as e:
        print(f"[!] Hata: {e}")
        if proc and proc.poll() is None:
            kill_tree(proc.pid)
        return 1


if __name__ == "__main__":
    sys.exit(main())