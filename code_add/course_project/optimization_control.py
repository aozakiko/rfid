"""Shared request accounting and cooperative optimization limits."""
import time


class OptimizationStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class OptimizationControl:
    def __init__(self, max_requests=30, max_seconds=1800, cancel_check=None):
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.started = time.monotonic()
        self.requests = 0
        self.cancel_check = cancel_check

    def check(self):
        if self.cancel_check:
            from course_project.jobs import JobCancelled
            try:
                self.cancel_check()
            except JobCancelled:
                raise OptimizationStopped("cancelled") from None
        if time.monotonic() - self.started >= self.max_seconds:
            raise OptimizationStopped("time_budget")

    def before_request(self):
        self.check()
        if self.requests >= self.max_requests:
            raise OptimizationStopped("model_request_budget")
        self.requests += 1
        return max(.001, self.max_seconds - (time.monotonic() - self.started))

    def snapshot(self):
        return {"model_requests": self.requests, "max_model_requests": self.max_requests,
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "max_seconds": self.max_seconds,
                "time_limit_scope": "cooperative phase boundaries; active HTTP uses remaining socket timeout"}
