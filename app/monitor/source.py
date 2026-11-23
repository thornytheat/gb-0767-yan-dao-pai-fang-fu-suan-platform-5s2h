import json
from hashlib import sha256

from app.config import FILES, SAMPLES, SOURCE_LIMIT
from app.errors import InputError
from app.monitor.reader import read_csv
from app.monitor.schema import HEADERS, Window


def sample(name):
    if name not in {'clean', 'dirty'}:
        raise InputError('SAMPLE_UNKNOWN', '找不到指定样例', 404)
    return {key: (SAMPLES / name / (key + ('.json' if key == 'window' else '.csv')))
            .read_bytes().decode('utf-8') for key in FILES}


def input_packet(payload):
    if not isinstance(payload, dict) or set(payload) not in ({'sample'}, {'files'}):
        raise InputError('BAD_PACKET', '选择内置样例或提供原件')
    if 'sample' in payload:
        return sample(str(payload['sample'])), str(payload['sample'])
    files = payload['files']
    legacy = set(FILES) - {'receipts'}
    if not isinstance(files, dict) or set(files) not in (set(FILES), legacy) or any(
        not isinstance(value, str) or len(value) > SOURCE_LIMIT for value in files.values()
    ):
        raise InputError('BAD_PACKET', '原件必须为限长 UTF-8 文本（回执可省略）')
    files = {'receipts': ','.join(HEADERS['receipts'])} | files
    try:
        for value in files.values():
            value.encode('utf-8')
    except UnicodeEncodeError:
        raise InputError('BAD_PACKET', '原件含无效 Unicode') from None
    return files, 'upload'


def intake(files):
    try:
        window_raw = json.loads(files['window'].removeprefix('\ufeff'))
    except (json.JSONDecodeError, RecursionError):
        raise InputError('BAD_WINDOW', '窗口文件不是有效 JSON') from None
    window = Window.parse(window_raw)
    parsed = {source: read_csv(files[source], source) for source in HEADERS}
    return window, parsed


def digests(files):
    return {key: sha256(value.encode('utf-8')).hexdigest() for key, value in sorted(files.items())}
