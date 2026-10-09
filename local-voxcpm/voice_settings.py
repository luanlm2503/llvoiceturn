"""Map ElevenLabs-style voice settings onto the real VoxCPM2 generation knobs.

Defaults (stability 50, similarity 75, style 0, no boost) reproduce the original
output exactly: cfg_value=2.0, inference_timesteps=10, no post-processing.
Speed is handled separately by srt_audio.apply_speed.
"""

from dataclasses import dataclass

import numpy as np

BOOST_PEAK = 0.89  # ~ -1 dBFS


class VoiceSettingsError(ValueError):
    pass


def _piecewise(value: int, pivot: int, low: float, mid: float, high: float) -> float:
    if value <= pivot:
        return low + (mid - low) * value / pivot
    return mid + (high - mid) * (value - pivot) / (100 - pivot)


@dataclass(frozen=True)
class VoiceSettings:
    stability: int = 50
    similarity: int = 75
    style_exaggeration: int = 0
    speaker_boost: bool = False

    def __post_init__(self) -> None:
        for name in ("stability", "similarity", "style_exaggeration"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise VoiceSettingsError(f"{name} must be an integer between 0 and 100.")

    def model_kwargs(self) -> dict[str, object]:
        # Stability -> inference_timesteps: more diffusion steps = steadier, cleaner, slower.
        # Similarity -> cfg_value: follows the text and reference voice more closely.
        return {
            "cfg_value": round(_piecewise(self.similarity, 75, 1.0, 2.0, 3.0), 3),
            "inference_timesteps": round(_piecewise(self.stability, 50, 4, 10, 20)),
        }

    def style_hint(self) -> str:
        if self.style_exaggeration <= 0:
            return ""
        if self.style_exaggeration <= 33:
            return "slightly expressive"
        if self.style_exaggeration <= 66:
            return "expressive, lively intonation"
        return "very expressive, strong emotion"

    def full_text(self, text: str, description: str | None) -> str:
        parts = [part for part in ((description or "").strip(), self.style_hint()) if part]
        return f"({', '.join(parts)}){text}" if parts else text

    def boost(self, wav: np.ndarray) -> np.ndarray:
        if not self.speaker_boost:
            return wav
        peak = float(np.max(np.abs(wav))) if wav.size else 0.0
        if peak <= 1e-6:
            return wav
        return np.asarray(wav * (BOOST_PEAK / peak), dtype=np.float32)
