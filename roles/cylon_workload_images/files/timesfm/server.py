"""TimesFM forecast API for the cylon ts-inference node (spark3).

One process serves every model named in TIMESFM_MODELS (comma list of keys below).
Weights come from the Hugging Face cache (hostPath on the node, filled on first start).

Licences: timesfm-2.5 is Apache-2.0. timesfm-3.0 weights are NON-COMMERCIAL: this server
runs them for training and research only; production use of 3.0 goes through Vertex AI.

  GET  /health                 -> {"status": "ok", "models": [...]}
  POST /v1/forecast            {"model": "timesfm-2.5", "horizon": 12,
                                "series": [[...], [...]], "quantiles": true}
                             -> {"model": ..., "horizon": ..., "forecast": [[...]],
                                 "quantiles": [[[...9 levels...] per step] per series] | null,
                                 "elapsed_ms": ...}
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch

log = logging.getLogger("timesfm-server")
MAX_CONTEXT = int(os.environ.get("TIMESFM_MAX_CONTEXT", "2048"))
MAX_HORIZON = int(os.environ.get("TIMESFM_MAX_HORIZON", "256"))
MAX_SERIES = int(os.environ.get("TIMESFM_MAX_SERIES", "512"))


class _TimesFM25:
    repo = "google/timesfm-2.5-200m-pytorch"

    def __init__(self) -> None:
        import timesfm

        self.m = timesfm.TimesFM_2p5_200M_torch.from_pretrained(self.repo)
        self.m.compile(
            timesfm.ForecastConfig(
                max_context=MAX_CONTEXT,
                max_horizon=MAX_HORIZON,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )

    def forecast(self, series, horizon, quantiles):
        point, q = self.m.forecast(horizon=horizon, inputs=series)
        point = np.asarray(point)[:, :horizon]
        qs = None
        if quantiles and q is not None:
            # q: (n, horizon, 10) = mean + deciles 0.1..0.9; drop the mean column.
            qs = np.asarray(q)[:, :horizon, 1:]
        return point, qs


class _TimesFM30:
    repo = "google/timesfm-3.0-pytorch"

    def __init__(self) -> None:
        import timesfm

        self.m = timesfm.TimesFM3Forecaster.from_pretrained(self.repo, device="cuda")

    def forecast(self, series, horizon, quantiles):
        outs = list(self.m.predict_batch(series, horizon=horizon, return_quantiles=quantiles))
        point = np.stack([np.asarray(o.forecast).reshape(-1)[:horizon] for o in outs])
        qs = None
        if quantiles:
            qs = np.stack([np.asarray(o.quantiles).reshape(-1, np.asarray(o.quantiles).shape[-1])[:horizon] for o in outs])
        return point, qs


LOADERS = {"timesfm-2.5": _TimesFM25, "timesfm-3.0": _TimesFM30}
MODELS: dict[str, object] = {}
WANTED = [k.strip() for k in os.environ.get("TIMESFM_MODELS", "timesfm-2.5").split(",") if k.strip()]
LOCKS: dict[str, threading.Lock] = {}


def load_models() -> None:
    for key in WANTED:
        t0 = time.time()
        MODELS[key] = LOADERS[key]()
        LOCKS[key] = threading.Lock()
        log.info("loaded %s in %.1fs", key, time.time() - t0)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quiet access log
        pass

    def do_GET(self):
        # /health = the process answers (liveness/startup; true while weights download).
        # /ready  = every model in TIMESFM_MODELS is loaded (readiness).
        if self.path in ("/health", "/healthz", "/ready"):
            ready = len(MODELS) == len(WANTED)
            code = 503 if (self.path == "/ready" and not ready) else 200
            self._send(code, {
                "status": "ok" if ready else "loading",
                "models": sorted(MODELS),
                "wanted": WANTED,
                "cuda": torch.cuda.is_available(),
                "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/forecast":
            return self._send(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            key = req.get("model") or next(iter(MODELS))
            if key not in MODELS:
                return self._send(400, {"error": f"unknown model {key!r}", "models": sorted(MODELS)})
            horizon = int(req.get("horizon", 12))
            series = req["series"]
            if not (1 <= horizon <= MAX_HORIZON):
                return self._send(400, {"error": f"horizon must be 1..{MAX_HORIZON}"})
            if not series or len(series) > MAX_SERIES:
                return self._send(400, {"error": f"series must hold 1..{MAX_SERIES} arrays"})
            arrs = [np.asarray(s, dtype=np.float32)[-MAX_CONTEXT:] for s in series]
            if any(a.ndim != 1 or a.size < 2 or not np.isfinite(a).all() for a in arrs):
                return self._send(400, {"error": "each series must be a finite 1-D array of >= 2 values"})
            t0 = time.time()
            with LOCKS[key], torch.inference_mode():
                point, qs = MODELS[key].forecast(arrs, horizon, bool(req.get("quantiles", False)))
            self._send(200, {
                "model": key,
                "horizon": horizon,
                "forecast": point.tolist(),
                "quantiles": qs.tolist() if qs is not None else None,
                "quantile_levels": [round(0.1 * i, 1) for i in range(1, 10)] if qs is not None else None,
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            })
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("forecast failed")
            self._send(500, {"error": repr(e)})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    load_models()
    log.info("serving %s on :%s", sorted(MODELS), srv.server_address[1])
    threading.Event().wait()


if __name__ == "__main__":
    main()
