"""Prepare a paper draft from local PDFs and return its localhost preview URL."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from scripts.upload_news_draft import UploadError, draft_token, local_origin


ARTICLES = Path(__file__).resolve().parents[1] / 'site_content' / 'articles.json'
DRAFT_ENDPOINT = '/editor/api/v1/papers/drafts'
DOI = r'10\.\d{4,9}/[^\s<>"\[\]]+'
DOI_LABEL = re.compile(r'(?:\bdoi\s*:\s*|https?://(?:dx\.)?doi\.org/)' + f'({DOI})', re.IGNORECASE)
TEXT_FIELDS = ('title', 'venue', 'abstract', 'abstract_zh', 'doi', 'paper_url', 'official_free_access_url', 'resource_url')
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_BYTES = 23 * 1024 * 1024


def _pdf_tool(tool, options, path, output_args=()):
    program = shutil.which(tool)
    if program is None:
        return ''
    try:
        result = subprocess.run(
            [program, *options, str(path), *output_args], capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=12,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ''
    return result.stdout if result.returncode == 0 else ''


def _pdf_source(path):
    try:
        with path.open('rb') as source:
            header = source.read(5)
    except OSError as error:
        raise UploadError(f'无法读取 PDF {path}。{error.strerror or ""}') from None
    if path.suffix.lower() != '.pdf' or header != b'%PDF-':
        raise UploadError(f'请选择有效的 PDF 文件。{path}')


def _doi_from_url(value):
    match = re.search(DOI, value, re.IGNORECASE)
    return match[0].rstrip('.,;:)/').lower() if match else ''


def _match_title(value):
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold() if char.isalnum())


def _matching_site_article(title, doi):
    try:
        articles = json.loads(ARTICLES.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    if doi:
        for row in articles:
            local = row.get('doi') or _doi_from_url(row.get('paper_url', ''))
            if local and local.casefold() == doi.casefold():
                return row
    title_key = _match_title(title) if title else ''
    matches = [row for row in articles if title_key and _match_title(row.get('title', '')) == title_key]
    return matches[0] if len(matches) == 1 else {}


def _source_values(path):
    info = _pdf_tool('pdfinfo', ('-enc', 'UTF-8'), path)
    front = _pdf_tool('pdftotext', ('-q', '-f', '1', '-l', '2', '-enc', 'UTF-8'), path, ('-',))
    fields = dict(re.findall(r'^(Title|Subject|Author):[ \t]*(.+)$', info, re.MULTILINE))
    result = {'title': fields.get('Title', '').strip()}
    author = fields.get('Author', '').strip()
    if re.fullmatch(r"[^,;\d]{2,90}", author) or ';' in author:
        result['authors'] = [name.strip() for name in author.split(';') if name.strip()]
    match = re.match(r'^([A-Za-z][A-Za-z ]{1,70})[ .]+((?:19|20)\d{2})\b', fields.get('Subject', ''))
    if match:
        result['venue'], result['year'] = match[1].strip(), int(match[2])
    front = front.split('\f', 1)[0].replace('\u00ad', '')
    abstract = re.search(r'(?ims)^\s*abstract\s*[:—–-]?\s+(.+?)(?=\n\s*\n|\n\s*(?:keywords|introduction)\b|\Z)', front)
    if abstract:
        result['abstract'] = ' '.join(abstract[1].split())
    doi_values = {_doi_from_url(match[1]) for match in DOI_LABEL.finditer(info + '\n' + front)}
    if len(doi_values) == 1:
        result['doi'] = doi_values.pop()
    return result


def _crossref(doi):
    response = requests.get(
        f'https://api.crossref.org/works/{quote(doi, safe="/")}',
        timeout=(4, 7), allow_redirects=False,
    )
    if response.status_code != 200:
        raise UploadError(f'Crossref 返回 HTTP {response.status_code}。')
    work = response.json()['message']
    if work.get('DOI', '').casefold() != doi.casefold():
        raise UploadError('Crossref 返回的 DOI 与查询不一致。')
    parts = (work.get('issued') or {}).get('date-parts') or [[]]
    return {
        'title': (work.get('title') or [''])[0],
        'authors': [
            ' '.join(part for part in (author.get('given'), author.get('family')) if part) or author.get('name', '')
            for author in work.get('author', [])
        ],
        'venue': (work.get('container-title') or [''])[0],
        'year': parts[0][0] if parts[0] else None,
    }


def _local_metadata(path):
    if path is None:
        return {}
    try:
        values = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(values, dict) or set(values) - {*TEXT_FIELDS, 'authors', 'year'}:
            raise ValueError
        if any(not isinstance(values[name], str) for name in TEXT_FIELDS if name in values):
            raise ValueError
        if 'authors' in values and (not isinstance(values['authors'], list) or not all(isinstance(name, str) for name in values['authors'])):
            raise ValueError
        if 'year' in values and (type(values['year']) is not int or not 1000 <= values['year'] <= 9999):
            raise ValueError
    except (OSError, ValueError, RecursionError):
        raise UploadError(f'无法读取有效的 UTF-8 论文元数据 JSON。{path}') from None
    return values


def prepare_metadata(paper, metadata_path=None, offline=False):
    _pdf_source(paper)
    source = _source_values(paper)
    overrides = _local_metadata(metadata_path)
    fields, provenance = {}, {}
    warnings = []

    def use(values, origin):
        for name in (*TEXT_FIELDS, 'authors', 'year'):
            if values.get(name):
                fields[name] = values[name]
                provenance[name] = origin

    use(source, '源论文 PDF')
    selected_title = overrides.get('title') or fields.get('title', '')
    selected_doi = _doi_from_url(overrides.get('doi') or fields.get('doi', ''))
    if overrides.get('doi') and not selected_doi:
        raise UploadError('请在元数据中填写有效的 DOI。')
    row = _matching_site_article(selected_title, selected_doi)
    if row and selected_title and _match_title(row.get('title', '')) != _match_title(selected_title):
        warnings.append('本地索引中的 DOI 与论文 PDF 标题存在差异，已跳过该索引。')
        row = {}
    if row:
        default_values = {key: value for key, value in row.items() if not fields.get(key)}
        use(default_values, f'本机已收录记录 {row.get("id", "")}')
    doi = selected_doi or _doi_from_url(fields.get('paper_url', ''))
    if doi and 'doi' not in fields:
        use({'doi': doi}, provenance.get('paper_url', '人工核对元数据'))
    if doi and not offline:
        try:
            official = _crossref(doi)
            reference_title = selected_title or fields.get('title', '')
            if reference_title and official['title'] and _match_title(reference_title) != _match_title(official['title']):
                warnings.append('DOI 登记标题与本地材料不同，已保留本地字段，请核对 DOI。')
            else:
                use(official, f'Crossref DOI {doi}')
        except (UploadError, requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            warnings.append('本次未取得有效的 Crossref 登记数据，请在预览中核对书目信息。')
    use(overrides, '人工核对元数据')
    if doi:
        fields['doi'] = doi
        if 'paper_url' not in fields:
            use({'paper_url': 'https://doi.org/' + quote(doi, safe='/')}, 'DOI 登记链接')
    if 'authors' in fields:
        fields['authors'] = [name.strip() for name in fields['authors'] if name.strip()]
    if not fields.get('title', '').strip():
        raise UploadError('未能从源 PDF 确认论文标题。请用 --metadata 提供核对后的书目信息。')
    missing = [label for key, label in (
        ('authors', '作者及顺序'), ('venue', '期刊或会议'), ('year', '出版年份'),
        ('abstract', '摘要'), ('doi', 'DOI'), ('paper_url', '原文链接'),
    ) if not fields.get(key)]
    if missing:
        warnings.append('待核对并补充 ' + '、'.join(missing) + '。')
    return fields, provenance, warnings


def _attachments(paths):
    result = []
    names = set()
    total = 0
    for path in paths:
        _pdf_source(path)
        name = path.name
        if name.casefold() in names or any(ord(char) < 32 or char in '\\/' for char in name):
            raise UploadError(f'附件文件名重复或无效。{name}')
        names.add(name.casefold())
        try:
            with path.open('rb') as source:
                header = source.read(MAX_PDF_BYTES + 1)
        except OSError:
            raise UploadError(f'无法读取附件。{path}') from None
        if len(header) > MAX_PDF_BYTES:
            raise UploadError(f'单个 PDF 附件须在 20 MiB 以内。{path}')
        total += len(header)
        if total > MAX_ATTACHMENTS_BYTES:
            raise UploadError('本次附件总大小须在 23 MiB 以内。')
        result.append((name, header))
    return result


def _preview_link(origin, value):
    if not isinstance(value, str) or any(char.isspace() or char == '\\' or ord(char) < 32 for char in value):
        raise UploadError('服务器返回的草稿链接格式不正确。')
    if not value.startswith('/') or value.startswith('//'):
        raise UploadError('服务器返回了非本机草稿链接。')
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise UploadError('服务器返回的草稿链接格式不正确。') from None
    if parsed.scheme or parsed.netloc:
        raise UploadError('服务器返回了非本机草稿链接。')
    return origin + value


def submit(origin, fields, attachments, token):
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(payload.encode('utf-8'))
    for name, body in attachments:
        digest.update(b'\x00' + name.encode('utf-8') + b'\x00' + hashlib.sha256(body).digest())
    key = 'paper-' + digest.hexdigest()
    files = [('payload', (None, payload, 'application/json'))]
    files.extend(('attachments', (name, body, 'application/pdf')) for name, body in attachments)
    with requests.Session() as client:
        client.trust_env = False
        try:
            response = client.post(
                origin + DRAFT_ENDPOINT, files=files,
                headers={'Authorization': f'Bearer {token}', 'Idempotency-Key': key},
                timeout=(4, 25), allow_redirects=False,
            )
        except requests.RequestException:
            raise UploadError(f'无法确认本机已收到草稿。请检查 {origin} 后用同一材料重试。') from None
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.status_code not in (200, 201) or not isinstance(result, dict):
        detail = result.get('error') if isinstance(result, dict) else ''
        message = ' '.join(detail.replace(token, '[凭证已隐藏]').split())[:350] if isinstance(detail, str) else ''
        raise UploadError(f'草稿提交失败（HTTP {response.status_code}）。{message or "请检查本机预览服务。"}')
    item_id = result.get('id')
    if not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', item_id):
        raise UploadError('服务器未返回有效的草稿编号。')
    returned = result.get('attachments')
    if attachments and not (isinstance(returned, list) and returned and all(isinstance(item, dict) for item in returned)):
        raise UploadError('服务器未确认补充 PDF。请到草稿页核对附件。')
    return {
        'id': item_id, 'created': response.status_code == 201,
        'edit_url': _preview_link(origin, result.get('edit_url')),
        'preview_url': _preview_link(origin, result.get('preview_url')),
        'attachments': returned or [], 'submission_key': key,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paper', type=Path, help='本机论文 PDF')
    parser.add_argument('--supplement', type=Path, action='append', default=[], help='补充 PDF，可重复指定')
    parser.add_argument('--attach-paper', action='store_true', help='把论文 PDF 也作为私有草稿附件')
    parser.add_argument('--metadata', type=Path, help='人工核对的 UTF-8 元数据 JSON')
    parser.add_argument('--offline', action='store_true', help='跳过 Crossref DOI 核对')
    parser.add_argument('--inspect', action='store_true', help='只输出整理结果，便于核对')
    parser.add_argument('--json', action='store_true', help='输出 JSON 回执')
    parser.add_argument('--site', default='http://localhost:8000', help='本机预览地址')
    args = parser.parse_args()
    try:
        origin = local_origin(args.site)
        fields, provenance, warnings = prepare_metadata(args.paper, args.metadata, args.offline)
        paths = ([args.paper] if args.attach_paper else []) + args.supplement
        attachments = _attachments(paths)
        if args.inspect:
            print(json.dumps({'metadata': fields, 'sources': provenance, 'warnings': warnings}, ensure_ascii=False))
            return 0
        result = submit(origin, fields, attachments, draft_token())
    except (UploadError, EOFError) as error:
        print(f'处理失败。{error}', file=sys.stderr)
        return 1
    report = {**result, 'paper': str(args.paper.resolve()), 'attachment_sources': [str(path.resolve()) for path in paths],
              'metadata': fields, 'sources': provenance, 'warnings': warnings, 'published_by_script': False}
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print('已创建论文草稿。' if report['created'] else '已取得已有论文草稿。')
        print('源论文', report['paper'])
        print('标题', fields['title'])
        print('作者', '、'.join(fields.get('authors', [])) or '待核对')
        print('发表信息', fields.get('venue') or '待核对', fields.get('year') or '待核对')
        print('DOI', fields.get('doi') or '待核对')
        for attachment in report['attachments']:
            print('私有附件', attachment.get('name') or attachment.get('filename'))
        for warning in warnings:
            print(warning)
        print('本机预览', report['preview_url'])
        print('请登录编辑账号核对草稿与公开附件，然后决定是否发布。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
