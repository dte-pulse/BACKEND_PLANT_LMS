"""PPWEC media production (item 9): TTS audio + WebVTT captions per screen.

Pipeline: voice_over text -> TTS engine -> mp3 into upload_dir/ppwec/ ->
attach audio_url + caption_url + transcript to each screen (S7/S23).

TTS backends, in order of preference (first available wins):
  1. gTTS      (pip install gtts)         — natural, needs internet
  2. espeak-ng (apt install espeak-ng)    — robotic, offline
  3. tone sine (ffmpeg fallback)          — placeholder beep-track, keeps
                                            captions/timing pipeline testable

Captions: one WebVTT cue per sentence, timed from word-count at ~150 wpm
(mirrors the /tts-export estimator). After producing REAL audio we could
measure duration with ffprobe and re-time; sentence-level estimation is
within tolerance for narrated learning content.

Run:  cd Backend && ./venv/bin/python -m scripts.ppwec_media_produce [--module 1] [--force]
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ppwec import PpwecModule, PpwecScreen  # noqa: E402

WPM = 150


def tts_gtts(text: str, out: Path) -> bool:
    try:
        from gtts import gTTS
        gTTS(text=text, lang='en', tld='co.in', slow=False).save(str(out))
        return out.exists() and out.stat().st_size > 500
    except Exception:
        return False


def tts_espeak(text: str, out: Path) -> bool:
    exe = shutil.which('espeak-ng') or shutil.which('espeak')
    if not exe:
        return False
    wav = out.with_suffix('.wav')
    try:
        subprocess.run([exe, '-v', 'en+f3', '-s', '150', '-w', str(wav), text],
                       check=True, capture_output=True, timeout=120)
        # transcode to mp3 for the browser
        subprocess.run(['ffmpeg', '-y', '-i', str(wav), '-b:a', '96k', str(out)],
                       check=True, capture_output=True, timeout=120)
        wav.unlink(missing_ok=True)
        return out.exists() and out.stat().st_size > 500
    except Exception:
        return False


def tts_placeholder(text: str, out: Path) -> bool:
    """Sine-tone placeholder timed to the narration length (offline fallback)."""
    seconds = max(4.0, len(text.split()) / WPM * 60)
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi',
             '-i', f'sine=frequency=440:duration={seconds:.1f}',
             '-af', 'volume=0.15', '-b:a', '64k', str(out)],
            check=True, capture_output=True, timeout=120)
        return out.exists() and out.stat().st_size > 500
    except Exception:
        return False


def produce_audio(text: str, out: Path) -> tuple[bool, str]:
    for name, fn in (('gtts', tts_gtts), ('espeak', tts_espeak), ('placeholder', tts_placeholder)):
        if fn(text, out):
            return True, name
    return False, 'none'


def _vtt_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def make_vtt(text: str) -> str:
    """One cue per sentence, timed at ~WPM words/sec."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    cues = ['WEBVTT', '']
    t = 0.0
    for i, sent in enumerate(sentences, 1):
        dur = max(1.6, len(sent.split()) / WPM * 60)
        cues.append(str(i))
        cues.append(f'{_vtt_time(t)} --> {_vtt_time(t + dur)}')
        cues.append(sent)
        cues.append('')
        t += dur
    return '\n'.join(cues)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--module', type=int, default=1)
    ap.add_argument('--force', action='store_true', help='reproduce even if audio_url set')
    args = ap.parse_args()

    db = SessionLocal()
    try:
        m = db.query(PpwecModule).filter(PpwecModule.module_number == args.module).first()
        if not m:
            print(f'Module {args.module} not found')
            return
        screens = (db.query(PpwecScreen)
                   .filter(PpwecScreen.module_id == m.id, PpwecScreen.voice_over.isnot(None))
                   .order_by(PpwecScreen.screen_number).all())

        media_root = Path(settings.upload_dir) / 'ppwec'
        media_root.mkdir(parents=True, exist_ok=True)

        produced = skipped = failed = 0
        backend_used = None
        for s in screens:
            if s.audio_url and not args.force:
                skipped += 1
                continue
            fname = f'M{m.module_number:02d}-S{s.screen_number:02d}.mp3'
            vtt_name = f'M{m.module_number:02d}-S{s.screen_number:02d}.vtt'
            out = media_root / fname
            ok, backend = produce_audio(s.voice_over, out)
            if not ok:
                print(f'  S{s.screen_number:02d}: FAILED all backends')
                failed += 1
                continue
            backend_used = backend
            (media_root / vtt_name).write_text(make_vtt(s.voice_over), encoding='utf-8')
            s.audio_url = f'ppwec/{fname}'
            s.caption_url = f'ppwec/{vtt_name}'
            if not (s.transcript or '').strip():
                s.transcript = s.voice_over
            db.flush()
            produced += 1
            print(f'  S{s.screen_number:02d}: {fname} ({backend}, {out.stat().st_size // 1024} KB) + {vtt_name}')

        db.commit()
        print(f'Done: {produced} produced, {skipped} skipped (already present), {failed} failed. Backend: {backend_used}')
    finally:
        db.close()


if __name__ == '__main__':
    main()
