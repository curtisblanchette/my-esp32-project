"""
Voice processing service using Vosk (STT) and Kokoro (TTS).
"""

import io
import json
import logging
import re
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
_vosk = None
_kokoro = None

# Number word tables for text normalization
_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _num_to_words(n: int) -> str:
    """Convert an integer (0–999,999,999) to English words."""
    if n == 0:
        return "zero"
    if n < 0:
        return "negative " + _num_to_words(-n)

    parts = []
    if n >= 1_000_000:
        parts.append(_num_to_words(n // 1_000_000) + " million")
        n %= 1_000_000
    if n >= 1_000:
        parts.append(_num_to_words(n // 1_000) + " thousand")
        n %= 1_000
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))
    elif n > 0:
        parts.append(_ONES[n])
    return " ".join(parts)


def _get_vosk():
    global _vosk
    if _vosk is None:
        import vosk
        _vosk = vosk
    return _vosk


class VoiceService:
    """Service for speech-to-text and text-to-speech processing."""

    def __init__(
        self,
        vosk_model_path: str | Path | None = None,
        kokoro_model_path: str | Path | None = None,
        kokoro_voices_path: str | Path | None = None,
        kokoro_voice: str = "af_heart",
        kokoro_speed: float = 1.0,
        kokoro_lang: str = "en-us",
    ):
        self.vosk_model_path = Path(vosk_model_path) if vosk_model_path else None
        self.kokoro_model_path = Path(kokoro_model_path) if kokoro_model_path else None
        self.kokoro_voices_path = Path(kokoro_voices_path) if kokoro_voices_path else None
        self.kokoro_voice = kokoro_voice
        self.kokoro_speed = kokoro_speed
        self.kokoro_lang = kokoro_lang
        self._vosk_model = None
        self._kokoro_model = None

    def _load_vosk_model(self):
        """Lazy load Vosk model."""
        if self._vosk_model is None and self.vosk_model_path:
            vosk = _get_vosk()
            if not self.vosk_model_path.exists():
                raise FileNotFoundError(f"Vosk model not found: {self.vosk_model_path}")
            vosk.SetLogLevel(-1)  # Suppress Vosk logs
            self._vosk_model = vosk.Model(str(self.vosk_model_path))
            logger.info(f"Loaded Vosk model from {self.vosk_model_path}")
        return self._vosk_model

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe audio to text using Vosk.

        Args:
            audio_data: Raw audio bytes (PCM 16-bit mono) or WAV file bytes
            sample_rate: Sample rate of the audio (default 16000 for Vosk)

        Returns:
            Transcribed text
        """
        model = self._load_vosk_model()
        if model is None:
            raise RuntimeError("Vosk model not loaded")

        vosk = _get_vosk()

        # Convert audio to proper format if needed
        pcm_data = self._convert_to_pcm(audio_data, sample_rate)

        # Create recognizer and process audio
        recognizer = vosk.KaldiRecognizer(model, sample_rate)
        recognizer.SetWords(True)

        # Process in chunks
        chunk_size = 4000
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i:i + chunk_size]
            recognizer.AcceptWaveform(chunk)

        # Get final result
        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()

        logger.info(f"Transcribed ({len(pcm_data)} bytes audio): '{text}'")
        return text

    def _load_kokoro_model(self):
        """Lazy load Kokoro model."""
        if self._kokoro_model is None and self.kokoro_model_path and self.kokoro_voices_path:
            from kokoro_onnx import Kokoro
            if not self.kokoro_model_path.exists():
                raise FileNotFoundError(f"Kokoro model not found: {self.kokoro_model_path}")
            if not self.kokoro_voices_path.exists():
                raise FileNotFoundError(f"Kokoro voices not found: {self.kokoro_voices_path}")
            self._kokoro_model = Kokoro(
                str(self.kokoro_model_path),
                str(self.kokoro_voices_path),
            )
            logger.info(f"Loaded Kokoro model from {self.kokoro_model_path}")
        return self._kokoro_model

    def _clean_text_for_tts(self, text: str) -> str:
        """
        Clean and normalize text for TTS.

        Converts numbers, times, currency, percentages, units, and ordinals
        to their spoken English form. Strips emojis, markdown, arrows, and
        other symbols that TTS engines can't pronounce.
        """
        # --- Strip non-pronounceable characters ---

        # Remove emojis
        text = re.compile(
            "["
            "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251"
            "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
            "\U00002600-\U000026FF\U00002700-\U000027BF"
            "]+",
            flags=re.UNICODE,
        ).sub("", text)

        # Remove arrows and symbols
        text = re.sub(r"[→←↑↓↔↕⇒⇐⇑⇓⇔⇕➔➜➡➤►▶◄◀•●○◦▪▫★☆✓✗✔✘|~`^]", " ", text)

        # Remove markdown formatting
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # **bold**
        text = re.sub(r"\*([^*]+)\*", r"\1", text)       # *italic*
        text = re.sub(r"__([^_]+)__", r"\1", text)       # __underline__
        text = re.sub(r"_([^_]+)_", r"\1", text)         # _italic_
        text = re.sub(r"`([^`]+)`", r"\1", text)         # `code`

        # --- Normalize numbers and symbols to spoken English ---

        # Unit symbols adjacent to numbers: 20.5°C → 20.5 degrees celsius
        text = re.sub(r'°C\b', ' degrees celsius', text)
        text = re.sub(r'°F\b', ' degrees fahrenheit', text)

        # Times: 5:43 PM → five forty three PM, 14:30 → two thirty PM
        def _expand_time(m: re.Match) -> str:
            h, mins = int(m.group(1)), int(m.group(2))
            period = (m.group(3) or "").strip()
            if not period and h >= 13:
                period = "PM"
                h -= 12
            elif not period and h == 0:
                h = 12
                period = "AM"
            h_words = _num_to_words(h)
            if mins == 0:
                m_words = "o'clock"
            elif mins < 10:
                m_words = "oh " + _num_to_words(mins)
            else:
                m_words = _num_to_words(mins)
            return f"{h_words} {m_words} {period}".strip()

        # Match H:MM or HH:MM with optional seconds and AM/PM
        text = re.sub(
            r'\b(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM|am|pm|A\.M\.|P\.M\.)?\b',
            _expand_time,
            text,
        )

        # Currency: $5.99 → five dollars and ninety nine cents
        def _expand_currency(m: re.Match) -> str:
            symbol = m.group(1)
            dollars = int(m.group(2))
            cents = int(m.group(3)) if m.group(3) else 0
            name = {"$": "dollar", "\u00a3": "pound", "\u20ac": "euro"}.get(symbol, "dollar")
            plural = name + "s" if dollars != 1 else name
            result = f"{_num_to_words(dollars)} {plural}"
            if cents:
                cent_name = "cents" if cents != 1 else "cent"
                result += f" and {_num_to_words(cents)} {cent_name}"
            return result

        text = re.sub(r'([$£€])(\d+)(?:\.(\d{2}))?\b', _expand_currency, text)

        # Percentages: 85% → eighty five percent
        text = re.sub(
            r'\b(\d+(?:\.\d+)?)\s*%',
            lambda m: _num_to_words(int(float(m.group(1)))) + " percent",
            text,
        )

        # Ordinals: 1st, 2nd, 3rd, 4th...
        _ordinal_suffixes = {
            1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth",
            9: "ninth", 12: "twelfth",
        }
        def _expand_ordinal(m: re.Match) -> str:
            n = int(m.group(1))
            if n in _ordinal_suffixes:
                return _ordinal_suffixes[n]
            word = _num_to_words(n)
            if word.endswith("y"):
                return word[:-1] + "ieth"
            if word.endswith("e"):
                return word[:-1] + "th"
            return word + "th"

        text = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', _expand_ordinal, text)

        # Decimals: 3.14 → three point one four
        def _expand_decimal(m: re.Match) -> str:
            whole = _num_to_words(int(m.group(1)))
            frac = " ".join(_num_to_words(int(d)) for d in m.group(2))
            return f"{whole} point {frac}"

        text = re.sub(r'\b(\d+)\.(\d+)\b', _expand_decimal, text)

        # Temperature units: 72°F → seventy two degrees Fahrenheit
        text = re.sub(
            r'\b(\d+)\s*°\s*([FCfc])\b',
            lambda m: _num_to_words(int(m.group(1))) + " degrees " +
                      ("Fahrenheit" if m.group(2).upper() == "F" else "Celsius"),
            text,
        )

        # Bare numbers: 1234 → one thousand two hundred thirty four
        text = re.sub(r'\b(\d+)\b', lambda m: _num_to_words(int(m.group(1))), text)

        # Standalone colons (keep nothing — times already handled)
        text = re.sub(r"(?<!\w):(?!\w)", " ", text)

        # Degree symbol without unit
        text = text.replace("°", " degrees ")

        # Collapse multiple spaces and trim
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def _split_into_chunks(self, text: str, max_chars: int = 100) -> list[str]:
        """
        Split text into chunks safe for Kokoro's 510 phoneme limit.

        Uses a cascade of split points: newlines, sentences, clauses,
        then parentheses/timestamps as a last resort.
        """
        # First split on newlines and sentence boundaries
        segments = re.split(r'\n+|(?<=[.!?])\s+', text)

        chunks: list[str] = []
        current = ""

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # If a single segment exceeds max_chars, break it down further
            if len(segment) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                # Split on clauses, parentheses, or before timestamps
                parts = re.split(
                    r'(?<=[,;:])\s+|\s+[-–—]\s+|(?<=\))\s+|\s+(?=\d{1,2}:\d{2})',
                    segment,
                )
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if len(current) + len(part) + 1 > max_chars and current:
                        chunks.append(current.strip())
                        current = part
                    else:
                        current = f"{current} {part}".strip() if current else part
            elif len(current) + len(segment) + 1 > max_chars and current:
                chunks.append(current.strip())
                current = segment
            else:
                current = f"{current} {segment}".strip() if current else segment

        if current:
            chunks.append(current.strip())

        return chunks if chunks else [text]

    def synthesize(self, text: str) -> bytes:
        """
        Synthesize text to speech using Kokoro.

        Splits long text into chunks to stay within Kokoro's 510 phoneme limit,
        then concatenates the resulting audio.

        Args:
            text: Text to synthesize

        Returns:
            WAV audio bytes (24kHz sample rate)
        """
        kokoro = self._load_kokoro_model()
        if kokoro is None:
            raise RuntimeError("Kokoro model not loaded")

        # Clean text before synthesis
        clean_text = self._clean_text_for_tts(text)
        logger.debug(f"TTS text cleaned: '{text}' -> '{clean_text}'")

        chunks = self._split_into_chunks(clean_text)
        logger.info(f"TTS input ({len(clean_text)} chars): {clean_text[:100]}...")
        logger.info(f"TTS split into {len(chunks)} chunk(s): {[len(c) for c in chunks]} chars")

        try:
            all_samples: list[np.ndarray] = []
            sample_rate = None

            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                samples, sr = kokoro.create(
                    chunk,
                    voice=self.kokoro_voice,
                    speed=self.kokoro_speed,
                    lang=self.kokoro_lang,
                )
                all_samples.append(samples)
                sample_rate = sr

                # Add a brief pause (0.15s silence) between chunks
                if i < len(chunks) - 1:
                    pause = np.zeros(int(sr * 0.15), dtype=samples.dtype)
                    all_samples.append(pause)

            if not all_samples or sample_rate is None:
                raise RuntimeError("No audio generated")

            combined = np.concatenate(all_samples)

            # Convert to int16
            audio_int16 = (combined * 32767).astype(np.int16)

            # Write to WAV buffer
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio_int16.tobytes())

            wav_buffer.seek(0)
            logger.debug(f"Synthesized {len(text)} chars ({len(chunks)} chunks) to WAV ({len(wav_buffer.getvalue())} bytes)")
            return wav_buffer.getvalue()

        except Exception as e:
            logger.error(f"Kokoro TTS failed: {e}")
            raise RuntimeError(f"TTS synthesis failed: {e}")

    def set_voice(self, voice: str) -> None:
        """Set the TTS voice."""
        self.kokoro_voice = voice
        logger.info(f"TTS voice set to: {voice}")

    def set_speed(self, speed: float) -> None:
        """Set the TTS speed."""
        self.kokoro_speed = speed
        logger.info(f"TTS speed set to: {speed}")

    def _convert_to_pcm(self, audio_data: bytes, target_rate: int) -> bytes:
        """Convert various audio formats to PCM 16-bit mono."""
        import subprocess
        import tempfile

        # Check if it's already a WAV file
        if audio_data[:4] == b"RIFF":
            # Read WAV and convert to target format
            audio_buffer = io.BytesIO(audio_data)
            data, sample_rate = sf.read(audio_buffer, dtype="int16")

            # Convert stereo to mono if needed
            if len(data.shape) > 1:
                data = data.mean(axis=1).astype(np.int16)

            # Resample if needed
            if sample_rate != target_rate:
                data = self._resample(data, sample_rate, target_rate)

            return data.tobytes()

        # Check for webm/ogg/mp4 formats (browser MediaRecorder output)
        # webm starts with 0x1A45DFA3, ogg with OggS, mp4 with ftyp after first bytes
        is_webm = audio_data[:4] == b"\x1a\x45\xdf\xa3"
        is_ogg = audio_data[:4] == b"OggS"
        is_mp4 = b"ftyp" in audio_data[:12]

        if is_webm or is_ogg or is_mp4:
            # Use ffmpeg to convert to WAV
            ext = ".webm" if is_webm else ".ogg" if is_ogg else ".mp4"
            try:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as in_file:
                    in_file.write(audio_data)
                    in_path = in_file.name

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_file:
                    out_path = out_file.name

                # Convert with ffmpeg: mono, 16-bit, target sample rate
                result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", in_path,
                        "-ar", str(target_rate),
                        "-ac", "1",
                        "-sample_fmt", "s16",
                        out_path
                    ],
                    capture_output=True,
                    timeout=30
                )

                if result.returncode != 0:
                    logger.error(f"ffmpeg conversion failed: {result.stderr.decode()}")
                    raise RuntimeError("Audio conversion failed")

                # Read the converted WAV
                with open(out_path, "rb") as f:
                    wav_data = f.read()

                # Parse WAV and extract PCM
                audio_buffer = io.BytesIO(wav_data)
                data, _ = sf.read(audio_buffer, dtype="int16")
                return data.tobytes()

            finally:
                # Cleanup temp files
                import os
                try:
                    os.unlink(in_path)
                    os.unlink(out_path)
                except:
                    pass

        # Assume raw PCM data
        return audio_data

    def _resample(self, data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Simple resampling using linear interpolation."""
        if src_rate == dst_rate:
            return data

        duration = len(data) / src_rate
        new_length = int(duration * dst_rate)
        indices = np.linspace(0, len(data) - 1, new_length)
        return np.interp(indices, np.arange(len(data)), data).astype(np.int16)

    def is_stt_available(self) -> bool:
        """Check if speech-to-text is available."""
        return self.vosk_model_path is not None and self.vosk_model_path.exists()

    def is_tts_available(self) -> bool:
        """Check if text-to-speech is available."""
        return (
            self.kokoro_model_path is not None
            and self.kokoro_model_path.exists()
            and self.kokoro_voices_path is not None
            and self.kokoro_voices_path.exists()
        )
