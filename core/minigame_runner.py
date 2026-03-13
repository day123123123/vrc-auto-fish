"""
小游戏编排器
============
负责小游戏一局的主循环，把 `FishingBot` 降为顶层 orchestrator。
"""

import time

import config


class MinigameRunner:
    """执行一局小游戏，并复用 FishingBot 已有的检测/输入能力。"""

    def __init__(self, bot):
        self.bot = bot

    def run(self, start_in_minigame=False):
        """返回 True=成功, False=失败/停止, None=验证失败需重试。"""
        bot = self.bot

        bot.il.reset_round()

        if config.USE_YOLO and bot.yolo is None:
            try:
                from core.bot import _get_yolo_detector
                bot.yolo = _get_yolo_detector()
            except Exception as e:
                from utils.logger import log
                log.warning_t("runner.log.yoloLoadFallback", error=e)

        use_yolo = config.USE_YOLO and bot.yolo is not None
        skip_success_check = getattr(config, "SKIP_SUCCESS_CHECK", False)

        ok_to_continue, entered_minigame_early = bot._wait_for_minigame_entry(
            start_in_minigame, use_yolo
        )
        if not ok_to_continue:
            return False

        bot._announce_minigame_start(entered_minigame_early, use_yolo)
        runtime = bot._build_minigame_runtime(entered_minigame_early)
        ctx = bot._build_detection_context(use_yolo, skip_success_check)
        bot._initialize_minigame_context(ctx)
        pipe = bot._start_pipeline(ctx)
        bot._active_control_backend = bot._build_control_backend()

        try:
            while bot.running:
                pipe_data = bot._get_next_detection_result(runtime, ctx, pipe)
                if pipe_data is None:
                    continue
                (screen_raw, screen,
                 pipe_fish, pipe_bar, pipe_progress, pipe_hook,
                 pipe_mk, pipe_bs, _pipe_track) = pipe_data

                elapsed = time.time() - runtime.minigame_start
                if elapsed > config.MINIGAME_TIMEOUT:
                    from utils.logger import log
                    log.info_t(
                        "runner.log.timeoutForceEnd",
                        elapsed=elapsed,
                        limit=config.MINIGAME_TIMEOUT,
                    )
                    break

                if runtime.frame % 60 == 0:
                    bot.input.ensure_cursor_in_game()

                if runtime.no_detect > 3 and not use_yolo:
                    if pipe_bar is not None:
                        from utils.logger import log
                        log.info_t("runner.log.barRecovered", count=runtime.no_detect)
                        runtime.no_detect = 0
                    else:
                        runtime.no_detect += 1
                        if runtime.no_detect > 5:
                            bot.input.mouse_up()
                        if runtime.no_detect >= config.VERIFY_FRAMES:
                            if bot._try_rescue_pd(
                                    "连续丢失结束判定", runtime, skip_success_check):
                                continue
                            from utils.logger import log
                            log.info(
                                f"[📋 结束] 连续{runtime.no_detect}帧未检测到有效UI，"
                                f"达到结束帧数 {config.VERIFY_FRAMES}"
                            )
                            runtime.hook_timeout_retry = False
                            break
                        bot._show_debug_overlay(
                            screen_raw,
                            status_text=f"⚠ 丢失中 {runtime.no_detect}/{config.VERIFY_FRAMES}"
                        )
                        continue

                fish = pipe_fish
                bar = pipe_bar
                yolo_progress = pipe_progress
                prog_hook = pipe_hook
                fish, bar, yolo_progress = bot._postprocess_minigame_detection(
                    screen, screen_raw,
                    fish, bar, pipe_mk, pipe_bs, yolo_progress, prog_hook,
                    runtime, ctx
                )

                activation_state = bot._maybe_activate_minigame(
                    fish, bar, yolo_progress, runtime, ctx
                )
                if activation_state == "break":
                    break
                if activation_state == "continue":
                    time.sleep(config.GAME_LOOP_INTERVAL)
                    continue

                green = bot._compute_minigame_progress(
                    screen, screen_raw, fish, bar, yolo_progress,
                    prog_hook, runtime, ctx
                )

                end_state = bot._evaluate_minigame_end_state(
                    screen, fish, bar, runtime,
                    lambda reason, attempts=3, interval_s=0.02: bot._try_rescue_pd(
                        reason, runtime, skip_success_check, attempts, interval_s
                    )
                )
                if end_state == "break":
                    break
                if end_state == "continue":
                    continue

                held = bot._run_minigame_control(
                    fish, bar, yolo_progress, runtime, ctx
                )
                if held:
                    runtime.hold_count += 1

                bot._log_minigame_frame(
                    fish, bar, green, runtime, skip_success_check
                )
                bot._sync_pipeline_params(runtime, ctx, pipe)
                time.sleep(config.GAME_LOOP_INTERVAL)
        finally:
            bot._active_control_backend = None
            bot._stop_pipeline(pipe)
            return bot._finalize_minigame(
                runtime.hook_timeout_retry,
                runtime.skip_fish,
                skip_success_check,
                runtime.last_green,
            )
