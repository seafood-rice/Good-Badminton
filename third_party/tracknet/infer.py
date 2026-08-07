"""Inference-only extraction of TrackNetV3's prediction pipeline.

This file is NOT verbatim upstream. It is a mechanical extraction of
`predict.py`'s `__main__` block into `run_prediction()` plus the three
`test.py` helpers it needs, so we avoid importing `test.py` (which imports
`pycocotools` at module load). Edits vs upstream are enumerated in CONTRACT.md.
Source: qaz812345/TrackNetV3 @ 77c123ad4dd449b7d275f16cc43f316ba5b54042
"""
import math

import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset import Shuttlecock_Trajectory_Dataset, Video_IterableDataset
from utils.general import get_model, generate_frames, to_img, to_img_format, HEIGHT, WIDTH, COOR_TH


def get_ensemble_weight(seq_len, eval_mode):
    # verbatim from upstream test.py:25-50
    if eval_mode == 'average':
        weight = torch.ones(seq_len) / seq_len
    elif eval_mode == 'weight':
        weight = torch.ones(seq_len)
        for i in range(math.ceil(seq_len / 2)):
            weight[i] = (i + 1)
            weight[seq_len - i - 1] = (i + 1)
        weight = weight / weight.sum()
    else:
        raise ValueError('Invalid mode')
    return weight


def predict_location(heatmap):
    # verbatim from upstream test.py:52-79
    if np.amax(heatmap) == 0:
        return 0, 0, 0, 0
    (cnts, _) = cv2.findContours(heatmap.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = [cv2.boundingRect(ctr) for ctr in cnts]
    max_area_idx = 0
    max_area = rects[0][2] * rects[0][3]
    for i in range(1, len(rects)):
        area = rects[i][2] * rects[i][3]
        if area > max_area:
            max_area_idx = i
            max_area = area
    x, y, w, h = rects[max_area_idx]
    return x, y, w, h


def generate_inpaint_mask(pred_dict, th_h=30):
    # verbatim from upstream test.py:223-258
    y = np.array(pred_dict['Y'])
    vis_pred = np.array(pred_dict['Visibility'])
    inpaint_mask = np.zeros_like(y)
    i = 0
    j = 0
    threshold = th_h
    while j < len(vis_pred):
        while i < len(vis_pred) - 1 and vis_pred[i] == 1:
            i += 1
        j = i
        while j < len(vis_pred) - 1 and vis_pred[j] == 0:
            j += 1
        if j == i:
            break
        elif i == 0 and y[j] > threshold:
            inpaint_mask[:j] = 1
        elif (i > 1 and y[i - 1] > threshold) and (j < len(vis_pred) and y[j] > threshold):
            inpaint_mask[i:j] = 1
        else:
            pass
        i = j
    return inpaint_mask.tolist()


def predict(indices, y_pred=None, c_pred=None, img_scaler=(1, 1)):
    """ Predict coordinates from heatmap or inpainted coordinates.

        Args:
            indices (torch.Tensor): indices of input sequence with shape (N, L, 2)
            y_pred (torch.Tensor, optional): predicted heatmap sequence with shape (N, L, H, W)
            c_pred (torch.Tensor, optional): predicted inpainted coordinates sequence with shape (N, L, 2)
            img_scaler (Tuple): image scaler (w_scaler, h_scaler)

        Returns:
            pred_dict (Dict): dictionary of predicted coordinates
                Format: {'Frame':[], 'X':[], 'Y':[], 'Visibility':[]}
    """

    pred_dict = {'Frame':[], 'X':[], 'Y':[], 'Visibility':[]}

    batch_size, seq_len = indices.shape[0], indices.shape[1]
    indices = indices.detach().cpu().numpy()if torch.is_tensor(indices) else indices.numpy()

    # Transform input for heatmap prediction
    if y_pred is not None:
        y_pred = y_pred > 0.5
        y_pred = y_pred.detach().cpu().numpy() if torch.is_tensor(y_pred) else y_pred
        y_pred = to_img_format(y_pred) # (N, L, H, W)

    # Transform input for coordinate prediction
    if c_pred is not None:
        c_pred = c_pred.detach().cpu().numpy() if torch.is_tensor(c_pred) else c_pred

    prev_f_i = -1
    for n in range(batch_size):
        for f in range(seq_len):
            f_i = indices[n][f][1]
            if f_i != prev_f_i:
                if c_pred is not None:
                    # Predict from coordinate
                    c_p = c_pred[n][f]
                    cx_pred, cy_pred = int(c_p[0] * WIDTH * img_scaler[0]), int(c_p[1] * HEIGHT* img_scaler[1])
                elif y_pred is not None:
                    # Predict from heatmap
                    y_p = y_pred[n][f]
                    bbox_pred = predict_location(to_img(y_p))
                    cx_pred, cy_pred = int(bbox_pred[0]+bbox_pred[2]/2), int(bbox_pred[1]+bbox_pred[3]/2)
                    cx_pred, cy_pred = int(cx_pred*img_scaler[0]), int(cy_pred*img_scaler[1])
                else:
                    raise ValueError('Invalid input')
                vis_pred = 0 if cx_pred == 0 and cy_pred == 0 else 1
                pred_dict['Frame'].append(int(f_i))
                pred_dict['X'].append(cx_pred)
                pred_dict['Y'].append(cy_pred)
                pred_dict['Visibility'].append(vis_pred)
                prev_f_i = f_i
            else:
                break

    return pred_dict


def run_prediction(video_file, tracknet_file, inpaintnet_file='', batch_size=16, eval_mode='weight', large_video=True, max_sample_num=1800, video_range=None, device=None):
    """ Run TrackNet (+ optional InpaintNet refinement) prediction on a video.

        Extracted from upstream `predict.py`'s `__main__` block. Edits vs
        upstream are enumerated in CONTRACT.md ("infer.py edits vs upstream").

        Args:
            video_file (str): file path of the video
            tracknet_file (str): file path of the TrackNet model checkpoint
            inpaintnet_file (str, optional): file path of the InpaintNet model checkpoint
            batch_size (int, optional): batch size for inference
            eval_mode (str, optional): evaluation mode ('nonoverlap', 'average', 'weight')
            large_video (bool, optional): whether to process large video
            max_sample_num (int, optional): maximum number of frames to sample for generating median image
            video_range (Tuple, optional): range of start second and end second of the video for generating median image
            device (str, optional): torch device to run inference on; defaults to cuda if available else cpu

        Returns:
            pred_dict (Dict): dictionary of predicted coordinates
                Format: {'Frame':[], 'X':[], 'Y':[], 'Visibility':[]}
    """
    device = device or ('cuda' if torch.cuda.is_available() else 'cpu')

    video_file = video_file
    video_range = video_range if video_range else None
    large_video = large_video

    # Load model
    tracknet_ckpt = torch.load(tracknet_file, map_location=device, weights_only=False)
    tracknet_seq_len = tracknet_ckpt['param_dict']['seq_len']
    bg_mode = tracknet_ckpt['param_dict']['bg_mode']
    tracknet = get_model('TrackNet', tracknet_seq_len, bg_mode).to(device)
    tracknet.load_state_dict(tracknet_ckpt['model'])

    if inpaintnet_file:
        inpaintnet_ckpt = torch.load(inpaintnet_file, map_location=device, weights_only=False)
        inpaintnet_seq_len = inpaintnet_ckpt['param_dict']['seq_len']
        inpaintnet = get_model('InpaintNet').to(device)
        inpaintnet.load_state_dict(inpaintnet_ckpt['model'])
    else:
        inpaintnet = None

    cap = cv2.VideoCapture(video_file)
    w, h = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    w_scaler, h_scaler = w / WIDTH, h / HEIGHT
    img_scaler = (w_scaler, h_scaler)

    tracknet_pred_dict = {'Frame':[], 'X':[], 'Y':[], 'Visibility':[], 'Inpaint_Mask':[],
                        'Img_scaler': (w_scaler, h_scaler), 'Img_shape': (w, h)}

    # Test on TrackNet
    tracknet.eval()
    seq_len = tracknet_seq_len
    if eval_mode == 'nonoverlap':
        # Create dataset with non-overlap sampling
        if large_video:
            dataset = Video_IterableDataset(video_file, seq_len=seq_len, sliding_step=seq_len, bg_mode=bg_mode,
                                            max_sample_num=max_sample_num, video_range=video_range)
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, drop_last=False)
            print(f'Video length: {dataset.video_len}')
        else:
            # Sample all frames from video
            frame_list = generate_frames(video_file)
            dataset = Shuttlecock_Trajectory_Dataset(seq_len=seq_len, sliding_step=seq_len, data_mode='heatmap', bg_mode=bg_mode,
                                                 frame_arr=np.array(frame_list)[:, :, :, ::-1], padding=True)
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, drop_last=False)

        for step, (i, x) in enumerate(tqdm(data_loader)):
            x = x.float().to(device)
            with torch.no_grad():
                y_pred = tracknet(x).detach().cpu()

            # Predict
            tmp_pred = predict(i, y_pred=y_pred, img_scaler=img_scaler)
            for key in tmp_pred.keys():
                tracknet_pred_dict[key].extend(tmp_pred[key])
    else:
        # Create dataset with overlap sampling for temporal ensemble
        if large_video:
            dataset = Video_IterableDataset(video_file, seq_len=seq_len, sliding_step=1, bg_mode=bg_mode,
                                            max_sample_num=max_sample_num, video_range=video_range)
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, drop_last=False)
            video_len = dataset.video_len
            print(f'Video length: {video_len}')

        else:
            # Sample all frames from video
            frame_list = generate_frames(video_file)
            dataset = Shuttlecock_Trajectory_Dataset(seq_len=seq_len, sliding_step=1, data_mode='heatmap', bg_mode=bg_mode,
                                                 frame_arr=np.array(frame_list)[:, :, :, ::-1])
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, drop_last=False)
            video_len = len(frame_list)

        # Init prediction buffer params
        num_sample, sample_count = video_len-seq_len+1, 0
        buffer_size = seq_len - 1
        batch_i = torch.arange(seq_len) # [0, 1, 2, 3, 4, 5, 6, 7]
        frame_i = torch.arange(seq_len-1, -1, -1) # [7, 6, 5, 4, 3, 2, 1, 0]
        y_pred_buffer = torch.zeros((buffer_size, seq_len, HEIGHT, WIDTH), dtype=torch.float32)
        weight = get_ensemble_weight(seq_len, eval_mode)
        for step, (i, x) in enumerate(tqdm(data_loader)):
            x = x.float().to(device)
            b_size, seq_len = i.shape[0], i.shape[1]
            with torch.no_grad():
                y_pred = tracknet(x).detach().cpu()

            y_pred_buffer = torch.cat((y_pred_buffer, y_pred), dim=0)
            ensemble_i = torch.empty((0, 1, 2), dtype=torch.float32)
            ensemble_y_pred = torch.empty((0, 1, HEIGHT, WIDTH), dtype=torch.float32)

            for b in range(b_size):
                if sample_count < buffer_size:
                    # Imcomplete buffer
                    y_pred = y_pred_buffer[batch_i+b, frame_i].sum(0) / (sample_count+1)
                else:
                    # General case
                    y_pred = (y_pred_buffer[batch_i+b, frame_i] * weight[:, None, None]).sum(0)

                ensemble_i = torch.cat((ensemble_i, i[b][0].reshape(1, 1, 2)), dim=0)
                ensemble_y_pred = torch.cat((ensemble_y_pred, y_pred.reshape(1, 1, HEIGHT, WIDTH)), dim=0)
                sample_count += 1

                if sample_count == num_sample:
                    # Last batch
                    y_zero_pad = torch.zeros((buffer_size, seq_len, HEIGHT, WIDTH), dtype=torch.float32)
                    y_pred_buffer = torch.cat((y_pred_buffer, y_zero_pad), dim=0)

                    for f in range(1, seq_len):
                        # Last input sequence
                        y_pred = y_pred_buffer[batch_i+b+f, frame_i].sum(0) / (seq_len-f)
                        ensemble_i = torch.cat((ensemble_i, i[-1][f].reshape(1, 1, 2)), dim=0)
                        ensemble_y_pred = torch.cat((ensemble_y_pred, y_pred.reshape(1, 1, HEIGHT, WIDTH)), dim=0)

            # Predict
            tmp_pred = predict(ensemble_i, y_pred=ensemble_y_pred, img_scaler=img_scaler)
            for key in tmp_pred.keys():
                tracknet_pred_dict[key].extend(tmp_pred[key])

            # Update buffer, keep last predictions for ensemble in next iteration
            y_pred_buffer = y_pred_buffer[-buffer_size:]

    #assert video_len == len(tracknet_pred_dict['Frame']), 'Prediction length mismatch'
    # Test on TrackNetV3 (TrackNet + InpaintNet)
    if inpaintnet is not None:
        inpaintnet.eval()
        seq_len = inpaintnet_seq_len
        tracknet_pred_dict['Inpaint_Mask'] = generate_inpaint_mask(tracknet_pred_dict, th_h=h*0.05)
        inpaint_pred_dict = {'Frame':[], 'X':[], 'Y':[], 'Visibility':[]}

        if eval_mode == 'nonoverlap':
            # Create dataset with non-overlap sampling
            dataset = Shuttlecock_Trajectory_Dataset(seq_len=seq_len, sliding_step=seq_len, data_mode='coordinate', pred_dict=tracknet_pred_dict, padding=True)
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, drop_last=False)

            for step, (i, coor_pred, inpaint_mask) in enumerate(tqdm(data_loader)):
                coor_pred, inpaint_mask = coor_pred.float(), inpaint_mask.float()
                with torch.no_grad():
                    coor_inpaint = inpaintnet(coor_pred.to(device), inpaint_mask.to(device)).detach().cpu()
                    coor_inpaint = coor_inpaint * inpaint_mask + coor_pred * (1-inpaint_mask) # replace predicted coordinates with inpainted coordinates

                # Thresholding
                th_mask = ((coor_inpaint[:, :, 0] < COOR_TH) & (coor_inpaint[:, :, 1] < COOR_TH))
                coor_inpaint[th_mask] = 0.

                # Predict
                tmp_pred = predict(i, c_pred=coor_inpaint, img_scaler=img_scaler)
                for key in tmp_pred.keys():
                    inpaint_pred_dict[key].extend(tmp_pred[key])

        else:
            # Create dataset with overlap sampling for temporal ensemble
            dataset = Shuttlecock_Trajectory_Dataset(seq_len=seq_len, sliding_step=1, data_mode='coordinate', pred_dict=tracknet_pred_dict)
            data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, drop_last=False)
            weight = get_ensemble_weight(seq_len, eval_mode)

            # Init buffer params
            num_sample, sample_count = len(dataset), 0
            buffer_size = seq_len - 1
            batch_i = torch.arange(seq_len) # [0, 1, 2, 3, 4, 5, 6, 7]
            frame_i = torch.arange(seq_len-1, -1, -1) # [7, 6, 5, 4, 3, 2, 1, 0]
            coor_inpaint_buffer = torch.zeros((buffer_size, seq_len, 2), dtype=torch.float32)

            for step, (i, coor_pred, inpaint_mask) in enumerate(tqdm(data_loader)):
                coor_pred, inpaint_mask = coor_pred.float(), inpaint_mask.float()
                b_size = i.shape[0]
                with torch.no_grad():
                    coor_inpaint = inpaintnet(coor_pred.to(device), inpaint_mask.to(device)).detach().cpu()
                    coor_inpaint = coor_inpaint * inpaint_mask + coor_pred * (1-inpaint_mask)

                # Thresholding
                th_mask = ((coor_inpaint[:, :, 0] < COOR_TH) & (coor_inpaint[:, :, 1] < COOR_TH))
                coor_inpaint[th_mask] = 0.

                coor_inpaint_buffer = torch.cat((coor_inpaint_buffer, coor_inpaint), dim=0)
                ensemble_i = torch.empty((0, 1, 2), dtype=torch.float32)
                ensemble_coor_inpaint = torch.empty((0, 1, 2), dtype=torch.float32)

                for b in range(b_size):
                    if sample_count < buffer_size:
                        # Imcomplete buffer
                        coor_inpaint = coor_inpaint_buffer[batch_i+b, frame_i].sum(0)
                        coor_inpaint /= (sample_count+1)
                    else:
                        # General case
                        coor_inpaint = (coor_inpaint_buffer[batch_i+b, frame_i] * weight[:, None]).sum(0)

                    ensemble_i = torch.cat((ensemble_i, i[b][0].view(1, 1, 2)), dim=0)
                    ensemble_coor_inpaint = torch.cat((ensemble_coor_inpaint, coor_inpaint.view(1, 1, 2)), dim=0)
                    sample_count += 1

                    if sample_count == num_sample:
                        # Last input sequence
                        coor_zero_pad = torch.zeros((buffer_size, seq_len, 2), dtype=torch.float32)
                        coor_inpaint_buffer = torch.cat((coor_inpaint_buffer, coor_zero_pad), dim=0)

                        for f in range(1, seq_len):
                            coor_inpaint = coor_inpaint_buffer[batch_i+b+f, frame_i].sum(0)
                            coor_inpaint /= (seq_len-f)
                            ensemble_i = torch.cat((ensemble_i, i[-1][f].view(1, 1, 2)), dim=0)
                            ensemble_coor_inpaint = torch.cat((ensemble_coor_inpaint, coor_inpaint.view(1, 1, 2)), dim=0)

                # Thresholding
                th_mask = ((ensemble_coor_inpaint[:, :, 0] < COOR_TH) & (ensemble_coor_inpaint[:, :, 1] < COOR_TH))
                ensemble_coor_inpaint[th_mask] = 0.

                # Predict
                tmp_pred = predict(ensemble_i, c_pred=ensemble_coor_inpaint, img_scaler=img_scaler)
                for key in tmp_pred.keys():
                    inpaint_pred_dict[key].extend(tmp_pred[key])

                # Update buffer, keep last predictions for ensemble in next iteration
                coor_inpaint_buffer = coor_inpaint_buffer[-buffer_size:]

    return inpaint_pred_dict if inpaintnet is not None else tracknet_pred_dict
