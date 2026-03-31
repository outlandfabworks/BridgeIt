"""Subprocess target for pipeline execution isolated from Qt.

Running cv2 inside a QThread corrupts glibc's heap on some Qt/OpenCV builds.
By executing the pipeline here — in a fresh child process — cv2 and Qt
never share a heap, so the corruption cannot happen.
"""


def run_pipeline(queue, source, settings):
    """Entry point called in a child process by multiprocessing.Process.

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
    except BaseException as exc:
        import traceback
        queue.put(("err", traceback.format_exc()))
