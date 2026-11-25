"""Set up an offline content checkout for the localhost preview."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


BRANCH = 'preview'


def offline_git_env():
    env = {name: value for name, value in os.environ.items() if not name.upper().startswith('GIT_')}
    env.update(GIT_ALLOW_PROTOCOL='file', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git(directory, *args, check=True):
    result = subprocess.run(
        ['git', *args], cwd=directory, env=offline_git_env(), capture_output=True,
        text=True, encoding='utf-8', errors='replace',
    )
    if check and result.returncode:
        raise RuntimeError(f'Preview Git {args[0]} failed: {result.stderr.strip()}')
    return result


def prepare_content_checkout(state_dir: Path, seed: Path) -> Path:
    """Seed once from a copy. Later launches keep the existing checkout and edits."""
    checkout = state_dir / 'content-workspace'
    checkout.mkdir(parents=True, exist_ok=True)
    content = checkout / 'site_content'
    if not content.exists():
        with TemporaryDirectory(prefix='seed-', dir=state_dir) as staging:
            staged = Path(staging) / 'site_content'
            shutil.copytree(seed, staged)
            staged.rename(content)

    if not (checkout / '.git').is_dir():
        _git(checkout, 'init', f'--initial-branch={BRANCH}')
    if _git(checkout, 'symbolic-ref', '--short', 'HEAD').stdout.strip() != BRANCH:
        raise RuntimeError('The localhost content workspace must use the preview branch.')

    if _git(checkout, 'rev-parse', '--verify', 'HEAD', check=False).returncode:
        _git(checkout, 'config', '--local', 'user.name', 'OPTICompt Lab Preview')
        _git(checkout, 'config', '--local', 'user.email', 'preview@opticom.invalid')
        _git(checkout, 'add', '--', 'site_content')
        _git(checkout, 'commit', '--no-gpg-sign', '-m', 'Seed localhost content', '--', 'site_content')

    remote = state_dir / 'content-origin.git'
    if not (remote / 'HEAD').is_file():
        _git(state_dir, 'init', '--bare', f'--initial-branch={BRANCH}', str(remote))
    origin = remote.resolve().as_uri()
    remotes = _git(checkout, 'remote').stdout.splitlines()
    if 'origin' not in remotes:
        _git(checkout, 'remote', 'add', 'origin', origin)
    fetch_url = _git(checkout, 'remote', 'get-url', 'origin').stdout.strip()
    push_url = _git(checkout, 'remote', 'get-url', '--push', 'origin').stdout.strip()
    if (fetch_url, push_url) != (origin, origin):
        raise RuntimeError('The localhost content workspace must use its own local Git remote.')

    ref = f'refs/heads/{BRANCH}'
    if _git(remote, 'show-ref', '--verify', '--quiet', ref, check=False).returncode:
        _git(checkout, 'push', '--set-upstream', 'origin', BRANCH)
    return content
