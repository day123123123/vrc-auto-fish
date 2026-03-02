"""
图像识别模块
============
基于 OpenCV 模板匹配 + 多尺度搜索 + 颜色检测。

改进:
- 多尺度匹配: 针对不同分辨率 / DPI 缩放自动尝试多个比例
- 灰度匹配: 降低颜色偏差影响
- 调试报告: debug 模式下打印最佳置信度（即使低于阈值）
"""

import cv2
import numpy as np
import os

import config
from utils.logger import log


class ImageDetector:
    """图像检测器"""

    def __init__(self, img_dir: str, template_files: dict):
        self.templates = {}
        self.templates_gray = {}
        self.debug_report = False          # 由 bot 设置
        self._last_scale = 1.0             # 最近一次 find_multiscale 的命中缩放
        self._last_best_key = None         # 最近一次 find_best 的最佳 key
        self._last_best_scale = 1.0        # 最近一次 find_best 的最佳缩放
        self._use_cuda = False
        self._cuda_matcher = None
        self._scaled_cache = {}
        self._gpu_scaled_cache = {}
        self._load_templates(img_dir, template_files)
        self._init_gpu()

    # ══════════════════ 模板加载 ══════════════════

    _TMPL_MAX_DIM = 9999  # 禁用裁剪: 保留完整模板以提高匹配准确度

    def _load_templates(self, img_dir: str, file_map: dict):
        pass  # 静默加载模板
        for key, fname in file_map.items():
            path = os.path.join(img_dir, fname)
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                orig_desc = f"{w}×{h}"
                # 超长/超宽模板: 中心裁剪到 _TMPL_MAX_DIM
                mx = self._TMPL_MAX_DIM
                if h > mx:
                    cy = h // 2
                    y0 = cy - mx // 2
                    img = img[y0:y0 + mx, :, :]
                    h = mx
                if w > mx:
                    cx = w // 2
                    x0 = cx - mx // 2
                    img = img[:, x0:x0 + mx, :]
                    w = mx
                if orig_desc != f"{w}×{h}":
                    pass
                else:
                    pass
                self.templates[key] = img
                self.templates_gray[key] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                self.templates[key] = None
                self.templates_gray[key] = None
                log.warning(f"  ✗ {fname:<15s}  (未找到)")

    # ══════════════════ GPU / CUDA ══════════════════

    def _init_gpu(self):
        """尝试启用 CUDA 加速; 不可用时回退 CPU"""
        self._use_cuda = False
        self._cuda_matcher = None
        self._gpu_templates = {}
        cv2.ocl.setUseOpenCL(False)

        try:
            if hasattr(cv2, 'cuda') and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                self._cuda_matcher = cv2.cuda.createTemplateMatching(
                    cv2.CV_8U, cv2.TM_CCOEFF_NORMED
                )
                _t = np.zeros((32, 32), dtype=np.uint8)
                _s = np.zeros((8, 8), dtype=np.uint8)
                self._cuda_matcher.match(
                    cv2.cuda_GpuMat(_t), cv2.cuda_GpuMat(_s)
                )
                self._use_cuda = True
                for key, tmpl in self.templates_gray.items():
                    if tmpl is not None:
                        self._gpu_templates[key] = cv2.cuda_GpuMat(tmpl)
                dev = cv2.cuda.getDevice()
                dev_info = cv2.cuda.DeviceInfo(dev)
                vram_mb = dev_info.totalMemory() // 1048576
                log.info(f"[引擎] ✓ CUDA 已启用: GPU #{dev} ({vram_mb} MB)")
                log.info(
                    f"  GPU模板缓存: {len(self._gpu_templates)} 个"
                )
                return
        except Exception as e:
            self._use_cuda = False
            self._cuda_matcher = None
            self._gpu_templates = {}
            log.debug(f"[引擎] CUDA 初始化失败: {e}")

        pass  # 静默 CPU 模式

    _CUDA_MIN_PIXELS = 50_000

    def _match_template(self, img_gray, tmpl_gray):
        """模板匹配核心 — CPU 路径"""
        result = cv2.matchTemplate(img_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return max_val, max_loc

    def _cuda_match(self, gpu_img, gpu_tmpl):
        """CUDA 路径 — 参数已在 GPU 上"""
        gpu_result = self._cuda_matcher.match(gpu_img, gpu_tmpl)
        result_cpu = gpu_result.download()
        _, max_val, _, max_loc = cv2.minMaxLoc(result_cpu)
        return max_val, max_loc

    def _should_use_cuda(self, h, w):
        """图像 >= _CUDA_MIN_PIXELS 时才走 GPU, 否则 CPU 更快"""
        return self._use_cuda and h * w >= self._CUDA_MIN_PIXELS

    # ══════════════════ 灰度缓存 ══════════════════

    _gray_cache_id = -1
    _gray_cache_img = None

    def prepare_gray(self, screen, search_region=None, upload_gpu=False):
        """预计算搜索区域的灰度图, 供同帧多次 find_multiscale 复用。
        upload_gpu=True 时返回 GpuMat (CUDA模式)。
        返回 (gray_img_or_GpuMat, ox, oy)"""
        ox, oy = 0, 0
        img = screen
        if search_region:
            rx, ry, rw, rh = int(search_region[0]), int(search_region[1]), \
                             int(search_region[2]), int(search_region[3])
            ox, oy = rx, ry
            img = screen[ry:ry + rh, rx:rx + rw]
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        if upload_gpu and self._should_use_cuda(*gray.shape[:2]):
            try:
                gray = np.ascontiguousarray(gray)
                return cv2.cuda_GpuMat(gray), ox, oy
            except Exception:
                pass
        return gray, ox, oy

    # ══════════════════ 单尺度匹配 ══════════════════

    def find(self, screen, tmpl_key: str, threshold: float = 0.6,
             search_region=None):
        """
        单尺度 (1:1) 模板匹配。
        Returns: (x, y, w, h, confidence) 或 None
        """
        tmpl = self.templates.get(tmpl_key)
        if tmpl is None:
            return None

        ox, oy = 0, 0
        img = screen
        if search_region:
            rx, ry, rw, rh = [int(v) for v in search_region]
            ox, oy = rx, ry
            img = screen[ry: ry + rh, rx: rx + rw]

        th, tw = tmpl.shape[:2]
        if img.shape[0] < th or img.shape[1] < tw:
            return None

        if len(img.shape) == 3:
            img_g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            img_g = img
        tmpl_g = self.templates_gray.get(tmpl_key)
        if tmpl_g is None:
            tmpl_g = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if len(tmpl.shape) == 3 else tmpl
        if self._should_use_cuda(*img_g.shape[:2]):
            gpu_tmpl = self._gpu_templates.get(tmpl_key)
            if gpu_tmpl is not None:
                try:
                    max_val, max_loc = self._cuda_match(
                        cv2.cuda_GpuMat(np.ascontiguousarray(img_g)),
                        gpu_tmpl)
                except Exception:
                    max_val, max_loc = self._match_template(img_g, tmpl_g)
            else:
                max_val, max_loc = self._match_template(img_g, tmpl_g)
        else:
            max_val, max_loc = self._match_template(img_g, tmpl_g)

        if max_val >= threshold:
            return (max_loc[0] + ox, max_loc[1] + oy, tw, th, max_val)

        if self.debug_report:
            log.debug(f"  {tmpl_key}: 最佳置信度 {max_val:.3f} (阈值 {threshold})")
        return None

    # ══════════════════ 多尺度匹配 ══════════════════

    def find_multiscale(self, screen, tmpl_key: str, threshold: float = 0.6,
                        search_region=None, scales=None,
                        pre_gray=None, pre_offset=None):
        """
        多尺度模板匹配。

        pre_gray / pre_offset: 由 prepare_gray() 预计算的灰度图和偏移,
        传入后跳过裁剪+灰度转换 (同一帧多次调用时避免重复计算)。
        pre_gray 可以是 numpy 数组或 cv2.cuda_GpuMat。
        """
        tmpl = self.templates_gray.get(tmpl_key)
        if tmpl is None:
            return None

        if scales is None:
            scales = config.MATCH_SCALES

        # ── 准备灰度图 ──
        gpu_img = None
        if pre_gray is not None:
            if self._use_cuda and isinstance(pre_gray, cv2.cuda.GpuMat):
                gpu_img = pre_gray
                ih, iw = gpu_img.size()[1], gpu_img.size()[0]
            else:
                img_gray = pre_gray
            ox, oy = pre_offset or (0, 0)
        else:
            ox, oy = 0, 0
            img = screen
            if search_region:
                rx, ry, rw, rh = int(search_region[0]), int(search_region[1]), \
                                 int(search_region[2]), int(search_region[3])
                ox, oy = rx, ry
                img = screen[ry:ry + rh, rx:rx + rw]
            if len(img.shape) == 3:
                img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                img_gray = img

        if gpu_img is None:
            ih, iw = img_gray.shape[:2]
            if self._should_use_cuda(ih, iw):
                try:
                    gpu_img = cv2.cuda_GpuMat(
                        np.ascontiguousarray(img_gray))
                except Exception:
                    pass

        th, tw = tmpl.shape[:2]
        best_val = 0.0
        best_match = None
        best_scale = 1.0

        # ════════════ CUDA 路径: 图像已在GPU, 模板缓存复用 ════════════
        if gpu_img is not None:
            gpu_tmpl_orig = self._gpu_templates.get(tmpl_key)

            for scale in scales:
                try:
                    if scale == 1.0:
                        if ih < th or iw < tw or gpu_tmpl_orig is None:
                            continue
                        max_val, max_loc = self._cuda_match(
                            gpu_img, gpu_tmpl_orig)
                        if max_val > best_val:
                            best_val = max_val
                            best_scale = scale
                            if max_val >= threshold:
                                best_match = (max_loc[0] + ox, max_loc[1] + oy,
                                              tw, th, max_val)

                    elif scale < 1.0:
                        new_w = int(iw * scale)
                        new_h = int(ih * scale)
                        if new_w < tw or new_h < th:
                            continue
                        gpu_img_s = cv2.cuda.resize(
                            gpu_img, (new_w, new_h),
                            interpolation=cv2.INTER_AREA)
                        if gpu_tmpl_orig is None:
                            continue
                        max_val, max_loc = self._cuda_match(
                            gpu_img_s, gpu_tmpl_orig)
                        if max_val > best_val:
                            best_val = max_val
                            best_scale = scale
                            if max_val >= threshold:
                                real_x = int(max_loc[0] / scale) + ox
                                real_y = int(max_loc[1] / scale) + oy
                                best_match = (real_x, real_y,
                                              int(tw / scale), int(th / scale),
                                              max_val)

                    else:  # scale > 1.0
                        new_tw = int(tw / scale)
                        new_th = int(th / scale)
                        if new_tw < 15 or new_th < 15:
                            continue
                        if ih < new_th or iw < new_tw:
                            continue
                        _gkey = (tmpl_key, new_tw, new_th)
                        gpu_tmpl_s = self._gpu_scaled_cache.get(_gkey)
                        if gpu_tmpl_s is None:
                            scaled_tmpl = cv2.resize(
                                tmpl, (new_tw, new_th),
                                interpolation=cv2.INTER_LINEAR)
                            scaled_tmpl = np.ascontiguousarray(scaled_tmpl)
                            try:
                                gpu_tmpl_s = cv2.cuda_GpuMat(scaled_tmpl)
                                self._gpu_scaled_cache[_gkey] = gpu_tmpl_s
                            except Exception:
                                max_val, max_loc = self._match_template(
                                    img_gray if gpu_img is None else gpu_img.download(),
                                    scaled_tmpl)
                                if max_val > best_val:
                                    best_val = max_val
                                    best_scale = scale
                                    if max_val >= threshold:
                                        best_match = (max_loc[0] + ox,
                                                      max_loc[1] + oy,
                                                      new_tw, new_th, max_val)
                                continue
                        max_val, max_loc = self._cuda_match(
                            gpu_img, gpu_tmpl_s)
                        if max_val > best_val:
                            best_val = max_val
                            best_scale = scale
                            if max_val >= threshold:
                                best_match = (max_loc[0] + ox,
                                              max_loc[1] + oy,
                                              new_tw, new_th, max_val)
                except cv2.error:
                    continue

        # ════════════ CPU 路径 ════════════
        else:
            for scale in scales:
                if scale == 1.0:
                    if ih < th or iw < tw:
                        continue
                    max_val, max_loc = self._match_template(img_gray, tmpl)
                    if max_val > best_val:
                        best_val = max_val
                        best_scale = scale
                        if max_val >= threshold:
                            best_match = (max_loc[0] + ox, max_loc[1] + oy,
                                          tw, th, max_val)

                elif scale < 1.0:
                    new_w = int(iw * scale)
                    new_h = int(ih * scale)
                    if new_w < tw or new_h < th:
                        continue
                    scaled_img = cv2.resize(img_gray, (new_w, new_h),
                                            interpolation=cv2.INTER_AREA)
                    max_val, max_loc = self._match_template(scaled_img, tmpl)
                    if max_val > best_val:
                        best_val = max_val
                        best_scale = scale
                        if max_val >= threshold:
                            real_x = int(max_loc[0] / scale) + ox
                            real_y = int(max_loc[1] / scale) + oy
                            best_match = (real_x, real_y,
                                          int(tw / scale), int(th / scale),
                                          max_val)

                else:
                    new_tw = int(tw / scale)
                    new_th = int(th / scale)
                    if new_tw < 15 or new_th < 15:
                        continue
                    if ih < new_th or iw < new_tw:
                        continue
                    _ckey = (tmpl_key, new_tw, new_th)
                    scaled_tmpl = self._scaled_cache.get(_ckey)
                    if scaled_tmpl is None:
                        scaled_tmpl = cv2.resize(
                            tmpl, (new_tw, new_th),
                            interpolation=cv2.INTER_LINEAR)
                        self._scaled_cache[_ckey] = scaled_tmpl
                    max_val, max_loc = self._match_template(
                        img_gray, scaled_tmpl)
                    if max_val > best_val:
                        best_val = max_val
                        best_scale = scale
                        if max_val >= threshold:
                            best_match = (max_loc[0] + ox, max_loc[1] + oy,
                                          new_tw, new_th, max_val)

        self._last_scale = best_scale

        if self.debug_report:
            if best_match:
                log.debug(f"  {tmpl_key}(多尺度): ✓ 置信度 {best_val:.3f} @ scale={best_scale:.2f} (阈值 {threshold})")
            else:
                log.debug(f"  {tmpl_key}(多尺度): ✗ 最佳置信度 {best_val:.3f} @ scale={best_scale:.2f} (阈值 {threshold})")

        return best_match

    # ══════════════════ 颜色检测咬钩 ══════════════════

    def detect_bite_by_color(self, screen, min_cluster: int = 400) -> bool:
        """
        通过颜色特征检测咬钩标志（感叹号）。

        感叹号有鲜艳的青蓝色边框（HSV ≈ H:85-130, S:100+, V:150+），
        是一个**完整的大聚类**图形。

        ★ 关键改进: 不再简单计数所有蓝色像素 (夜晚蓝色方块会散落满屏)，
        而是要求存在**单一大连通区域** ≥ min_cluster 像素。
        感叹号是一整块，而蓝色方块是很多分散的小方块。
        """
        h_scr, w_scr = screen.shape[:2]

        # 搜索区域: 中央区域 (感叹号出现在鱼竿/浮漂附近)
        x1 = int(w_scr * 0.25)
        x2 = int(w_scr * 0.75)
        y1 = int(h_scr * 0.05)
        y2 = int(h_scr * 0.65)
        roi = screen[y1:y2, x1:x2]

        if roi.size == 0:
            return False

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # 鲜艳青蓝色: 感叹号边框的典型颜色
        mask = cv2.inRange(hsv,
                           np.array([85, 100, 150]),
                           np.array([130, 255, 255]))

        # 形态学: 先去噪再膨胀连接近邻像素
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        # ★ 查找连通区域，只看最大的那个
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        largest_area = 0
        largest_contour = None
        for c in contours:
            area = cv2.contourArea(c)
            if area > largest_area:
                largest_area = area
                largest_contour = c

        detected = largest_area >= min_cluster

        # 额外检查: 感叹号大致是竖长形 (高 > 宽)
        if detected and largest_contour is not None:
            _, _, cw, ch = cv2.boundingRect(largest_contour)
            if cw > 0 and ch > 0:
                aspect = ch / cw
                # 感叹号高宽比 > 1.2; 方块接近 1.0
                if aspect < 1.0:
                    detected = False
                    if self.debug_report:
                        log.debug(
                            f"  颜色检测(bite): 最大聚类={largest_area} "
                            f"但形状不对(宽高比={aspect:.1f}), 可能是方块"
                        )

        total_px = int(cv2.countNonZero(mask))

        if self.debug_report:
            log.debug(
                f"  颜色检测(bite): 总像素={total_px} "
                f"最大聚类={largest_area} (阈值={min_cluster}) "
                f"→ {'✓ 检测到' if detected else '✗'}"
            )

        # 调试: 保存颜色mask
        if self.debug_report and detected:
            try:
                import config as _cfg
                path = os.path.join(_cfg.DEBUG_DIR, "bite_color_mask.png")
                cv2.imwrite(path, mask)
            except Exception:
                pass

        return detected

    # ══════════════════ 组合检测方法 ══════════════════

    def find_best(self, screen, keys: list, thresholds: list,
                  search_region=None, multiscale: bool = False):
        """尝试多个模板，返回置信度最高的匹配，并记录最佳 key/scale。
        ★ 找到首个超过阈值的匹配后立即返回（同一场景只有一种鱼）"""
        self._last_best_key = None        # ★ 重置, 防止残留
        self._last_best_scale = 1.0
        best = None
        best_conf = 0.0
        for key, thr in zip(keys, thresholds):
            if multiscale:
                m = self.find_multiscale(screen, key, thr, search_region)
            else:
                m = self.find(screen, key, thr, search_region)
            if m and m[4] > best_conf:
                best = m
                best_conf = m[4]
                self._last_best_key = key
                self._last_best_scale = self._last_scale
                break                     # ★ 找到即停, 不再遍历其余模板
        return best

    def _fish_scales_for(self, tmpl_key: str) -> list:
        """
        根据 config.FISH_GAME_SIZE 自动计算鱼模板的最佳搜索缩放比例。

        原理:
          optimal_scale = 模板尺寸 / 游戏内鱼像素
          然后在 optimal 附近生成 7 个搜索点, 覆盖约 ±40% 范围。

        例: 模板38px, FISH_GAME_SIZE=20 → optimal=1.9
            搜索: [1.14, 1.43, 1.71, 1.90, 2.09, 2.38, 2.66]
        """
        tmpl = self.templates.get(tmpl_key)
        game_size = getattr(config, 'FISH_GAME_SIZE', 0)
        if tmpl is None or game_size <= 0:
            return config.MATCH_SCALES

        h, w = tmpl.shape[:2]
        tmpl_size = max(h, w)
        optimal = tmpl_size / game_size

        scales = sorted(set(
            round(optimal * f, 2)
            for f in [0.6, 0.8, 1.0, 1.25, 1.5]
        ))
        # 限制范围: scale >= 0.3 且 <= 5.0
        scales = [s for s in scales if 0.3 <= s <= 5.0]
        return scales if scales else config.MATCH_SCALES

    def find_fish(self, screen, threshold: float, search_region=None,
                  pre_gray=None, pre_offset=None, keys=None):
        """查找鱼图标 — 遍历所有鱼模板，返回置信度最高的匹配。
        keys: 只搜索指定的鱼模板 (用于分帧轮询提速)"""
        self._last_best_key = None
        self._last_best_scale = 1.0

        best_match = None
        best_conf = 0.0
        best_key = None
        best_scale = 1.0

        for k in (keys or config.FISH_KEYS):
            thr = max(threshold, 0.75) if k == "fish_white" else threshold
            scales = self._fish_scales_for(k)
            m = self.find_multiscale(
                screen, k, thr, search_region, scales=scales,
                pre_gray=pre_gray, pre_offset=pre_offset,
            )
            if m and m[4] > best_conf:
                best_conf = m[4]
                best_match = m
                best_key = k
                best_scale = self._last_scale

        if best_match is not None:
            self._last_best_key = best_key
            self._last_best_scale = best_scale
        return best_match

    def identify_fish_type(self, screen, fish_box, debug_save=False):
        """YOLO 给位置后, 分析鱼框内中心区域像素颜色判定鱼种。
        只取框内中心 70% + 高饱和度像素, 用色相直方图找主色。"""
        import os
        import numpy as np
        fx, fy, fw, fh = fish_box[:4]
        h_img, w_img = screen.shape[:2]
        # 只取 YOLO 框中心 70%, 排除边缘背景
        mx = int(fw * 0.15)
        my = int(fh * 0.15)
        x1 = max(0, fx + mx)
        y1 = max(0, fy + my)
        x2 = min(w_img, fx + fw - mx)
        y2 = min(h_img, fy + fh - my)
        if x2 - x1 < 3 or y2 - y1 < 3:
            x1, y1 = max(0, fx), max(0, fy)
            x2, y2 = min(w_img, fx + fw), min(h_img, fy + fh)
        crop = screen[y1:y2, x1:x2]
        if crop.size == 0:
            return "fish_golden"

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h_ch = hsv[:, :, 0].flatten()
        s_ch = hsv[:, :, 1].flatten()
        v_ch = hsv[:, :, 2].flatten()

        mask = (s_ch > 70) & (v_ch > 50)
        n_sat = int(mask.sum())

        if n_sat < 5:
            v_mean = float(v_ch.mean())
            result = "fish_white" if v_mean > 130 else "fish_black"
        else:
            h_fish = h_ch[mask]
            red_count = int(np.sum((h_fish < 12) | (h_fish > 165)))
            h_dom = -1
            if red_count > n_sat * 0.35:
                result = "fish_red"
            else:
                hist, _ = np.histogram(h_fish, bins=18, range=(0, 180))
                peak = int(np.argmax(hist))
                h_dom = peak * 10 + 5
                if h_dom < 15 or h_dom > 165:
                    result = "fish_red"
                elif h_dom < 25:
                    result = "fish_copper"
                elif h_dom < 40:
                    result = "fish_golden"
                elif h_dom < 80:
                    result = "fish_green"
                elif h_dom < 115:
                    result = "fish_blue"
                elif h_dom < 140:
                    result = "fish_purple"
                elif h_dom < 165:
                    result = "fish_pink"
                else:
                    result = "fish_rainbow"

        if debug_save:
            full_crop = screen[max(0, fy):min(h_img, fy + fh),
                               max(0, fx):min(w_img, fx + fw)]
            dbg = full_crop.copy() if full_crop.size > 0 else crop.copy()
            info = f"{result} sat={n_sat} h={h_dom if n_sat >= 5 else -1}"
            cv2.putText(dbg, info, (2, dbg.shape[0] - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
            debug_dir = os.path.join(config.BASE_DIR, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            cv2.imwrite(os.path.join(debug_dir, "fish_id_crop.png"), dbg)

        return result

    def find_fish_by_color(self, screen, search_region=None,
                           bar_cx=None):
        """
        颜色检测鱼的位置。

        鱼是小型像素精灵，具有鲜艳的饱和色（绿/金/铜/蓝/紫），
        在轨道背景（暗色或浅色）上非常突出。

        ★ 如果传入 bar_cx (白条中心X)，会进一步收窄搜索到轨道附近，
           大幅减少海面/背景误识别。
        """
        if search_region is None:
            return None

        rx, ry, rw, rh = [int(v) for v in search_region]

        # ★ 如果已知白条位置，收窄到白条 ±40px (轨道宽度约30-50px)
        if bar_cx is not None:
            strip_half = 40
            new_rx = max(0, int(bar_cx) - strip_half)
            new_rw = min(strip_half * 2, screen.shape[1] - new_rx)
            # 取与原搜索区域的交集
            left = max(rx, new_rx)
            right = min(rx + rw, new_rx + new_rw)
            if right > left:
                rx, rw = left, right - left

        roi = screen[ry:ry + rh, rx:rx + rw]
        if roi.size == 0:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # ★ 更严格的饱和色过滤 (S>=80, V>=80)
        # 鱼精灵颜色非常鲜艳, 海面反光 S 通常较低
        mask = cv2.inRange(hsv,
                           np.array([0, 80, 80]),
                           np.array([180, 255, 255]))

        # 形态学去噪
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        # 查找轮廓
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # ★ 筛选: 面积适中 + 大致方形 (非长条)
        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if 50 < area < 4000:     # ★ 最小面积从25提到50
                bx, by, bw, bh = cv2.boundingRect(c)
                aspect = max(bw, bh) / max(min(bw, bh), 1)
                if aspect < 3.0:     # ★ 从3.5收紧到3.0
                    candidates.append((bx, by, bw, bh, area))

        if not candidates:
            if self.debug_report:
                total = int(cv2.countNonZero(mask))
                log.debug(f"  颜色鱼检测: 饱和像素={total}, 无合格轮廓")
            return None

        # 返回最大的候选 (最可能是鱼)
        candidates.sort(key=lambda c: c[4], reverse=True)
        bx, by, bw, bh, area = candidates[0]

        result = (rx + bx, ry + by, bw, bh, 0.55)

        if self.debug_report:
            log.debug(
                f"  颜色鱼检测: ✓ 位置=({result[0]},{result[1]}) "
                f"大小={bw}×{bh} 面积={area}"
            )

        return result

    def find_catch_bar(self, screen, bar_thresh: float,
                       hook_thresh: float, search_region=None):
        """查找白色控制条（模板匹配 → 鱼钩辅助 → 颜色检测）
        ★ 白条在游戏中总是比模板大, 只用 BAR_SCALES (≤1.0)"""
        # 方案1: 多尺度匹配白条 (仅低scale)
        bar = self.find_multiscale(
            screen, "bar", bar_thresh, search_region,
            scales=config.BAR_SCALES
        )
        if bar:
            return bar

        # 方案2: 鱼钩辅助定位
        hook = self.find_multiscale(screen, "hook", hook_thresh, search_region)
        if hook:
            bar_tmpl = self.templates.get("bar")
            bar_h = bar_tmpl.shape[0] if bar_tmpl is not None else 60
            return (hook[0], hook[1] - bar_h // 2, hook[2], bar_h, hook[4] * 0.9)

        return None

    def find_catch_bar_by_color(self, screen, strip_x: int, strip_w: int,
                                y_top: int, y_bottom: int):
        """颜色检测白条（最后备用方案）"""
        x1 = max(0, strip_x)
        x2 = min(screen.shape[1], strip_x + strip_w)
        y1 = max(0, y_top)
        y2 = min(screen.shape[0], y_bottom)
        strip = screen[y1:y2, x1:x2]
        if strip.size == 0:
            return None

        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 0, 190]), np.array([180, 50, 255]))
        row_ratio = np.mean(mask > 0, axis=1)
        bright_rows = np.where(row_ratio > 0.3)[0]

        if len(bright_rows) < 5:
            return None

        center_y = y1 + int(np.mean(bright_rows))
        height = int(bright_rows[-1] - bright_rows[0])
        return (center_y, max(height, 10))

    # ══════════════════ 颜色检测轨道 (旋转不变) ══════════════════

    def detect_track_by_color(self, screen):
        """
        通过颜色检测钓鱼轨道（旋转不变）。

        轨道特征:
        - 有明亮的蓝/青色发光边框
        - 是屏幕上最大的细长蓝色区域
        - 内部有明亮的白色区域（白块）

        返回: dict {
            'center': (cx, cy),    # 轨道中心坐标
            'angle':  float,       # 偏转角(度), 0=垂直, 正=顺时针
            'length': float,       # 轨道长边(像素)
            'width':  float,       # 轨道短边(像素)
        } 或 None
        """
        hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
        h_scr, w_scr = screen.shape[:2]

        # ── 蓝/青色: 轨道边框的发光色 ──
        blue_mask = cv2.inRange(
            hsv, np.array([85, 50, 100]), np.array([140, 255, 255])
        )

        # ── 明亮白色: 白块也属于轨道 ──
        white_mask = cv2.inRange(
            hsv, np.array([0, 0, 200]), np.array([180, 40, 255])
        )

        combined = cv2.bitwise_or(blue_mask, white_mask)

        # ── 形态学: 闭合轨道内部间隙 + 去噪 ──
        kernel_close = np.ones((11, 11), np.uint8)
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel_close)
        kernel_open = np.ones((5, 5), np.uint8)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel_open)

        contours, _ = cv2.findContours(
            combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # ── 找最大的细长轮廓 (轨道) ──
        best_contour = None
        best_score = 0
        min_length = max(h_scr, w_scr) * 0.15

        for c in contours:
            area = cv2.contourArea(c)
            if area < 1500:
                continue

            rect = cv2.minAreaRect(c)
            (_, _), (rw, rh), _ = rect
            long_side = max(rw, rh)
            short_side = max(min(rw, rh), 1)
            aspect = long_side / short_side

            if aspect < 3.5:            # 轨道是细长的
                continue
            if long_side < min_length:   # 至少占屏幕 15%
                continue

            score = area * aspect
            if score > best_score:
                best_score = score
                best_contour = c

        if best_contour is None:
            if self.debug_report:
                log.debug("  颜色轨道检测: ✗ 未找到细长蓝色区域")
            return None

        # ── fitLine 精确计算角度 ──
        vx, vy, x0, y0 = cv2.fitLine(
            best_contour, cv2.DIST_L2, 0, 0.01, 0.01
        )
        vx, vy = float(vx[0]), float(vy[0])

        # 确保方向从上到下 (vy > 0)
        if vy < 0:
            vx, vy = -vx, -vy

        # 从垂直方向的偏转角: atan2(vx, vy), 0=垂直, 正=向右偏
        angle_deg = float(np.degrees(np.arctan2(vx, vy)))

        # 归一化到 [-90, 90]
        while angle_deg > 90:
            angle_deg -= 180
        while angle_deg < -90:
            angle_deg += 180

        rect = cv2.minAreaRect(best_contour)
        (cx, cy), (rw, rh), _ = rect

        result = {
            'center': (int(cx), int(cy)),
            'angle':  angle_deg,
            'length': float(max(rw, rh)),
            'width':  float(min(rw, rh)),
        }

        if self.debug_report:
            log.debug(
                f"  颜色轨道检测: ✓ 中心=({int(cx)},{int(cy)}) "
                f"角度={angle_deg:.1f}° 长={result['length']:.0f} "
                f"宽={result['width']:.0f}"
            )

        return result

    # ══════════════════ 进度条检测 ══════════════════

    def detect_green_ratio(self, screen, region) -> float:
        """检测区域中绿色像素比例（进度条状态）"""
        x, y, w, h = [int(v) for v in region]
        x, y = max(0, x), max(0, y)
        w = min(w, screen.shape[1] - x)
        h = min(h, screen.shape[0] - y)
        if w <= 0 or h <= 0:
            return 0.0

        roi = screen[y: y + h, x: x + w]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([35, 50, 50]), np.array([85, 255, 255]))
        total = mask.size
        return float(np.count_nonzero(mask)) / total if total > 0 else 0.0
