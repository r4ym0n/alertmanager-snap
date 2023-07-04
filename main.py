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
        logging.info(f"Function {func.__name__} took {elapsed_time:.6f} seconds to execute.")
        return result
    return wrapper


@time_es
def save_file_s3(local_file_path, filename):
    s3 = boto3.client('s3', region_name=region_name)
    s3_object_key = f'imgs/{filename}'

    # 上传文件到 S3
    s3.upload_file(local_file_path, bucket_name, s3_object_key)

@time_es
def regenerate_quary_for_instance(alert_data):
    generator_url = alert_data["alerts"][0]["generatorURL"]
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)
    import urllib.parse 
    query_expr = urllib.parse.unquote(query_expr).replace('+', ' ')
    def remove_comparison_strings(s):
        pattern = r'<\s*\d+|\s*>\s*\d+|<\s*\d+\s*>|\s*>\s*\d+\s*' # 匹配所有大于小于空格数字组合的正则表达式
        return re.sub(pattern, "", s)  # 使用sub函数替换所有匹配到的组合为“”


    logging.info(query_expr) 
    query_expr_no_comparison = remove_comparison_strings(query_expr)
    logging.info(query_expr_no_comparison) 
    

    return query_expr_no_comparison



@time_es
def get_graph_data(prometheus_url, query, start_time, end_time, x_header=""):
    url = f"{prometheus_url}/api/v1/query_range?query={query}&start={start_time}&end={end_time}&step=15s"
    # url = f"{prometheus_url}/api/v1/query_range"
    # params = {
    #     "query": query,
    #     "start": start_time,
    #     "end": end_time,
    #     "step": "15s"
    # }
    headers = {"X-Scope-OrgID": x_header}

    response = requests.get(url, headers=headers)
    logging.info(f"hrader: {x_header}, url: {response.url}")

    response.raise_for_status()

    data = response.json().get('data', {})
    result = data.get('result', [])

    if not result:
        return None
    print(result)
    serials = []
    for sl in result:
        ts = [item[0] for item in sl.get('values', [])]
        values = [item[1] for item in sl.get('values', [])]
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
    def to_float(y):
        return [float(x) for x in y]
    def to_int(y):
        return [int(x) for x in y]
    fig, ax = plt.subplots()
    for serial in serials:
        dates = [datetime.fromtimestamp(ts) for ts in to_int(serial['x'])]
        ax.plot(dates, to_float(serial['y']), label=serial['label'])
    #url decode title string
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

def get_X_header(ext_url):
    data_map = config['prometheus']['business_map']
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
    GRAPH_DURATION = 30
    manager_notify = request.json
    generator_url = manager_notify["alerts"][0]["generatorURL"]
    status = manager_notify["alerts"][0]["status"]
    fingerprint = manager_notify["alerts"][0]["fingerprint"]
    ext_url = manager_notify["alerts"][0]['labels']["ext_url"]
    # 从generatorURL字段中提取查询表达式和时间戳信息
    query_expr = re.search(r"g0\.expr=(.*?)&", generator_url).group(1)

    # replace comparison strings, quary all serial...
    query_expr = regenerate_quary_for_instance(manager_notify)

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

    instanceId = list(map(lambda x: x['labels']["instance"], manager_notify["alerts"]))
    print(instanceId)
    graph_data = get_graph_data(
        prometheus_url=prometheus_url,
        query=query_expr,
        start_time=start_time,
        end_time=end_time,
        x_header=get_X_header(ext_url)
    )

    if graph_data == None:
        logging.warning("graph_data is None, query_expr: {}".format(query_expr))
        # status = 404
        abort(404)


    svg_name = plot_multi_line_svg(graph_data['title'], graph_data['serials'])
    target_name = f"{fingerprint}-{status}.png"

    shutil.move(svg_name, f"./{target_name}")
    save_file_s3(target_name, target_name)

    # shutil.move(target_name, f"/var/www/html/imgs/{target_name}")
    return target_name


if __name__ == '__main__':
    app.run(debug=DEBUG, host="0.0.0.0")
