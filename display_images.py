"""Serve prebuilt display sizes for the site's shipped public images."""

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path


@lru_cache(maxsize=128)
def _source_digest(path, stamp):
    return sha256(path.read_bytes()).hexdigest()


def register_display_images(app, roots, static_asset_url):
    """Use a prebuilt variant only when it still matches the published original."""
    image_dir = Path(app.static_folder) / 'images' / 'display'
    manifest = json.loads((image_dir / 'manifest.json').read_text(encoding='utf-8'))

    def display_image(kind, filename):
        entry = manifest.get(f'{kind}/{filename}')
        if entry is None:
            return None
        path = Path(roots[kind]) / filename
        try:
            stat = path.stat()
            if stat.st_size != entry['size']:
                return None
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
            if _source_digest(path, stamp) != entry['sha256']:
                return None
        except OSError:
            return None

        return {
            'width': entry['width'],
            'height': entry['height'],
            'srcset': ', '.join(
                f"{static_asset_url('images/display/' + name)} {width}w"
                for name, width in entry['variants']
            ),
        }

    app.jinja_env.globals.update(display_image=display_image)
