import io
import wave

import numpy as np
import pytest
import soundfile as sf

from srt_audio import SrtAudioError, render_srt_audio


def test_wav_output_preserves_gaps_timeline_end_and_pcm_mono(tmp_path):
    output = tmp_path / "dub.wav"
    cues = [{"index": 1, "start_ms": 500, "end_ms": 1500}, {"index": 3, "start_ms": 2000, "end_ms": 3000}]
    wavs = [np.ones(8000, dtype=np.float32) * 0.1, np.ones(8000, dtype=np.float32) * 0.2]
    render_srt_audio(cues, wavs, 8000, "wav", output)
    info = sf.info(output)
    assert info.samplerate == 44100 and info.channels == 1 and info.subtype == "PCM_16"
    assert info.duration == pytest.approx(3.0, abs=1 / 44100)
    samples, _ = sf.read(output)
    assert np.max(np.abs(samples[:20000])) == 0
    assert np.mean(samples[23000:60000]) > 0.09
    assert np.max(np.abs(samples[70000:85000])) == 0
    assert np.mean(samples[95000:125000]) > 0.18


def test_stretch_only_when_needed_and_reject_above_1_5_without_file(tmp_path, monkeypatch):
    import srt_audio

    calls = []
    monkeypatch.setattr(srt_audio.librosa.effects, "time_stretch", lambda samples, *, rate: (calls.append(rate) or samples[:int(len(samples) / rate)]))
    output = tmp_path / "dub.wav"
    cues = [{"index": 9, "start_ms": 0, "end_ms": 1000}]
    render_srt_audio(cues, [np.ones(8000, dtype=np.float32)], 8000, "wav", output)
    assert calls == []
    cues[0]["end_ms"] = 800
    render_srt_audio(cues, [np.ones(8000, dtype=np.float32)], 8000, "wav", output)
    assert calls == [pytest.approx(1.25)]
    cues[0]["end_ms"] = 500
    output.unlink()
    with pytest.raises(SrtAudioError) as error:
        render_srt_audio(cues, [np.ones(8000, dtype=np.float32)], 8000, "wav", output)
    assert error.value.code == "cue_too_long" and error.value.cue_index == 9
    assert not output.exists()


def test_renderer_never_crops_speech_if_stretcher_returns_oversized_audio(tmp_path, monkeypatch):
    import srt_audio

    monkeypatch.setattr(srt_audio.librosa.effects, "time_stretch", lambda samples, *, rate: np.ones(9000, dtype=np.float32))
    with pytest.raises(SrtAudioError) as error:
        render_srt_audio([{"index": 7, "start_ms": 0, "end_ms": 500}], [np.ones(6000, dtype=np.float32)], 8000, "wav", tmp_path / "never.wav")
    assert error.value.code == "cue_too_long"
    assert not (tmp_path / "never.wav").exists()


def test_exact_1_5_stretch_boundary_is_accepted(tmp_path, monkeypatch):
    import srt_audio

    rates = []
    def stretch(samples, *, rate):
        rates.append(rate)
        return np.zeros(round(len(samples) / rate), dtype=np.float32)
    monkeypatch.setattr(srt_audio.librosa.effects, "time_stretch", stretch)
    render_srt_audio([{"index": 2, "start_ms": 0, "end_ms": 1000}], [np.ones(40500, dtype=np.float32)], 27000, "wav", tmp_path / "ok.wav")
    assert rates == [pytest.approx(1.5)]


def test_mp3_profile_is_mono_44100_cbr_128k(tmp_path, monkeypatch):
    import srt_audio

    class Completed:
        returncode = 0
        stdout = b"ID3" + b"x" * 200
        stderr = b""
    commands = []
    monkeypatch.setattr(srt_audio.shutil, "which", lambda _: "ffmpeg.exe")
    monkeypatch.setattr(srt_audio.subprocess, "run", lambda args, **kwargs: (commands.append(args) or Completed()))
    output = tmp_path / "dub.mp3"
    render_srt_audio([{"index": 1, "start_ms": 0, "end_ms": 1000}], [np.zeros(8000, dtype=np.float32)], 8000, "mp3", output)
    assert output.read_bytes().startswith(b"ID3")
    command = commands[0]
    assert "-ac" in command and command[command.index("-ac") + 1] == "1"
    assert "-ar" in command and command[command.index("-ar") + 1] == "44100"
    assert command[command.index("-b:a") + 1] == "128k"


def test_invalid_format_or_generation_count_does_not_leave_output(tmp_path):
    output = tmp_path / "dub.wav"
    with pytest.raises(SrtAudioError):
        render_srt_audio([{"index": 1, "start_ms": 0, "end_ms": 1000}], [], 16000, "wav", output)
    assert not output.exists()
