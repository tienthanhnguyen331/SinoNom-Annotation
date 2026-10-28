"""Read portable annotation JSON files and recovery ZIP archives."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile


EXPORT_FILES = {
    'text_annotations.json',
    'inscription_content.json',
    'source_mismatches.json',
    'annotation_drafts.json',
}


def _json_bytes(data: bytes, name: str):
    try:
        return json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{name} is not valid UTF-8 JSON: {exc}') from exc


def read_import_payload(path):
    """Load one JSON file or the JSON members from an annotation ZIP."""
    path = Path(path)
    if path.suffix.casefold() != '.zip':
        try:
            if path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError('The uploaded JSON is too large to import safely.')
            return {'upload.json': json.loads(path.read_text(encoding='utf-8'))}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'Cannot read uploaded JSON: {exc}') from exc
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist()
                     if Path(name).name in EXPORT_FILES]
            if not names:
                raise ValueError('The ZIP contains no supported annotation JSON files.')
            payload = {}
            total_size = 0
            for name in names:
                member_name = Path(name).name
                if member_name in payload:
                    raise ValueError(
                        f'The ZIP contains more than one {member_name}; import is ambiguous.'
                    )
                total_size += archive.getinfo(name).file_size
                if total_size > 200 * 1024 * 1024:
                    raise ValueError('The annotation data in this ZIP is too large to import safely.')
                if archive.getinfo(name).file_size > 100 * 1024 * 1024:
                    raise ValueError(f'{Path(name).name} is too large to import safely.')
                payload[member_name] = _json_bytes(archive.read(name), name)
            return payload
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f'Cannot read uploaded ZIP: {exc}') from exc


def _normalized_code(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value)
    return str(int(value)) if value.isdigit() else value


def _matches_image(record, code, image_name):
    if not isinstance(record, dict):
        return False
    image = record.get('image')
    inscription_code = record.get('inscription_code')
    expected_stem = Path(image_name).stem
    aliases = {code, expected_stem}
    aliases |= {value for value in (_normalized_code(code),
                                    _normalized_code(expected_stem))
                if value is not None}
    if image is not None:
        if not isinstance(image, str):
            return False
        image_stem = Path(image).stem
        if image != image_name and image_stem not in aliases:
            return False
    if inscription_code is not None and str(inscription_code) not in aliases:
        return False
    return image is not None or inscription_code is not None


def _category(record):
    if not isinstance(record, dict):
        return None
    if record.get('format') == 'vietnamica-annotation-draft':
        return 'draft'
    if {'image', 'inscription_code', 'content'}.issubset(record):
        return 'content'
    if {'image', 'bounding_boxes'}.issubset(record):
        return 'mismatch' if 'issue_type' in record else 'annotation'
    return None


def select_image_records(payload, code, image_name):
    """Select at most one imported record per data category for an image."""
    selected = {'annotation': None, 'content': None, 'mismatch': None,
                'draft': None}
    for name, document in payload.items():
        if isinstance(document, list):
            records = document
        elif isinstance(document, dict):
            records = [document]
        else:
            raise ValueError(f'{name} must contain a JSON object or array.')
        for record in records:
            category = _category(record)
            if not category or not _matches_image(record, code, image_name):
                continue
            if selected[category] is not None:
                raise ValueError(
                    f'The upload contains multiple {category} records for {code}.'
                )
            selected[category] = record
    if selected['annotation'] is not None and selected['mismatch'] is not None:
        raise ValueError('The upload contains both a normal annotation and a source mismatch for this image.')
    if not any(selected.values()):
        raise ValueError(
            f'No supported annotation record matched image code {code}. '
            'Upload an annotation JSON, a recovery ZIP, or one of the exported JSON files.'
        )
    return selected
