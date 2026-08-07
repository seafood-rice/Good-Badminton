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
