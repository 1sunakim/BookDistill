"""Small dependency-free Prometheus metrics registry for the API process."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency: dict[tuple[str, str], tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        self._model_calls: dict[tuple[str, str], tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        self._vector_searches: dict[tuple[str, str], tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        route = route.replace('"', "")
        with self._lock:
            self._requests[(method, route, status)] += 1
            count, total = self._latency[(method, route)]
            self._latency[(method, route)] = (count + 1, total + seconds)

    def observe_model(self, provider: str, status: str, seconds: float) -> None:
        with self._lock:
            count, total = self._model_calls[(provider, status)]
            self._model_calls[(provider, status)] = (count + 1, total + seconds)

    def observe_vector(self, backend: str, status: str, seconds: float) -> None:
        with self._lock:
            count, total = self._vector_searches[(backend, status)]
            self._vector_searches[(backend, status)] = (count + 1, total + seconds)

    def render(self) -> list[str]:
        lines = [
            "# HELP bookdistill_api_requests_total HTTP requests by route and status.",
            "# TYPE bookdistill_api_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(f'bookdistill_api_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
            lines.extend([
                "# HELP bookdistill_api_latency_seconds_sum Cumulative API latency.",
                "# TYPE bookdistill_api_latency_seconds_sum counter",
                "# HELP bookdistill_api_latency_seconds_count API latency observations.",
                "# TYPE bookdistill_api_latency_seconds_count counter",
            ])
            for (method, route), (count, total) in sorted(self._latency.items()):
                labels = f'method="{method}",route="{route}"'
                lines.append(f"bookdistill_api_latency_seconds_sum{{{labels}}} {total:.6f}")
                lines.append(f"bookdistill_api_latency_seconds_count{{{labels}}} {count}")
            lines.extend([
                "# HELP bookdistill_model_calls_total Model calls by provider and status.",
                "# TYPE bookdistill_model_calls_total counter",
                "# HELP bookdistill_model_call_duration_seconds_sum Cumulative model call duration.",
                "# TYPE bookdistill_model_call_duration_seconds_sum counter",
            ])
            for (provider, status), (count, total) in sorted(self._model_calls.items()):
                labels = f'provider="{provider}",status="{status}"'
                lines.append(f"bookdistill_model_calls_total{{{labels}}} {count}")
                lines.append(f"bookdistill_model_call_duration_seconds_sum{{{labels}}} {total:.6f}")
            lines.extend([
                "# HELP bookdistill_vector_searches_total Vector searches by backend and status.",
                "# TYPE bookdistill_vector_searches_total counter",
                "# HELP bookdistill_vector_search_duration_seconds_sum Cumulative vector search duration.",
                "# TYPE bookdistill_vector_search_duration_seconds_sum counter",
            ])
            for (backend, status), (count, total) in sorted(self._vector_searches.items()):
                labels = f'backend="{backend}",status="{status}"'
                lines.append(f"bookdistill_vector_searches_total{{{labels}}} {count}")
                lines.append(f"bookdistill_vector_search_duration_seconds_sum{{{labels}}} {total:.6f}")
        return lines


metrics = Metrics()
