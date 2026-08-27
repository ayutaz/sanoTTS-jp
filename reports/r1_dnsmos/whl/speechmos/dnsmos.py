
import concurrent.futures
import os
import librosa
import numpy as np
import onnxruntime as ort
from requests import session

__all__ = ["DNSMOS", "run"]

SR = 16000
INPUT_LENGTH = 9.01
dnsmos = None


class DNSMOS:
    def __init__(self, primary_model_path, p808_model_path) -> None:
        self.primary_model_path = primary_model_path
        self.onnx_sess = ort.InferenceSession(self.primary_model_path)
        self.p808_onnx_sess = ort.InferenceSession(p808_model_path)

    def audio_melspec(self, audio, n_mels=120, frame_size=320, hop_length=160, sr=16000, to_db=True):
        mel_spec = librosa.feature.melspectrogram(
            y=audio, sr=sr, n_fft=frame_size + 1, hop_length=hop_length, n_mels=n_mels)
        if to_db:
            mel_spec = (librosa.power_to_db(mel_spec, ref=np.max) + 40) / 40
        return mel_spec.T

    def get_polyfit_val(self, sig, bak, ovr, is_personalized_MOS):
        if is_personalized_MOS:
            p_ovr = np.poly1d(
                [-0.00533021, 0.005101, 1.18058466, -0.11236046])
            p_sig = np.poly1d(
                [-0.01019296, 0.02751166, 1.19576786, -0.24348726])
            p_bak = np.poly1d(
                [-0.04976499, 0.44276479, -0.1644611, 0.96883132])
        else:
            p_ovr = np.poly1d([-0.06766283, 1.11546468, 0.04602535])
            p_sig = np.poly1d([-0.08397278, 1.22083953, 0.0052439])
            p_bak = np.poly1d([-0.13166888, 1.60915514, -0.39604546])

        sig_poly = p_sig(sig)
        bak_poly = p_bak(bak)
        ovr_poly = p_ovr(ovr)

        return sig_poly, bak_poly, ovr_poly

    def __call__(self, sample, fs, is_personalized_MOS):
        clip_dict = {}
        if isinstance(sample, np.ndarray):
            audio = sample
            if not ((audio >= -1).all() and (audio <= 1).all()):
                raise ValueError("np.ndarray values must be between -1 and 1.")
        elif isinstance(sample, str) and os.path.isfile(sample):
            audio, _ = librosa.load(sample, sr=fs)
            clip_dict['filename'] = sample
        else:
            raise ValueError(
                f"Input must be a numpy array or a path to an audio file.")

        len_samples = int(INPUT_LENGTH * fs)
        while len(audio) < len_samples:
            audio = np.append(audio, audio)

        num_hops = int(np.floor(len(audio) / fs) - INPUT_LENGTH) + 1
        hop_len_samples = fs
        predicted_mos_sig_seg = []
        predicted_mos_bak_seg = []
        predicted_mos_ovr_seg = []
        predicted_p808_mos = []

        for idx in range(num_hops):
            audio_seg = audio[int(idx * hop_len_samples): int((idx + INPUT_LENGTH) * hop_len_samples)]
            if len(audio_seg) < len_samples:
                continue

            input_features = np.array(audio_seg).astype(
                'float32')[np.newaxis, :]
            p808_input_features = np.array(self.audio_melspec(
                audio=audio_seg[:-160])).astype('float32')[np.newaxis, :, :]
            oi = {'input_1': input_features}
            p808_oi = {'input_1': p808_input_features}
            p808_mos = self.p808_onnx_sess.run(None, p808_oi)[0][0][0]
            mos_sig_raw, mos_bak_raw, mos_ovr_raw = self.onnx_sess.run(None, oi)[
                0][0]
            mos_sig, mos_bak, mos_ovr = self.get_polyfit_val(
                mos_sig_raw, mos_bak_raw, mos_ovr_raw, is_personalized_MOS)
            predicted_mos_sig_seg.append(mos_sig)
            predicted_mos_bak_seg.append(mos_bak)
            predicted_mos_ovr_seg.append(mos_ovr)
            predicted_p808_mos.append(p808_mos)

        clip_dict['ovrl_mos'] = np.mean(predicted_mos_ovr_seg)
        clip_dict['sig_mos'] = np.mean(predicted_mos_sig_seg)
        clip_dict['bak_mos'] = np.mean(predicted_mos_bak_seg)
        clip_dict['p808_mos'] = np.mean(predicted_p808_mos)
        return clip_dict


def run(sample, sr, model_type="dnsmos", return_df=True, verbose=False):
    global dnsmos

    if sr != SR:
        raise ValueError(f"Sampling rate must be {SR}.")

    p808_model_path = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "dnsmos_models", 'model_v8.onnx')

    is_personalized_eval = model_type == "dnsmos_personalized"
    if is_personalized_eval:
        primary_model_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "pdnsmos_models", 'sig_bak_ovr.onnx')
    else:
        primary_model_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "dnsmos_models", 'sig_bak_ovr.onnx')

    if dnsmos is None or primary_model_path != dnsmos.primary_model_path:
        dnsmos = DNSMOS(primary_model_path, p808_model_path)

    if verbose:
        print(f"The model sampling rate is {sr}.")
        print(f"is personalized eval {is_personalized_eval}.")

    # evaluate a single sample, or a list of samples
    if (isinstance(sample, np.ndarray) and sample.ndim == 1) or \
            (isinstance(sample, str) and os.path.isfile(sample)):
        results = dnsmos(sample, sr, is_personalized_eval)
        if verbose:
            print(results)
        return results
    elif isinstance(sample, list):
        from itertools import repeat
        from tqdm import tqdm

        sample_list = sample
        if verbose:
            print(f"Using DNSMOS to evaluate {len(sample_list)} samples.")

        results = None
        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = tqdm(executor.map(
                dnsmos, sample_list, repeat(sr), repeat(is_personalized_eval)))

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
            f"Input must be a numpy array of ndim=1 or a path to an audio file, or a list of these.")
