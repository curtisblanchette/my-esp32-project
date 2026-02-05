"""
Voice processing service using Vosk (STT) and Kokoro (TTS).
"""

import io
import json
import logging
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
_vosk = None
_kokoro = None


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

    def synthesize(self, text: str) -> bytes:
        """
        Synthesize text to speech using Kokoro.

        Args:
            text: Text to synthesize

        Returns:
            WAV audio bytes (24kHz sample rate)
        """
        kokoro = self._load_kokoro_model()
        if kokoro is None:
            raise RuntimeError("Kokoro model not loaded")

        try:
            # Generate audio using Kokoro
            samples, sample_rate = kokoro.create(
                text,
                voice=self.kokoro_voice,
                speed=self.kokoro_speed,
                lang=self.kokoro_lang,
            )

            # Convert to int16
            audio_int16 = (samples * 32767).astype(np.int16)

            # Write to WAV buffer
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio_int16.tobytes())

            wav_buffer.seek(0)
            logger.debug(f"Synthesized {len(text)} chars to WAV ({len(wav_buffer.getvalue())} bytes)")
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
