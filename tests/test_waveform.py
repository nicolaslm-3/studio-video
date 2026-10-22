"""Forme d'onde : pics justes (silence / son), cache disque, colonnes, affichage dans le Studio."""
import subprocess

import numpy as np
import pytest

from studiovideo.media import ffmpeg, waveform

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _clip(path):
    # 1 s de son, 1 s de silence, 1 s de son plus faible
    subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='if(lt(t,1),0.8*sin(2*PI*440*t),if(lt(t,2),0,0.2*sin(2*PI*440*t)))':s=44100:d=3",
                    str(path)], check=True)
    return path


def test_peaks_and_cache(tmp_path):
    src = _clip(tmp_path / "a.wav")
    pk = waveform.ensure(tmp_path, "fp1", src)
    assert abs(pk.size - 150) <= 2
    loud, quiet, low = pk[5:45].astype(float), pk[55:95].astype(float), pk[105:145].astype(float)
    assert loud.min() > 0.85 and quiet.max() < 0.01 and 0.4 < low.mean() < 0.5   # racine de 0,8 / 0 / 0,2
    assert waveform.cache_path(tmp_path, "fp1").exists()
    assert np.array_equal(waveform.load(tmp_path, "fp1"), pk)
    cols = waveform.columns(pk, 1.0, 3.0, 20)
    assert cols.shape == (20,) and cols[1:9].max() < 0.05 and cols[11:].min() > 0.4
    assert waveform.columns(pk, 0.0, 0.2, 40).shape == (40,)                  # plus de pixels que de pics
