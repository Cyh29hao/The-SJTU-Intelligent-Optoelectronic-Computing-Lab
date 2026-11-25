"""Send a local news draft to the preview server and return its preview link."""

import argparse
import hashlib
import json
import os
import re
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from news_editor_store import MAX_IMAGE_BYTES


TOKEN_VARIABLE = 'OPTICOM_DRAFT_TOKEN'
DRAFT_ENDPOINT = '/editor/api/v1/news/drafts'


class UploadError(Exception):
    pass


def local_origin(value):
    try:
        site = urlsplit(value)
        port = site.port
    except ValueError:
        raise UploadError('站点地址格式有误。') from None
    if (
        site.scheme != 'http' or site.hostname not in ('localhost', '127.0.0.1')
        or site.username is not None or site.password is not None
        or site.path not in ('', '/') or site.query or site.fragment or port == 0
    ):
        raise UploadError('请选择本机预览地址，例如 http://localhost:8000。')
    return f'http://{site.hostname}' + (f':{port}' if port is not None else '')


def read_draft(path):
    try:
        with path.open(encoding='utf-8-sig') as source:
            values = json.load(source)
        if not isinstance(values, dict):
            raise ValueError
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except OSError as error:
        raise UploadError(f'无法读取草稿文件 {path}。{error.strerror or ""}') from None
    except (ValueError, RecursionError):
        raise UploadError(f'草稿文件须是 UTF-8 编码的 JSON 对象。请检查 {path}。') from None


def read_image(path):
    try:
        with path.open('rb') as source:
            content = source.read(MAX_IMAGE_BYTES + 1)
    except OSError as error:
        raise UploadError(f'无法读取图片 {path}。{error.strerror or ""}') from None
    if not 0 < len(content) <= MAX_IMAGE_BYTES:
        raise UploadError('请选择一张 5 MiB 以内的非空图片。')
    return content


def draft_token():
    token = os.environ.get(TOKEN_VARIABLE, '').strip()
    if not token and sys.stdin.isatty():
        token = getpass('请输入草稿凭证（输入隐藏）：').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise UploadError(f'请先通过安全方式为当前进程设置有效的 {TOKEN_VARIABLE}。')
    return token


def post_draft(origin, payload, image_path, image, key, token):
    headers = {'Authorization': f'Bearer {token}', 'Idempotency-Key': key}
    with requests.Session() as client:
        client.trust_env = False
        try:
            if image_path is None:
                headers['Content-Type'] = 'application/json'
                response = client.post(
                    origin + DRAFT_ENDPOINT, headers=headers, data=payload,
                    timeout=(3, 12), allow_redirects=False,
                )
            else:
                response = client.post(
                    origin + DRAFT_ENDPOINT, headers=headers, data={'payload': payload},
                    files={'image': (image_path.name, image)}, timeout=(3, 12), allow_redirects=False,
                )
        except requests.RequestException:
            raise UploadError(f'无法确认草稿是否已收到。请检查 {origin}，然后用原文件和原提交键重试。') from None

    try:
        result = response.json()
    except ValueError:
        result = None
    if response.status_code not in (200, 201):
        details = result.get('error') if isinstance(result, dict) else None
        if isinstance(details, str):
            details = ' '.join(details.replace(token, '[凭证已隐藏]').split())[:350]
        raise UploadError(f'草稿提交失败（HTTP {response.status_code}）。{details or "请检查本机预览服务。"}')

    news_id = result.get('id') if isinstance(result, dict) else None
    if not isinstance(news_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', news_id):
        raise UploadError('预览服务返回的数据缺少有效的新闻编号。请在后台核对草稿。')
    edit_path = f'/editor/news/{news_id}'
    created = response.status_code == 201
    if (
        result.get('edit_url') != edit_path or result.get('preview_url') != edit_path + '/preview'
        or result.get('created') is not created
    ):
        raise UploadError('预览服务返回的状态或链接有误。请在后台核对草稿。')
    return {
        'id': news_id,
        'created': created,
        'edit_url': origin + result['edit_url'],
        'preview_url': origin + result['preview_url'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('draft', type=Path, help='UTF-8 JSON 草稿文件')
    parser.add_argument('--image', type=Path, help='PNG、JPEG 或 WebP 配图')
    parser.add_argument('--site', default='http://localhost:8000', help='本机预览地址')
    parser.add_argument('--submission-key', help='同一次提交重试时复用的键，省略时按素材自动生成')
    parser.add_argument('--json', action='store_true', help='输出 JSON 供 Agent 读取')
    args = parser.parse_args()

    try:
        origin = local_origin(args.site)
        payload = read_draft(args.draft)
        image = read_image(args.image) if args.image is not None else b''
        key = args.submission_key
        if key is None:
            digest = hashlib.sha256(payload + b'\x00')
            digest.update(image)
            key = 'draft-' + digest.hexdigest()
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
            raise UploadError('提交键须由 1–128 个英文字母、数字或 ._:- 组成。')
        result = post_draft(origin, payload, args.image, image, key, draft_token())
    except (UploadError, EOFError) as error:
        print(f'提交失败。{error}', file=sys.stderr)
        return 1

    result.update(
        draft_file=str(args.draft.resolve()),
        image_file=str(args.image.resolve()) if args.image else None,
        published_by_script=False,
        submission_key=key,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('已创建草稿。' if result['created'] else '已取回同一次提交的原草稿。')
        print('草稿文件', result['draft_file'])
        if result['image_file']:
            print('配图', result['image_file'])
        print('预览链接', result['preview_url'])
        print('请登录编辑账号预览。本次操作仅提交草稿。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
