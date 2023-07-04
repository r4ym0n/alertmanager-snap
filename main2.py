from flask import Flask, request, jsonify
import requests
import matplotlib.pyplot as plt
from io import BytesIO
import svgwrite
import base64
import re
import time
from datetime import datetime
import shutil

import logging
import time
import boto3

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO)

config = json.load(open('config.json'))

prometheus_url = config['prometheus']['endpoint']

region_name="ap-northeast-1"
bucket_name = 'vinotech-monitor-snap-public'

def time_es(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        elapsed_time = end_time - start_time
        logging.info(
            f"Function {func.__name__} took {elapsed_time:.6f} seconds to execute.")
        return result
    return wrapper


@time_es
def save_file_s3(local_file_path, filename):
    s3 = boto3.client('s3', region_name=region_name)
    bucket_name=bucket_name
    s3_object_key = f'imgs/{filename}'

    # 上传文件到 S3
    s3.upload_file(local_file_path, bucket_name, s3_object_key)


@time_es
def get_graph_data(prometheus_url, query, start_time, end_time, x_header=""):
    """
    - `prometheus_url`: Prometheus 服务器的 URL
    - `query`: 要查询的 Prometheus 查询语句
    - `start_time`: 查询的起始时间
    - `end_time`: 查询的结束时间
    """
    url = f"{prometheus_url}/api/v1/query_range?query={query}&start={start_time}&end={end_time}&step=1m"
    print(query)
    headers={"X-Scope-OrgID":x_header}
    print(headers)
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    data = response.json().get('data', {})
    result = data.get('result', [])

    if len(result) == 0:
        return None

    serials = []
    for sl in result:
        ts = []
        values = []
        for item in sl.get('values', []):
            ts.append(item[0])
            values.append(item[1])
        serials.append({
            'label': sl.get('metric').get('instance'),
            'x': ts,
            'y': values
        })
    return {
        'title': query,
        'serials': serials
    }


@time_es
def plot_multi_line_svg(title, serials):
    # 创建一个新的图表
    # important
    def toNum(y): return list(map(lambda x: float(x), y))
    fig, ax = plt.subplots()
    for serial in serials:
        # for x, y in zip(serial['x'],serial['y']):
        #     ax.plot(int(x), int(y))
        ax.plot(toNum(serial['x']), toNum(serial['y']), label=serial['label'])
    # 设置图表标题和图例
    ax.set_title(title)
    ax.legend()
    plt.xlabel("ts")
    tmp_name = "output.png"
    image_format = 'png'  # e.g .png, .svg, etc.
    image_name = tmp_name

    fig.savefig(image_name, format=image_format, dpi=100)
    return tmp_name


def get_X_header(ext_url):
    data_map = {
        "https://prom-quant.monitor.viasupervisor.com": "ViabtcQuant",
        "https://prom-wallet.monitor.viasupervisor.com": "ViabtcWallet",
        "https://prom-pool.monitor.viasupervisor.com": "ViabtcPool",
        "https://prom-poolweb.monitor.viasupervisor.com": "ViabtcPoolWeb",
        "https://prom-chain.monitor.viasupervisor.com": "ViabtcChain",
    }
    return data_map.get(ext_url)


app = Flask(__name__)


@app.route('/alert', methods=['POST'])
def handle_alert():
    manager_notify = request.json
    alert = request.json
    generator_url = manager_notify["alerts"][0]["generatorURL"]

    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)

    graph_data = get_graph_data(
        prometheus_url=prometheus_url,
        query=query_expr,
        # start_time=alert['startsAt'],
        # end_time=alert['endsAt'],
        start_time=1687839599,
        end_time=1687849599
    )
    alert['graph_data'] = graph_data
    return jsonify(alert)


@app.route('/alert_svg', methods=['POST'])
def handle_alert_svg():
    manager_notify = request.json
    alert = request.json
    generator_url = manager_notify["alerts"][0]["generatorURL"]

    status = manager_notify["alerts"][0]["status"]
    fingerprint = manager_notify["alerts"][0]["fingerprint"]
    ext_url = manager_notify["alerts"][0]['labels']["ext_url"]

    # 从generatorURL字段中提取查询表达式和时间戳信息
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)

    if manager_notify["alerts"][0]["status"] == "reslved":
        start_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
        end_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
    else:
        start_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp()) - 600
        end_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
    
    graph_data = get_graph_data(
        prometheus_url=prometheus_url,
        query=query_expr,
        start_time=start_time,
        end_time=end_time,
        x_header=get_X_header(ext_url)
    )

    svg_name = plot_multi_line_svg(graph_data['title'], graph_data['serials'])
    target_name = f"{fingerprint}-{status}.png"

    shutil.move(svg_name, f"./{target_name}")
    save_file_s3(target_name, target_name)

    shutil.move(target_name, f"/var/www/html/imgs/{target_name}")
    return target_name


app.run(debug=True, host="0.0.0.0")