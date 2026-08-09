from pydub import AudioSegment

from config.settings import RuntimeSettings
from pipeline.audio_mixer import AudioMixer


def test_duck_preserves_chunk_length():
    source = AudioSegment.silent(duration=1000, frame_rate=48000).set_channels(2).set_sample_width(2)
    mixed = AudioMixer(RuntimeSettings())._duck(source, 200, 800, .3, 100)
    assert len(mixed) == 1000
    assert mixed.frame_rate == 48000
