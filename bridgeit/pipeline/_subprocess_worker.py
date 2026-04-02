"""Subprocess targets for pipeline execution isolated from Qt.

Running cv2 inside a QThread corrupts glibc's heap on some Qt/OpenCV builds.
By executing the pipeline here — in a fresh child process — cv2 and Qt
never share a heap, so the corruption cannot happen.

Both full runs and preview-only (settings-change) re-runs go through a
subprocess so the cv2 calls in trace_contours never touch Qt's allocator.
"""


def run_pipeline(queue, source, settings):
    """Full pipeline: background removal → trace → analyze → bridge → export.

    Args:
        queue:    multiprocessing.Queue used to send the result back.
        source:   Image file path (str/Path) or PIL Image.
        settings: PipelineSettings dataclass instance.
    """
    try:
        from bridgeit.pipeline.pipeline import PipelineRunner
        runner = PipelineRunner(settings=settings)
        result = runner.run(source)
        queue.put(("ok", result))
    except BaseException:
        import traceback
        queue.put(("err", traceback.format_exc()))


def run_preview(queue, nobg_image, settings):
    """Preview-only re-run: reuses cached nobg_image, skips background removal.

    Called when the user adjusts a settings slider — the slow bg-removal step
    is skipped; only trace → analyze → bridge → export are re-run.

    Args:
        queue:      multiprocessing.Queue used to send the result back.
        nobg_image: Already background-removed PIL Image (cached from full run).
        settings:   PipelineSettings dataclass instance.
    """
    try:
        from bridgeit.pipeline.pipeline import PipelineRunner
        runner = PipelineRunner(settings=settings)
        result = runner.run_to_preview(nobg_image)
        queue.put(("ok", result))
    except BaseException:
        import traceback
        queue.put(("err", traceback.format_exc()))
