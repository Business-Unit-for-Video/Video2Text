import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

from faster_whisper import WhisperModel


SOURCE_URL = os.getenv("SOURCE_URL", "").strip()
DESTINATION_RAW = os.getenv("DESTINATION", "").strip()
PLATFORM = os.getenv("PLATFORM", "auto").strip().lower()
USE_COOKIES = str(os.getenv("USE_COOKIES", "true")).strip().lower() in {"1", "true", "yes", "on"}

YOUTUBE_COOKIES_FILE = Path(os.getenv("YOUTUBE_COOKIES_FILE", "youtube_cookies.txt"))
BILIBILI_COOKIES_FILE = Path(os.getenv("BILIBILI_COOKIES_FILE", "bilibili_cookies.txt"))

MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "zh")
INITIAL_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "").strip()

AUDIO_FORMAT = os.getenv("AUDIO_FORMAT", "mp3")
AUDIO_QUALITY = os.getenv("AUDIO_QUALITY", "7")
BEAM_SIZE = int(os.getenv("BEAM_SIZE", "1"))
VAD_FILTER = os.getenv("VAD_FILTER", "1") in {"1", "true", "True"}
TRANSCRIBE_CHUNK_SECONDS = int(os.getenv("TRANSCRIBE_CHUNK_SECONDS", "1800"))
MAX_TRAILING_GAP_SECONDS = float(os.getenv("MAX_TRAILING_GAP_SECONDS", "120"))
MAX_TRAILING_GAP_RATIO = float(os.getenv("MAX_TRAILING_GAP_RATIO", "0.10"))

GIT_BRANCH = os.getenv("GITHUB_REF_NAME", "").strip()


def log(msg: str):
    print(msg, flush=True)


def clean_url(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    if text.startswith("[") and "](" in text and text.endswith(")"):
        left = text.find("](")
        return text[left + 2:-1].strip()
    m = re.search(r'https?://[^\s]+', text)
    if m:
        return m.group(0).rstrip(")")
    return text


def sanitize_key(name: str, max_len: int = 80) -> str:
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._ ")
    return (name[:max_len] or "default")


DESTINATION = sanitize_key(DESTINATION_RAW or "default")
OUTPUT_ROOT_RAW = os.getenv("OUTPUT_ROOT", "").strip()
OUTPUT_ROOT = Path(OUTPUT_ROOT_RAW) if OUTPUT_ROOT_RAW else Path("single_videos")

STATE_DIR = Path("state_single_video") / DESTINATION
OUTPUT_DIR = OUTPUT_ROOT / DESTINATION
WITH_TS_DIR = OUTPUT_DIR / "with_timestamps"
PLAIN_DIR = OUTPUT_DIR / "plain"
TMP_DIR = Path("tmp_audio") / DESTINATION
PROGRESS_FILE = STATE_DIR / "progress.json"
META_FILE = OUTPUT_DIR / "_meta.json"


def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WITH_TS_DIR.mkdir(parents=True, exist_ok=True)
    PLAIN_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8"):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


def save_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run(cmd, capture=False, check=True):
    log("[cmd] " + " ".join(cmd))
    if capture:
        return subprocess.run(cmd, text=True, capture_output=True, check=check)
    return subprocess.run(cmd, text=True, check=check)


def git_run(cmd, check=True):
    log("[git] " + " ".join(cmd))
    return subprocess.run(cmd, text=True, check=check)


def save_progress(status: str, note: str = "", extra: Optional[Dict] = None):
    payload = {
        "status": status,
        "note": note,
        "source_url": clean_url(SOURCE_URL),
        "destination": DESTINATION,
        "platform": PLATFORM,
        "use_cookies": USE_COOKIES,
        "updated_at": int(time.time()),
        "updated_at_readable": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    if extra:
        payload.update(extra)
    save_json(PROGRESS_FILE, payload)


def detect_platform(url: str) -> str:
    url = clean_url(url)
    host = (urlsplit(url).netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "bilibili.com" in host or "b23.tv" in host:
        return "bilibili"
    raise ValueError(f"unsupported platform: {url}")


def get_platform() -> str:
    if PLATFORM in {"youtube", "bilibili"}:
        return PLATFORM
    return detect_platform(SOURCE_URL)


def seconds_to_mmss_mmm(sec: float) -> str:
    total_ms = max(0, int(round(sec * 1000)))
    total_seconds, ms = divmod(total_ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{ms:03d}"


def cookies_file_for(platform: str) -> Optional[Path]:
    if not USE_COOKIES:
        return None
    if platform == "youtube" and YOUTUBE_COOKIES_FILE.exists() and YOUTUBE_COOKIES_FILE.stat().st_size > 0:
        return YOUTUBE_COOKIES_FILE
    if platform == "bilibili" and BILIBILI_COOKIES_FILE.exists() and BILIBILI_COOKIES_FILE.stat().st_size > 0:
        return BILIBILI_COOKIES_FILE
    return None


def yt_dlp_base_cmd(platform: str):
    cmd = ["yt-dlp"]
    if platform == "youtube":
        cmd.extend(["--remote-components", "ejs:github"])
    cookie_path = cookies_file_for(platform)
    if cookie_path:
        cmd.extend(["--cookies", str(cookie_path)])
    return cmd


def fetch_video_info(url: str, platform: str) -> Dict:
    url = clean_url(url)
    cmd = yt_dlp_base_cmd(platform)
    cmd.extend(["--no-playlist", "--dump-single-json", url])
    res = run(cmd, capture=True)
    data = json.loads(res.stdout)

    video_id = data.get("id") or "NOID"
    title = (data.get("title") or video_id).strip()
    webpage_url = data.get("webpage_url") or url

    return {
        "id": video_id,
        "title": title,
        "url": webpage_url,
        "uploader": data.get("uploader", ""),
        "channel": data.get("channel", ""),
        "duration": data.get("duration"),
        "platform": platform,
    }


def build_output_basename(info: Dict) -> str:
    video_id = info.get("id") or "NOID"
    return f"{DESTINATION}_{video_id}"


def plain_output_path(info: Dict) -> Path:
    return PLAIN_DIR / f"{build_output_basename(info)}.txt"


def ts_output_path(info: Dict) -> Path:
    return WITH_TS_DIR / f"{build_output_basename(info)}.txt"


def meta_output_path() -> Path:
    return META_FILE


def download_audio(info: Dict) -> Path:
    platform = info["platform"]
    url = clean_url(info["url"])
    video_id = info["id"]

    outtmpl = str(TMP_DIR / f"{video_id}.%(ext)s")
    cmd = yt_dlp_base_cmd(platform)
    cmd.extend([
        "--no-playlist",
        "-f", "ba/bestaudio",
        "-x",
        "--audio-format", AUDIO_FORMAT,
        "--audio-quality", AUDIO_QUALITY,
        "-o", outtmpl,
        url,
    ])
    run(cmd)

    files = [p for p in TMP_DIR.glob(f"{video_id}.*") if p.is_file() and not p.name.endswith(".part")]
    if not files:
        raise RuntimeError(f"audio file not found for {video_id}")
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def probe_audio_duration(audio_path: Path) -> float:
    res = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ], capture=True)
    try:
        duration = float(res.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"unable to read audio duration from ffprobe: {res.stdout!r}") from exc
    if duration <= 0:
        raise RuntimeError(f"invalid audio duration: {duration}")
    return duration


def validate_download_duration(info: Dict, audio_duration: float):
    expected = info.get("duration")
    if not expected:
        return
    expected = float(expected)
    allowed_shortfall = max(10.0, expected * 0.02)
    if audio_duration < expected - allowed_shortfall:
        raise RuntimeError(
            "downloaded audio is incomplete: "
            f"audio={audio_duration:.3f}s, source={expected:.3f}s, "
            f"shortfall={expected - audio_duration:.3f}s"
        )


def load_model() -> WhisperModel:
    log(f"[info] loading model: {MODEL_NAME}, device={DEVICE}, compute_type={COMPUTE_TYPE}")
    return WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)


def transcribe_audio(model: WhisperModel, audio_path: Path, audio_duration: float) -> Dict:
    kwargs = {
        "language": LANGUAGE if LANGUAGE else None,
        "beam_size": BEAM_SIZE,
        "vad_filter": VAD_FILTER,
        "condition_on_previous_text": False,
    }
    if INITIAL_PROMPT:
        kwargs["initial_prompt"] = INITIAL_PROMPT

    ts_lines = []
    plain_lines = []
    kept_segments = 0
    transcript_end = 0.0
    detected_info = None

    # Split long inputs deliberately so a prematurely exhausted lazy iterator
    # cannot silently turn a partial transcript into a successful output.
    chunk_seconds = max(60, TRANSCRIBE_CHUNK_SECONDS)
    with tempfile.TemporaryDirectory(prefix="transcribe_chunks_", dir=TMP_DIR) as chunk_dir:
        chunk_start = 0.0
        chunk_index = 0
        while chunk_start < audio_duration:
            chunk_duration = min(float(chunk_seconds), audio_duration - chunk_start)
            chunk_path = Path(chunk_dir) / f"chunk_{chunk_index:04d}.wav"
            log(
                f"[info] transcribing chunk {chunk_index + 1}: "
                f"{chunk_start:.3f}s..{chunk_start + chunk_duration:.3f}s"
            )
            run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{chunk_start:.3f}", "-t", f"{chunk_duration:.3f}",
                "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(chunk_path),
            ])
            segments, current_info = model.transcribe(str(chunk_path), **kwargs)
            if detected_info is None:
                detected_info = current_info
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                start = chunk_start + float(seg.start)
                end = min(audio_duration, chunk_start + float(seg.end))
                kept_segments += 1
                transcript_end = max(transcript_end, end)
                ts_lines.append(
                    f"[{seconds_to_mmss_mmm(start)} --> {seconds_to_mmss_mmm(end)}] {text}"
                )
                plain_lines.append(text)
            chunk_path.unlink(missing_ok=True)
            chunk_start += chunk_duration
            chunk_index += 1

    return {
        "language": getattr(detected_info, "language", ""),
        "language_probability": getattr(detected_info, "language_probability", ""),
        "segments": kept_segments,
        "audio_duration": audio_duration,
        "transcript_end": transcript_end,
        "coverage_ratio": transcript_end / audio_duration,
        "timestamp_text": "\n".join(ts_lines).strip(),
        "plain_text": "\n".join(plain_lines).strip(),
    }


def validate_transcription(result: Dict):
    audio_duration = float(result.get("audio_duration") or 0)
    transcript_end = float(result.get("transcript_end") or 0)
    if not result.get("segments") or not result.get("plain_text"):
        raise RuntimeError("transcription produced no text")
    trailing_gap = max(0.0, audio_duration - transcript_end)
    allowed_gap = max(MAX_TRAILING_GAP_SECONDS, audio_duration * MAX_TRAILING_GAP_RATIO)
    if trailing_gap > allowed_gap:
        raise RuntimeError(
            "transcription is incomplete: "
            f"audio={audio_duration:.3f}s, transcript_end={transcript_end:.3f}s, "
            f"trailing_gap={trailing_gap:.3f}s, allowed={allowed_gap:.3f}s"
        )
    log(
        f"[ok] transcription coverage: {transcript_end:.3f}/{audio_duration:.3f}s "
        f"({result['coverage_ratio']:.2%}), trailing gap {trailing_gap:.3f}s"
    )


def write_outputs(info: Dict, result: Dict):
    ts_body = (result.get("timestamp_text", "").strip() + "\n") if result.get("timestamp_text") else ""
    plain_body = (result.get("plain_text", "").strip() + "\n") if result.get("plain_text") else ""
    atomic_write_text(ts_output_path(info), ts_body)
    atomic_write_text(plain_output_path(info), plain_body)

    meta = {
        "id": info.get("id", ""),
        "title": info.get("title", ""),
        "url": clean_url(info.get("url", "")),
        "uploader": info.get("uploader", ""),
        "channel": info.get("channel", ""),
        "duration": info.get("duration"),
        "platform": info.get("platform", ""),
        "destination": DESTINATION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "whisper_model": MODEL_NAME,
        "language": result.get("language", ""),
        "language_probability": result.get("language_probability", ""),
        "segments": result.get("segments", 0),
        "audio_duration": result.get("audio_duration"),
        "transcript_end": result.get("transcript_end"),
        "coverage_ratio": result.get("coverage_ratio"),
    }
    save_json(meta_output_path(), meta)


def cleanup_temp_file(path: Optional[Path]):
    try:
        if path and path.exists():
            path.unlink(missing_ok=True)
    except Exception as e:
        log(f"[warn] cleanup failed: {e}")


def git_commit_and_push(message: str):
    git_run(["git", "add", str(OUTPUT_ROOT), "state_single_video"], check=False)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        log("[info] no git changes to commit")
        return

    git_run(["git", "commit", "-m", message])

    if GIT_BRANCH:
        subprocess.run(["git", "pull", "--rebase", "origin", GIT_BRANCH], check=False)
        git_run(["git", "push", "origin", f"HEAD:{GIT_BRANCH}"])
    else:
        git_run(["git", "push"])


def main():
    ensure_dirs()

    source_url = clean_url(SOURCE_URL)
    if not source_url:
        raise ValueError("SOURCE_URL is empty")

    platform = get_platform()
    save_progress("fetching_info", extra={"resolved_platform": platform, "source_url": source_url})

    info = fetch_video_info(source_url, platform)
    save_progress("downloading_audio", extra=info)

    model = load_model()
    audio_path = None

    try:
        audio_path = download_audio(info)
        audio_duration = probe_audio_duration(audio_path)
        validate_download_duration(info, audio_duration)
        log(f"[ok] downloaded audio duration: {audio_duration:.3f}s")

        save_progress("transcribing", extra=info)
        result = transcribe_audio(model, audio_path, audio_duration)
        validate_transcription(result)

        save_progress("writing_outputs", extra=info)
        write_outputs(info, result)

        save_progress("finished", extra=info)
        git_commit_and_push(f"single-video: {DESTINATION} {info['id']}")
        log(f"[ok] completed: {info['id']} {info['title']}")

    except Exception as e:
        save_progress("error", note=repr(e), extra=info if 'info' in locals() else {"source_url": source_url})
        raise
    finally:
        cleanup_temp_file(audio_path)


if __name__ == "__main__":
    main()
