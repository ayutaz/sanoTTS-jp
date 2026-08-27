import concurrent.futures
import os
import math
import numpy as np
from numpy.fft import rfft
from numpy.lib.stride_tricks import as_strided
import onnxruntime as ort

__all__ = ["PLCMOS", "run"]

SR = 16000
plcmos = None


class PLCMOS:
    def __init__(self, model_name="plcmos_v2", embed_rounds=15):
        """
        Use the final plcmos v2 model. This is the no-holdout version of the model described in the
        PLCMOS paper published at INTERSPEECH 2023.
        """
        self.model_name = model_name
        self.onnx_name = model_name + ".onnx"
        self.max_lens = 999999999999
        self.embed_rounds = embed_rounds

        self.model_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "plcmos_models", self.onnx_name)
        self.session = ort.InferenceSession(self.model_path)

    def logpow_dns(self, sig, floor=-30.):
        """
        Compute log power of complex spectrum.

        Floor any -`np.inf` value to (nonzero minimum + `floor`) dB.
        If all values are 0s, floor all values to -80 dB.
        """
        log10e = np.log10(np.e)
        pspec = sig.real**2 + sig.imag**2
        zeros = pspec == 0
        logp = np.empty_like(pspec)
        if np.any(~zeros):
            logp[~zeros] = np.log(pspec[~zeros])
            logp[zeros] = np.log(pspec[~zeros].min()) + floor / 10 / log10e
        else:
            logp.fill(-80 / 10 / log10e)

        return logp

    def hop2hsize(self, wind, hop):
        """
        Convert hop fraction to integer size if necessary.
        """
        if hop >= 1:
            assert isinstance(hop, int), "Hop size must be integer!"
            return hop
        else:
            assert 0 < hop < 1, "Hop fraction has to be in range (0,1)!"
            return int(len(wind) * hop)

    def stana(self, sig, sr, wind, hop, synth=False, center=False):
        """
        Short term analysis by windowing
        """
        ssize = len(sig)
        fsize = len(wind)
        hsize = self.hop2hsize(wind, hop)
        if synth:
            sstart = hsize - fsize  # int(-fsize * (1-hfrac))
        elif center:
            sstart = -int(len(wind) / 2)  # odd window centered at exactly n=0
        else:
            sstart = 0
        send = ssize

        nframe = math.ceil((send - sstart) / hsize)
        # Calculate zero-padding sizes
        zpleft = -sstart
        zpright = (nframe - 1) * hsize + fsize - zpleft - ssize
        if zpleft > 0 or zpright > 0:
            sigpad = np.zeros(ssize + zpleft + zpright, dtype=sig.dtype)
            sigpad[zpleft:len(sigpad) - zpright] = sig
        else:
            sigpad = sig

        return as_strided(sigpad, shape=(nframe, fsize),
                          strides=(sig.itemsize * hsize, sig.itemsize)) * wind

    def stft(self, sig, sr, wind, hop, nfft):
        """
        Compute STFT: window + rfft
        """
        frames = self.stana(sig, sr, wind, hop, synth=True)
        return rfft(frames, n=nfft)

    def stft_transform(self, audio, dft_size=512, hop_fraction=0.5, sr=SR):
        """
        Compute STFT parameters, then compute STFT
        """
        window = np.hamming(dft_size + 1)
        window = window[:-1]
        amp = np.abs(self.stft(audio, sr, window, hop_fraction, dft_size))
        feat = self.logpow_dns(amp, floor=-120.)
        return feat / 20.

    def get_mos(self, audio_degraded, sr_degraded=SR, combined=True, verbose=False):
        audio_features_degraded = np.float32(self.stft_transform(audio_degraded))[
            np.newaxis, np.newaxis, ...]
        mos = 0
        intermediate_scores = {}
        for i in range(self.embed_rounds):
            rater_embed = np.random.normal(size=(1, 64))

            assert len(
                audio_features_degraded) <= self.max_lens, "Maximum input length exceeded"
            onnx_inputs = {
                "degraded_audio": audio_features_degraded, "rater_embed": np.array(
                    rater_embed, dtype=np.float32).reshape(
                    1, -1)}

            mos_val = float(self.session.run(None, onnx_inputs)[0])
            intermediate_scores[str(i) + "_nonint"] = mos_val
            mos += mos_val
        return mos / self.embed_rounds

    def __call__(self, sample, model_ver=2, verbose=False):
        results = {}
        if isinstance(sample, np.ndarray):
            audio_degraded, sr_degraded = sample, SR
            if not ((audio_degraded >= -1).all() and (audio_degraded <= 1).all()):
                raise ValueError("np.ndarray values must be between -1 and 1.")
        elif isinstance(sample, str) and os.path.isfile(sample):
            import librosa
            audio_degraded, sr_degraded = librosa.load(sample, sr=SR)
            results["filename"] = sample
        else:
            raise ValueError(
                "Input must be a numpy array or a path to an audio file")

        score = self.get_mos(audio_degraded, sr_degraded)
        results["plcmos"] = score
        results["model"] = self.model_name
        return results


def run(sample, sr, return_df=True, verbose=False):
    global plcmos

    if sr != SR:
        raise ValueError(f"Sampling rate must be {SR}.")

    if plcmos is None:
        plcmos = PLCMOS()

    if verbose:
        print(f"Model version {plcmos.model_name}.")
        print(f"The model sampling rate is {SR}.")

    # eval a single sample, or a list of samples
    if (isinstance(sample, np.ndarray) and sample.ndim == 1) or \
            (isinstance(sample, str) and os.path.isfile(sample)):
        results = plcmos(sample)
        if verbose:
            print(results)
        return results
    elif isinstance(sample, list):
        from tqdm import tqdm

        sample_list = sample
        if verbose:
            print(f"Using PLCMOS to evaluate {len(sample_list)} samples.")

        results = None
        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = tqdm(executor.map(plcmos, sample_list))

        if return_df:
            import pandas as pd
            df = pd.DataFrame(results)
            if verbose:
                print(df.describe())
            return df
        else:
            return list(results)
    else:
        raise ValueError(
            "Input must be a numpy array of ndim=1 or a path to an audio file, or a list of these.")
