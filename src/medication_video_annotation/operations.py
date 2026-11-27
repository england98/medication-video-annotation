"""Timed operations reuse the same run, preserve failures, and recover lost sessions."""

from contextlib import contextmanager

from .timing import TERMINAL


def latest_task(run, task_id):
    return next((e for e in reversed(run.events()) if e.get("task_id") == task_id), None)


@contextmanager
def operation(run, task_id, stage, video_id=None):
    old = latest_task(run, task_id)
    if old and old["state"] not in TERMINAL:
        if old["state"] != "INTERRUPTED":
            run.transition(
                task_id, "RECOVER", reason="Process restarted; unfinished interval unknown"
            )
        run.transition(task_id, "RESUME")
    else:
        run.start(
            task_id,
            stage,
            video_id=video_id,
            activity_kind="TOOL_EXECUTION",
            retry_reason="Retry/revalidate requested operation" if old else None,
        )
    try:
        yield
    except Exception as error:
        run.transition(task_id, "FINISH", result="FAIL", reason=str(error) or type(error).__name__)
        raise
    else:
        run.transition(task_id, "FINISH", result="COMPLETE")
