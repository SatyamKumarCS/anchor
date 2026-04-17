import random
from flask import Flask, jsonify, abort
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
metrics = PrometheusMetrics(app)

FAILURE_RATE = 0.20  # 20% of requests return 500


@app.route("/")
def index():
    if random.random() < FAILURE_RATE:
        abort(500)
    return jsonify({"version": "v2", "status": "ok", "color": "green"})


@app.route("/health")
def health():
    # Health check always passes — the bug is in request handling, not startup
    return jsonify({"healthy": True, "version": "v2"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
