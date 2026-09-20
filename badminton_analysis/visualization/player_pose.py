import time

import cv2
import numpy as np

from ..detection.rtmpose import RTMPoseProcessor
from .skeleton import SKELETON_CONNECTIONS, draw_skeleton


class PlayerPoseVisualizer:
    """Detect, filter, and draw player pose keypoints."""

    def __init__(
        self,
        rtmpose_processor=None,
        show_skeletons=True,
        show_player_trajectories=True,
        show_performance_stats=False,
        court_filter_margin=0.75,
    ):
        self.rtmpose_processor = rtmpose_processor or RTMPoseProcessor()
        self.show_skeletons = show_skeletons
        self.show_player_trajectories = show_player_trajectories
        self.show_performance_stats = show_performance_stats
        self.current_pose_data = None
        self.court_mapper = None
        self.court_filter_margin = court_filter_margin

        self.skeleton_connections = SKELETON_CONNECTIONS

    def detect_players(self, roi, x1, y1, court_mapper=None):
        centroids = []
        point_left_hands = {}
        point_right_hands = {}

        t0 = time.time()
        keypoints_all, _confidence_scores = self.rtmpose_processor.process_frame(roi)
        if self.show_performance_stats:
            inference_name = getattr(self.rtmpose_processor, "inference_name", "Pose")
            print(f"{inference_name} inference took {time.time() - t0:.2f} sec")

        if keypoints_all is None:
            self.current_pose_data = None
            return centroids, point_left_hands, point_right_hands

        persons = self._normalize_people(keypoints_all)
        filtered_people = []
        active_court_mapper = court_mapper or self.court_mapper

        for kp in persons:
            kp_arr = np.asarray(kp)
            if kp_arr.ndim != 2 or kp_arr.shape[0] < 17 or kp_arr.shape[1] < 2:
                continue

            lf = kp_arr[15]
            rf = kp_arr[16]
            if lf[0] <= 1 or lf[1] <= 1 or rf[0] <= 1 or rf[1] <= 1:
                continue

            mid_point = (
                (float(lf[0] + x1) + float(rf[0] + x1)) / 2,
                (float(lf[1] + y1) + float(rf[1] + y1)) / 2 + 10,
            )
            if not self._is_on_court(mid_point, active_court_mapper):
                continue

            filtered_people.append(kp_arr)
            centroids.append(mid_point)

            lh = kp_arr[9]
            rh = kp_arr[10]
            if lh[0] > 1 and lh[1] > 1:
                point_left_hands[mid_point[1]] = (int(lh[0] + x1), int(lh[1] + y1))
            if rh[0] > 1 and rh[1] > 1:
                point_right_hands[mid_point[1]] = (int(rh[0] + x1), int(rh[1] + y1))

        if filtered_people:
            self.current_pose_data = {
                "keypoints": np.asarray(filtered_people),
                "offset_x": x1,
                "offset_y": y1,
            }
        else:
            self.current_pose_data = None

        return centroids, point_left_hands, point_right_hands

    def detect_far_players(self, frame, roi, court_mapper=None, imgsz=None,
                           net_court_y=6.7, static_filter=None, frame_index=0,
                           main_offset=(0, 0)):
        """Second pose pass over the far half of the court.

        The whole-frame pass cannot see the far player at all on wide framing
        -- 0 of 36,732 frames on the 0007 run -- because letterboxing a
        3840-wide frame to 640 shrinks a 100-155 px body to 17-26 px. Cropping
        to the far half first and inferring at a size matched to the crop finds
        them in 8 of 8 sampled frames for less cost than a big whole-frame
        pass (see badminton_analysis.court.far_roi).

        Only candidates in the UPPER court half are returned. That is what
        keeps this additive: the near player is already handled by the
        whole-frame pass, and admitting near-half candidates from this crop
        would let the people sitting courtside just past the net outrank the
        real player under the tracker's "closest to the net" rule.

        ``static_filter`` drops candidates that have not moved, which is the
        remaining defence against courtside bystanders inside the far half.

        Returns the same (centroids, left_hands, right_hands) triple as
        detect_players, for merging by the caller.
        """
        centroids, point_left_hands, point_right_hands = [], {}, {}
        if roi is None or frame is None:
            return centroids, point_left_hands, point_right_hands

        x0, y0, x1, y1 = (int(v) for v in roi)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return centroids, point_left_hands, point_right_hands

        keypoints_all, _scores = self.rtmpose_processor.process_frame(crop, imgsz=imgsz)
        if keypoints_all is None:
            return centroids, point_left_hands, point_right_hands

        active_court_mapper = court_mapper or self.court_mapper
        if active_court_mapper is None:
            # Without a mapper there is no upper/lower test, and every person
            # the crop contains -- including the next court's -- would be
            # returned as a far player. Skipping is the safe failure.
            return centroids, point_left_hands, point_right_hands

        accepted = []
        for kp in self._normalize_people(keypoints_all):
            kp_arr = np.asarray(kp)
            if kp_arr.ndim != 2 or kp_arr.shape[0] < 17 or kp_arr.shape[1] < 2:
                continue
            lf, rf = kp_arr[15], kp_arr[16]
            if lf[0] <= 1 or lf[1] <= 1 or rf[0] <= 1 or rf[1] <= 1:
                continue
            mid_point = (
                (float(lf[0]) + float(rf[0])) / 2 + x0,
                (float(lf[1]) + float(rf[1])) / 2 + y0 + 10,
            )
            if not self._is_on_court(mid_point, active_court_mapper):
                continue
            court = active_court_mapper.image_to_court(mid_point)
            if court is None or len(court) < 2 or float(court[1]) >= net_court_y:
                continue
            accepted.append((mid_point, kp_arr))

        if static_filter is not None and accepted:
            # Index-carrying points, so the surviving candidates are matched
            # back by position in the list rather than by object identity.
            tagged = [(p[0], p[1], i) for i, (p, _kp) in enumerate(accepted)]
            kept = {t[2] for t in static_filter.filter(frame_index, tagged)}
            accepted = [item for i, item in enumerate(accepted) if i in kept]

        far_people = []
        for mid_point, kp_arr in accepted:
            centroids.append(mid_point)
            far_people.append(kp_arr)
            lh, rh = kp_arr[9], kp_arr[10]
            if lh[0] > 1 and lh[1] > 1:
                point_left_hands[mid_point[1]] = (int(lh[0] + x0), int(lh[1] + y0))
            if rh[0] > 1 and rh[1] > 1:
                point_right_hands[mid_point[1]] = (int(rh[0] + x0), int(rh[1] + y0))

        if far_people:
            self._merge_pose_data(far_people, (x0, y0), main_offset)
        return centroids, point_left_hands, point_right_hands

    def _merge_pose_data(self, far_people, far_offset, main_offset):
        """Add far-pass keypoints to current_pose_data so they get drawn.

        current_pose_data carries ONE offset for all its keypoints, so the far
        crop's coordinates are rebased onto the main pass's offset rather than
        stored with their own.
        """
        shift_x = far_offset[0] - main_offset[0]
        shift_y = far_offset[1] - main_offset[1]
        rebased = []
        for kp in far_people:
            moved = np.array(kp, dtype=float, copy=True)
            valid = (moved[:, 0] > 1) & (moved[:, 1] > 1)
            moved[valid, 0] += shift_x
            moved[valid, 1] += shift_y
            rebased.append(moved)

        if self.current_pose_data is None:
            self.current_pose_data = {
                "keypoints": np.asarray(rebased),
                "offset_x": main_offset[0],
                "offset_y": main_offset[1],
            }
            return
        existing = np.asarray(self.current_pose_data["keypoints"], dtype=float)
        self.current_pose_data["keypoints"] = np.concatenate(
            [existing, np.asarray(rebased, dtype=float)], axis=0)

    def _normalize_people(self, keypoints):
        if isinstance(keypoints, np.ndarray):
            if keypoints.ndim == 2:
                return [keypoints]
            if keypoints.ndim == 3:
                return [keypoints[i] for i in range(keypoints.shape[0])]
            return []
        if isinstance(keypoints, (list, tuple)):
            return list(keypoints)
        return []

    def _is_on_court(self, image_point, court_mapper):
        if court_mapper is None:
            return True
        court_position = court_mapper.image_to_court(image_point)
        if court_position is None or len(court_position) < 2:
            return False
        x, y = float(court_position[0]), float(court_position[1])
        margin = self.court_filter_margin
        return -margin <= x <= 6.1 + margin and -margin <= y <= 13.4 + margin

    def draw_players(self, frame, player_tracker, cached_movement_stats, stats_visualizer=None, rally_count=0):
        if self.show_skeletons and self.current_pose_data is not None:
            t0 = time.time()
            self._draw_skeleton_on_frame(
                frame,
                self.current_pose_data["keypoints"],
                self.current_pose_data["offset_x"],
                self.current_pose_data["offset_y"],
            )
            if self.show_performance_stats:
                print(f"Drawing skeleton took {time.time() - t0:.2f} sec")

        t0 = time.time()
        for position in ["upper", "lower"]:
            if player_tracker.players[position] is None:
                continue

            color = (0, 255, 255) if position == "upper" else (255, 0, 255)
            cv2.circle(frame, tuple(map(int, player_tracker.players[position])), 5, color, -1, cv2.LINE_AA)

            if self.show_player_trajectories:
                history = list(player_tracker.history[position])
                for i, pos in enumerate(history):
                    if pos is None:
                        continue
                    radius = int(2 + (i / len(history)) * 3) if history else 2
                    cv2.circle(frame, tuple(map(int, pos)), radius, color, -1, cv2.LINE_AA)

        if self.show_performance_stats:
            print(f"Drawing players and trajectories took {time.time() - t0:.2f} sec")

        if stats_visualizer is not None:
            t0 = time.time()
            stats_visualizer.draw_player_stats(frame, cached_movement_stats, rally_count)
            if self.show_performance_stats:
                print(f"Drawing player stats took {time.time() - t0:.2f} sec")

    def _draw_skeleton_on_frame(self, frame, keypoints, offset_x, offset_y):
        for person in self._normalize_people(keypoints):
            person_arr = np.asarray(person, dtype=float)
            if person_arr.ndim != 2 or person_arr.shape[1] < 2:
                continue
            shifted = person_arr.copy()
            # shift only present points (leave the <=1 "missing" sentinel untouched)
            present = ~((shifted[:, 0] <= 1) & (shifted[:, 1] <= 1))
            shifted[present, 0] += offset_x
            shifted[present, 1] += offset_y
            draw_skeleton(frame, shifted)

    def get_current_pose_data(self):
        return self.current_pose_data
