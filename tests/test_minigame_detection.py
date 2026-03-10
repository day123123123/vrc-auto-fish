import queue
import threading
import unittest

from core.minigame_detection import DetectionResult, MinigameDetectionService


class FakeDetector:
    def __init__(self):
        self._use_cuda = False
        self._last_best_key = "fish_blue"
        self._last_scale = 1.1

    def prepare_gray(self, _screen, search_region=None, upload_gpu=False):
        return None, 0, 0

    def find_multiscale(self, _screen, tmpl_key, _threshold, search_region=None,
                        scales=None, pre_gray=None, pre_offset=None):
        if tmpl_key == "bar":
            return (10, 20, 12, 40, 0.8)
        if tmpl_key == "track":
            return (8, 18, 16, 80, 0.7)
        if tmpl_key == "fish_blue":
            return (11, 15, 10, 10, 0.9)
        return None

    def find_fish(self, _screen, _threshold, search_region=None,
                  pre_gray=None, pre_offset=None, keys=None):
        return (11, 15, 10, 10, 0.9)


class FakeYolo:
    def detect(self, _screen, roi=None):
        return {
            "fish": (1, 2, 3, 4, 0.9),
            "bar": (5, 6, 7, 8, 0.9),
            "track": (0, 0, 10, 10, 0.9),
            "progress": (9, 10, 11, 12, 0.9),
        }


class DetectionServiceTests(unittest.TestCase):
    def make_service(self, yolo=None):
        pd = type("PD", (), {"fish_smooth_cy": None})()
        return MinigameDetectionService(
            FakeDetector(),
            pd,
            lambda: yolo,
            lambda: None,
        )

    def test_detect_frame_returns_structured_result_for_yolo(self):
        service = self.make_service(FakeYolo())
        result = service.detect_frame(
            scr=None,
            use_yolo=True,
            search_region=None,
            bar_search_region=None,
            locked_fish_key=None,
            locked_fish_scales=None,
            locked_bar_scales=None,
            frame_no=1,
            yolo_roi=None,
            skip_success=False,
        )
        self.assertIsInstance(result, DetectionResult)
        self.assertIsNotNone(result.fish)
        self.assertIsNotNone(result.progress)

    def test_detect_once_keeps_legacy_tuple_contract(self):
        service = self.make_service(FakeYolo())
        result = service.detect_once(
            scr=None,
            use_yolo=True,
            search_region=None,
            bar_search_region=None,
            locked_fish_key=None,
            locked_fish_scales=None,
            locked_bar_scales=None,
            frame_no=1,
            yolo_roi=None,
            skip_success=False,
        )
        self.assertEqual(len(result), 7)

    def test_detect_worker_loop_pushes_latest_result(self):
        service = self.make_service(FakeYolo())
        frame_q = queue.Queue()
        result_q = queue.Queue(maxsize=1)
        stop_evt = threading.Event()
        params_lock = threading.Lock()
        shared_params = {
            "search_region": None,
            "bar_search_region": None,
            "locked_fish_key": None,
            "locked_fish_scales": None,
            "locked_bar_scales": None,
            "frame": 1,
            "yolo_roi": None,
            "skip_success": False,
        }
        frame_q.put(("raw", "rotated"))
        worker = threading.Thread(
            target=service.detect_worker_loop,
            args=(frame_q, result_q, stop_evt, shared_params, params_lock, True),
            daemon=True,
        )
        worker.start()
        payload = result_q.get(timeout=1.0)
        stop_evt.set()
        worker.join(timeout=1.0)
        self.assertEqual(payload[0], "raw")
        self.assertIsNotNone(payload[2])


if __name__ == "__main__":
    unittest.main()
