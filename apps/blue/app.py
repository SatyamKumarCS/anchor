from flask import Flask, jsonify
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
metrics = PrometheusMetrics(app)


@app.route("/")
def index():
    return jsonify({"version": "v1", "status": "ok", "color": "blue"})


@app.route("/health")
def health():
    return jsonify({"healthy": True, "version": "v1"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
