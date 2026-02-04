"""
Voice processing service using Vosk (STT) and Piper (TTS).
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
_piper_voice = None
_piper_phonemize = None


def _get_vosk():
    global _vosk
    if _vosk is None:
        import vosk
        _vosk = vosk
    return _vosk


def _get_piper_phonemize():
    global _piper_phonemize
    if _piper_phonemize is None:
        import piper_phonemize
        _piper_phonemize = piper_phonemize
    return _piper_phonemize


class VoiceService:
    """Service for speech-to-text and text-to-speech processing."""

    def __init__(
        self,
        vosk_model_path: str | Path | None = None,
        piper_model_path: str | Path | None = None,
        piper_voice: str = "en-us",
    ):
        self.vosk_model_path = Path(vosk_model_path) if vosk_model_path else None
        self.piper_model_path = Path(piper_model_path) if piper_model_path else None
        self.piper_voice = piper_voice
        self._vosk_model = None
        self._piper_model = None

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

    def _load_piper_model(self):
        """Lazy load Piper model."""
        if self._piper_model is None and self.piper_model_path:
            from piper import PiperVoice
            if not self.piper_model_path.exists():
                raise FileNotFoundError(f"Piper model not found: {self.piper_model_path}")
            self._piper_model = PiperVoice.load(str(self.piper_model_path))
            logger.info(f"Loaded Piper model from {self.piper_model_path}")
        return self._piper_model

    def synthesize(self, text: str, sample_rate: int = 22050) -> bytes:
        """
        Synthesize text to speech using Piper.

        Args:
            text: Text to synthesize
            sample_rate: Output sample rate (Piper uses model's native rate)

        Returns:
            WAV audio bytes
        """
        voice = self._load_piper_model()
        if voice is None:
            raise RuntimeError("Piper model not loaded")

        try:
            piper_phonemize = _get_piper_phonemize()

            # Phonemize and get IDs
            phonemes_list = piper_phonemize.phonemize_espeak(text, self.piper_voice)

            # Synthesize each sentence and concatenate
            audio_chunks = []
            for phonemes in phonemes_list:
                phoneme_ids = piper_phonemize.phoneme_ids_espeak(phonemes)
                audio = voice.phoneme_ids_to_audio(phoneme_ids)
                audio_chunks.append(audio)

            # Concatenate all audio
            full_audio = np.concatenate(audio_chunks) if len(audio_chunks) > 1 else audio_chunks[0]

            # Convert to int16
            audio_int16 = (full_audio * 32767).astype(np.int16)

            # Write to WAV buffer
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(voice.config.sample_rate)
                wav.writeframes(audio_int16.tobytes())

            wav_buffer.seek(0)
            logger.debug(f"Synthesized {len(text)} chars to WAV ({len(wav_buffer.getvalue())} bytes)")
            return wav_buffer.getvalue()

        except Exception as e:
            logger.error(f"Piper TTS failed: {e}")
            raise RuntimeError(f"TTS synthesis failed: {e}")

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
        return self.piper_model_path is not None and self.piper_model_path.exists()
