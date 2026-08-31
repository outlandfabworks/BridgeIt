"""Subprocess targets for pipeline execution isolated from Qt.

Running cv2 inside a QThread corrupts glibc's heap on some Qt/OpenCV builds.
By executing the pipeline here — in a fresh child process — cv2 and Qt
never share a heap, so the corruption cannot happen.

Both full runs and preview-only (settings-change) re-runs go through a
subprocess so the cv2 calls in trace_contours never touch Qt's allocator.

Progress messages are sent to the caller as ("progress", message) tuples on
the same queue as the result, so the worker thread can relay them to the UI.
"""


def run_pipeline(queue, source, settings):
    """Full pipeline: background removal → trace → analyze → bridge → export."""
    try:
        from bridgeit.pipeline.pipeline import PipelineRunner
        def on_progress(stage, msg):
            queue.put(("progress", msg))
        runner = PipelineRunner(settings=settings, on_progress=on_progress)
        result = runner.run(source)
        queue.put(("ok", result))
    except Exception:
        import traceback
        queue.put(("err", traceback.format_exc()))


def run_preview(queue, nobg_image, settings):
    """Preview-only re-run: reuses cached nobg_image, skips background removal."""
    try:
        from bridgeit.pipeline.pipeline import PipelineRunner
        def on_progress(stage, msg):
            queue.put(("progress", msg))
        runner = PipelineRunner(settings=settings, on_progress=on_progress)
        result = runner.run_to_preview(nobg_image)
        queue.put(("ok", result))
    except Exception:
        import traceback
        queue.put(("err", traceback.format_exc()))
