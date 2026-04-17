from flask import Flask, render_template, jsonify
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
metrics = PrometheusMetrics(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"healthy": True, "version": "v1"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
