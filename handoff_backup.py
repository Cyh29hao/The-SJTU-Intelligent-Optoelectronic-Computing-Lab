"""Portable backup of published content and the private handoff workflow."""

import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path, PurePosixPath
from tempfile import SpooledTemporaryFile, TemporaryDirectory
from unicodedata import normalize
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


CONTENT_PREFIX = 'content-workspace/site_content'
DRAFT_PREFIX = 'record-workflow/draft-files'
DATABASE_MEMBER = 'record-workflow/workflow.sqlite3'
SITE_JSON = {'site.json': dict, 'articles.json': list, 'people.json': list, 'news.json': list}
REQUIRED_MEMBERS = frozenset({DATABASE_MEMBER, *(f'{CONTENT_PREFIX}/{name}' for name in SITE_JSON)})
MAX_MEMBERS = 20000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
_PRIVATE_NAMES = frozenset({'preview-credentials.json', 'news.sqlite3', 'workflow.sqlite3'})
_DOS_DEVICE = re.compile(r'CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]', re.IGNORECASE)


class HandoffBackupError(ValueError):
    """The backup cannot be made or restored safely."""


def _excluded(name):
    return name.startswith('.') or name.casefold() in _PRIVATE_NAMES


def backup_member_path(name):
    parts = name.split('/')
    for part in parts:
        device_name = part.partition('.')[0].rstrip(' ')
        if (not part or part in ('.', '..') or part.endswith((' ', '.')) or _DOS_DEVICE.fullmatch(device_name)
                or any(ord(char) < 32 or char in '\\:<>"|?*' for char in part)):
            raise HandoffBackupError(f'无效的备份路径 {name!r}。')
    if name == DATABASE_MEMBER:
        return PurePosixPath(*parts)
    prefix = '/'.join(parts[:2])
    if prefix not in (CONTENT_PREFIX, DRAFT_PREFIX) or len(parts) < 3 or any(_excluded(part) for part in parts[2:]):
        raise HandoffBackupError(f'备份中存在未知路径 {name!r}。')
    return PurePosixPath(*parts)


def validated_backup_members(archive):
    members = archive.infolist()
    if not members or len(members) > MAX_MEMBERS:
        raise HandoffBackupError(f'备份文件数量必须在 1 到 {MAX_MEMBERS} 之间。')
    total_size = 0
    files = set()
    directories = set()
    checked = []
    for member in members:
        # ZipFile.write() retains host separators in orig_filename until the ZIP is read again.
        name = member.filename if archive.mode == 'w' else member.orig_filename
        path = backup_member_path(name)
        file_type = stat.S_IFMT(member.external_attr >> 16)
        if (member.is_dir() or file_type not in (0, stat.S_IFREG) or member.external_attr & (0x10 | 0x400)
                or member.flag_bits & 1 or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
            raise HandoffBackupError(f'备份成员 {member.filename!r} 必须是普通未加密文件。')
        key = normalize('NFC', path.as_posix()).casefold()
        parents = {normalize('NFC', parent.as_posix()).casefold() for parent in path.parents}
        if key in files or key in directories or parents.intersection(files):
            raise HandoffBackupError(f'备份路径 {member.filename!r} 重复或冲突。')
        files.add(key)
        directories.update(parents)
        total_size += member.file_size
        if member.file_size > MAX_MEMBER_BYTES or total_size > MAX_ARCHIVE_BYTES:
            raise HandoffBackupError('备份的解压大小超过上限。')
        checked.append((member, path))
    if REQUIRED_MEMBERS.difference(path.as_posix() for _, path in checked):
        raise HandoffBackupError('备份缺少公开内容或草稿数据库。')
    return checked


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _raise_walk_error(error):
    raise error


def _backup_files(root, prefix):
    if _linked(root):
        raise HandoffBackupError(f'备份目录 {root.name!r} 不能是链接。')
    if not root.exists():
        return
    for directory, folders, names in os.walk(root, onerror=_raise_walk_error):
        base = Path(directory)
        folders[:] = sorted(folder for folder in folders if not _excluded(folder))
        for folder in folders:
            if _linked(base / folder):
                raise HandoffBackupError(f'备份目录 {folder!r} 不能是链接。')
        for name in sorted(names):
            if _excluded(name):
                continue
            path = base / name
            if _linked(path) or not path.is_file():
                raise HandoffBackupError(f'备份内容 {name!r} 必须是普通文件。')
            member = f'{prefix}/{path.relative_to(root).as_posix()}'
            backup_member_path(member)
            yield member, path


def build_handoff_backup(workflow):
    """Return a seekable ZIP whose stored files and SQLite pages share one write lock."""
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    try:
        with workflow.content_write() as db:
            with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
                # Dump the same transaction used to recover updates and lock the published files.
                with TemporaryDirectory(prefix='handoff-snapshot-') as temporary:
                    snapshot_path = Path(temporary) / 'workflow.sqlite3'
                    with closing(sqlite3.connect(snapshot_path)) as snapshot:
                        snapshot.executescript('\n'.join(db.iterdump()))
                    archive.write(snapshot_path, DATABASE_MEMBER)
                for root, prefix in ((workflow.content_root, CONTENT_PREFIX), (workflow.file_dir, DRAFT_PREFIX)):
                    for member, path in _backup_files(root, prefix):
                        archive.write(path, member)
                validated_backup_members(archive)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
