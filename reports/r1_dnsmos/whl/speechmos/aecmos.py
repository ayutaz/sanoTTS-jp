import concurrent.futures
import librosa
import logging
import numpy as np
import onnxruntime as ort
import os

__all__ = ["AECMOS", "run"]

aecmos = None


class AECMOS:
    def __init__(self, model_name) -> None:
        self.model_name = model_name
        self.max_len = 20
        self.hop_fraction = 0.5

        if self.model_name == 'aecmos_16kHz':
            self.onnx_name = 'Run_1663915512_Stage_0.onnx'
            self.sampling_rate = 16000
            self.dft_size = 512
            self.transform = self._mel_transform
            self.need_scenario_marker = True
            self.hidden_size = (4, 1, 64)
        elif self.model_name == 'aecmos_scenarioless_16kHz':
            self.onnx_name = 'Run_1663829550_Stage_0.onnx'
            self.sampling_rate = 16000
            self.dft_size = 512
            self.transform = self._mel_transform
            self.need_scenario_marker = False
            self.hidden_size = (4, 1, 64)
        elif self.model_name == 'aecmos_48kHz':
            self.onnx_name = 'Run_1668423760_Stage_0.onnx'
            self.sampling_rate = 48000
            self.dft_size = 1536
            self.transform = self._mel_transform
            self.need_scenario_marker = True
            self.hidden_size = (4, 1, 96)
        else:
            ValueError, f"Not a supported model {self.model_name}."

        self.model_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "aecmos_models", self.onnx_name)

        self.ort_session = ort.InferenceSession(self.model_path)
        self.input_name = self.ort_session.get_inputs()[0].name

    def _mel_transform(self, sample, sr):
        mel_spec = librosa.feature.melspectrogram(
            y=sample, sr=sr, n_fft=self.dft_size + 1, hop_length=int(self.hop_fraction * self.dft_size), n_mels=160)
        mel_spec = (librosa.power_to_db(mel_spec, ref=np.max) + 40) / 40
        return mel_spec.T

    def _read_and_process_audio_files(self, lpb_path, mic_path, clip_path):
        lpb_sig, _ = librosa.load(lpb_path, sr=self.sampling_rate)
        mic_sig, _ = librosa.load(mic_path, sr=self.sampling_rate)
        enh_sig, _ = librosa.load(clip_path, sr=self.sampling_rate)

        # Make the clips the same length
        min_len = np.min([len(lpb_sig), len(mic_sig), len(enh_sig)])
        lpb_sig = lpb_sig[:min_len]
        mic_sig = mic_sig[:min_len]
        enh_sig = enh_sig[:min_len]
        return lpb_sig, mic_sig, enh_sig

    def _get_mos(self, talk_type, lpb_sig, mic_sig, enh_sig):
        if not len(lpb_sig) == len(mic_sig) == len(enh_sig):
            raise ValueError("The 'lpb', 'mic', and 'enh' clips must have the same length.")

        # cut segments if too long
        seg_nb_samples = self.max_len * self.sampling_rate
        if len(lpb_sig) >= seg_nb_samples:
            logging.warning('The input audio is too long, only the first 20 seconds will be used.')
            lpb_sig = lpb_sig[: seg_nb_samples]
            mic_sig = mic_sig[: seg_nb_samples]
            enh_sig = enh_sig[: seg_nb_samples]

        # feature transform
        lpb_sig = self.transform(lpb_sig, self.sampling_rate)
        mic_sig = self.transform(mic_sig, self.sampling_rate)
        enh_sig = self.transform(enh_sig, self.sampling_rate)

        # scenario marker
        if self.need_scenario_marker:
            if talk_type not in ['nst', 'st', 'dt']:
                raise ValueError("The 'talk_type' must be 'nst', 'st', or 'dt'.")

            if talk_type == 'nst':
                ne_st = 1
                fe_st = 0
            elif talk_type == 'st':
                ne_st = 0
                fe_st = 1
            else:
                ne_st = 0
                fe_st = 0

            mic_sig = np.concatenate(
                (mic_sig, np.ones((20, mic_sig.shape[1])) * (1 - fe_st), np.zeros((20, mic_sig.shape[1]))), axis=0)
            lpb_sig = np.concatenate(
                (lpb_sig, np.ones((20, lpb_sig.shape[1])) * (1 - ne_st), np.zeros((20, lpb_sig.shape[1]))), axis=0)
            enh_sig = np.concatenate((enh_sig, np.ones(
                (20, enh_sig.shape[1])), np.zeros((20, enh_sig.shape[1]))), axis=0)

        # stack
        feats = np.stack((lpb_sig, mic_sig, enh_sig)).astype(np.float32)
        feats = np.expand_dims(feats, axis=0)

        # GRU hidden layer shape is in h0
        h0 = np.zeros(self.hidden_size,
                      dtype=np.float32)
        result = self.ort_session.run([], {self.input_name: feats, 'h0': h0})
        result = result[0]

        echo_mos = float(result[0])
        deg_mos = float(result[1])
        return echo_mos, deg_mos

    def __call__(self, sample_dict, talk_type):
        results = {}
        lpb = sample_dict['lpb']
        mic = sample_dict['mic']
        enh = sample_dict['enh']
        if isinstance(lpb, np.ndarray) and \
                isinstance(mic, np.ndarray) and \
                isinstance(enh, np.ndarray):

            for key in [lpb, mic, enh]:
                if not ((key >= -1).all() and (key <= 1).all()):
                    raise ValueError(f"Values in input np.ndarray must be in range [-1, 1].")

            lpb_sig = lpb
            mic_sig = mic
            enh_sig = enh
        elif os.path.isfile(mic) and \
                os.path.isfile(lpb) and \
                os.path.isfile(enh):
            results = sample_dict

            lpb_sig, mic_sig, enh_sig = self._read_and_process_audio_files(
                lpb, mic, enh)
        else:
            raise ValueError("Input must be a numpy array or a path to an audio file")

        scores = self._get_mos(talk_type, lpb_sig, mic_sig, enh_sig)
        results['echo_mos'] = scores[0]
        results['deg_mos'] = scores[1]
        results['talk_type'] = talk_type
        results['model_name'] = self.model_name
        return results


def run(sample, sr, talk_type=None, return_df=True, verbose=False):
    global aecmos
    model_name = None

    if sr == 16000:
        if talk_type is None:
            model_name = 'aecmos_scenarioless_16kHz'
        else:
            model_name = 'aecmos_16kHz'
    elif sr == 48000:
        if talk_type is None:
            raise ValueError("The talk_type must be specified when using the 48kHz model.")
        else:
            model_name = 'aecmos_48kHz'
    else:
        raise ValueError("The sampling rate must be 16000 or 48000.")

    if aecmos is None or aecmos.model_name != model_name:
        aecmos = AECMOS(model_name)

    if verbose:
        print(f"Model version {aecmos.model_name}.")
        print(f"The model sampling rate is {aecmos.sampling_rate}.")

    if isinstance(sample, dict):
        if set(sample.keys()) != set(['lpb', 'mic', 'enh']):
            raise ValueError("The sample must be a dictionary with keys 'lpb', 'mic', and 'enh'.")

        results = aecmos(sample, talk_type)

        if verbose:
            print(results)
        return results
    elif isinstance(sample, list):
        from itertools import repeat
        from tqdm import tqdm

        if verbose:
            print(f"Using model AECMOS to evaluate {len(sample)} samples.")

        sample_list = sample
        results = None
        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = tqdm(executor.map(aecmos, sample_list, repeat(talk_type)))

        if return_df:
            import pandas as pd
            df = pd.DataFrame(results)
            if verbose:
                print(df.describe())
            return df
        else:
            return list(results)
    else:
        raise ValueError("Input must be a dictionary or a list of dictionaries.")
