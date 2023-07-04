from flask import Flask, request, jsonify, abort
import requests
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import svgwrite
import base64
import re
import json
import time
from datetime import datetime
import shutil
import math
import logging
import time
import boto3
from enum import Enum


logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO)

config = json.load(open('.config.json'))


DEBUG = True
# DEBUG = False


prometheus_url = config['prometheus']['endpoint']
bucket_name = config['s3']['bucket_name']
region_name = config['s3']['region_name']
        

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
    s3_object_key = f'imgs/{filename}'

    # 上传文件到 S3
    s3.upload_file(local_file_path, bucket_name, s3_object_key)


def data_instance_filter(alerts, graph_data, instanceID):
    info = get_current_business_info(alerts)
    if graph_data is None:
        return None
    if info['type'] == 1:
        return list(filter(lambda x: x['metric']['instance'] in instanceID, graph_data))
    elif info['type'] == 2:
        return list(filter(lambda x: x['metric']['instance'] in instanceID, graph_data))
    

def get_id_from_alerts(alerts):
    info = get_current_business_info(alerts)
    if info['type'] == 1:
        instanceIds = list(map(lambda x: x['labels']["instance"], alerts["alerts"]))
    elif info['type'] == 2:
        instanceIds = list(map(lambda x: x['labels']["instance"], alerts["alerts"]))
    logging.info(instanceIds)
    return instanceIds

@time_es
def regenerate_quary_for_instance(alert_data):
    # 获取非告警条件筛选的查询语句
    generator_url = alert_data["alerts"][0]["generatorURL"]
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)
    import urllib.parse
    query_expr = urllib.parse.unquote(query_expr).replace('+', ' ')

    def remove_comparison_strings(s):
        pattern = r'<\s*\d+|\s*>\s*\d+|<\s*\d+\s*>|\s*>\s*\d+\s*'  # 匹配所有大于小于空格数字组合的正则表达式
        return re.sub(pattern, "", s)  # 使用sub函数替换所有匹配到的组合为“”

    logging.info(query_expr)
    query_expr_no_comparison = remove_comparison_strings(query_expr)
    logging.info(query_expr_no_comparison)

    return query_expr_no_comparison


@time_es
def get_graph_data_raw(prometheus_url, query, start_time, end_time, x_header=""):
    url = f"{prometheus_url}/api/v1/query_range?query={query}&start={start_time}&end={end_time}&step=15s"
    headers = {"X-Scope-OrgID": x_header}

    response = requests.get(url, headers=headers)
    logging.info(f"hrader: {x_header}, url: {response.url}")

    response.raise_for_status()

    data = response.json().get('data', {})
    result = data.get('result', [])
    if not result:
        return None
    return result


def make_serial_data(raw_data, name):
    serials = []
    for sl in raw_data:
        ts = [item[0] for item in sl.get('values', [])]
        values = [item[1] for item in sl.get('values', [])]
        serials.append({
            'label': sl.get('metric').get('instance'),
            'x': ts,
            'y': values
        })
    return {
        'title': name,
        'serials': serials
    }


@time_es
def plot_multi_line_svg(title, serials):
    def to_float(y):
        return [float(x) for x in y]

    def to_int(y):
        return [int(x) for x in y]
    fig, ax = plt.subplots()
    for serial in serials:
        dates = [datetime.fromtimestamp(ts) for ts in to_int(serial['x'])]
        ax.plot(dates, to_float(serial['y']), label=serial['label'])
    # url decode title string
    import urllib.parse
    title = urllib.parse.unquote(title)
    # date_fmt = '%Y-%m-%d %H:%M:%S'
    date_fmt = '%H:%M'
    date_formatter = mdates.DateFormatter(date_fmt)
    ax.xaxis.set_major_formatter(date_formatter)
    ax.set_title(f"Expr: {title}")
    ax.legend()
    plt.ylabel("value")
    # today date YMD format
    plt.xlabel(datetime.now().strftime('%Y-%m-%d'))
    image_name = "output.png"
    fig.autofmt_xdate()
    fig.savefig(image_name, format='png', dpi=80)
    return image_name


def get_current_business_info(alerts):
    ext_url = alerts["alerts"][0]['labels']["ext_url"]
    buinfo = config['prometheus']['business_info']
    for info in buinfo:
        if info['ext_url'] == ext_url:
            return info
    return None


app = Flask(__name__)


@app.route('/alert', methods=['POST'])
def handle_alert():
    GRAPH_DURATION = 30
    manager_notify = request.json
    alert = request.json
    generator_url = manager_notify["alerts"][0]["generatorURL"]
    if manager_notify["alerts"][0]["status"] == "reslved":
        start_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp()) - (GRAPH_DURATION*60)
        end_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
    else:
        start_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp()) - (GRAPH_DURATION*60)
        end_time = int(datetime.strptime(
            manager_notify["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)
    graph_data = get_graph_data_raw(
        prometheus_url=prometheus_url,
        query=query_expr,
        start_time=start_time,
        end_time=end_time
    )
    alert['graph_data'] = graph_data
    return jsonify(alert)


@app.route('/alert_svg', methods=['POST'])
def handle_alert_svg():
    GRAPH_DURATION = 50
    alerts_data = request.json
    generator_url = alerts_data["alerts"][0]["generatorURL"]
    status = alerts_data["alerts"][0]["status"]
    fingerprint = alerts_data["alerts"][0]["fingerprint"]
    ext_url = alerts_data["alerts"][0]['labels']["ext_url"]
    # 从generatorURL字段中提取查询表达式和时间戳信息
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)

    # replace comparison strings, quary all serial...
    query_expr = regenerate_quary_for_instance(alerts_data)

    if alerts_data["alerts"][0]["status"] == "reslved":
        start_time = int(datetime.strptime(
            alerts_data["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp()) - (GRAPH_DURATION*60)
        end_time = int(datetime.strptime(
            alerts_data["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())
    else:
        start_time = int(datetime.strptime(
            alerts_data["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp()) - (GRAPH_DURATION*60)
        end_time = int(datetime.strptime(
            alerts_data["alerts"][0]["startsAt"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp())

    # 提取告警中的特征ID

    graph_data = get_graph_data_raw(
        prometheus_url=prometheus_url,
        query=query_expr,
        start_time=start_time,
        end_time=end_time,
        x_header=get_current_business_info(alerts_data)['xorg']
    )
    if graph_data == None:
        logging.warning(
            "graph_data is None, query_expr: {}".format(query_expr))
        # status = 404
        abort(404)

    graph_data_of_instances = data_instance_filter(alerts_data, graph_data, get_id_from_alerts(alerts_data))

    if graph_data_of_instances == []:
        logging.warning(
            "graph_data is None, query_expr: {}".format(query_expr))
        abort(404)
    
    
    serial_data = make_serial_data(graph_data_of_instances, query_expr)

    svg_name = plot_multi_line_svg(
        serial_data['title'], serial_data['serials'])
    target_name = f"{fingerprint}-{status}.png"

    shutil.move(svg_name, f"./{target_name}")
    save_file_s3(target_name, target_name)

    # shutil.move(target_name, f"/var/www/html/imgs/{target_name}")
    return target_name


if __name__ == '__main__':
    app.run(debug=DEBUG, host="0.0.0.0")
