"""Restore a localhost handoff backup to a fresh state directory."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile
from zlib import error as DeflateError

from handoff_backup import (
    CONTENT_PREFIX, DATABASE_MEMBER, DRAFT_PREFIX, SITE_JSON, HandoffBackupError, validated_backup_members,
)


_TABLES = {'drafts', 'submissions', 'publication_journal', 'markers'}


def _extract(archive, members, stage):
    for member, path in members:
        destination = stage.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with archive.open(member) as source, destination.open('xb') as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > member.file_size:
                    raise HandoffBackupError(f'备份成员 {path} 的大小与声明不符。')
                output.write(chunk)
        if size != member.file_size:
            raise HandoffBackupError(f'备份成员 {path} 的大小与声明不符。')


def _validate_state(stage):
    for name, expected in SITE_JSON.items():
        with (stage / CONTENT_PREFIX / name).open(encoding='utf-8-sig') as handle:
            if not isinstance(json.load(handle), expected):
                raise HandoffBackupError(f'备份中的 {name} 格式无效。')
    database = stage / DATABASE_MEMBER
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro&immutable=1', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        tables = {name for name, in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if tables != _TABLES or db.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
            raise HandoffBackupError('备份中的草稿数据库损坏或结构不符。')


def _install_state(stage, destination):
    if os.name == 'nt':
        # On Windows rename refuses any existing destination, even an empty directory.
        stage.rename(destination)
        return
    # Reserve a new destination here because POSIX rename can replace an empty directory.
    destination.mkdir()
    try:
        stage.replace(destination)
    except OSError:
        destination.rmdir()
        raise


def restore_handoff_backup(backup, state_dir):
    """Extract, verify and install a fresh state next to the staging directory."""
    target = Path(state_dir).expanduser().absolute()
    target = target.parent.resolve() / target.name
    if target.exists() or target.is_symlink():
        raise HandoffBackupError(f'目录 {target} 已经存在，请选择新路径。')
    with ZipFile(backup) as archive:
        members = validated_backup_members(archive)
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='.handoff-restore-', dir=target.parent) as temporary:
            stage = Path(temporary) / 'state'
            _extract(archive, members, stage)
            (stage / DRAFT_PREFIX).mkdir(parents=True, exist_ok=True)
            _validate_state(stage)
            _install_state(stage, target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        target = restore_handoff_backup(args.backup, args.state_dir)
    except (BadZipFile, DeflateError, EOFError, HandoffBackupError, OSError, RecursionError,
            UnicodeError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f'恢复失败。{error}', file=sys.stderr)
        return 1
    print(f'已恢复到 {target}')
    print(f'python -X utf8 -m scripts.run_news_preview --handoff --state-dir "{target}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
