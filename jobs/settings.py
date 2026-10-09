"""Shared, non-secret application settings, independent of the working directory."""

import json
from pathlib import Path

SETTINGS = json.loads((Path(__file__).resolve().parents[1] / 'config' / 'settings.json').read_text(encoding='utf-8'))

# Reserve four fund sources, three sector sources and one portfolio source.
if not 1 <= SETTINGS['collection']['maxPositions'] <= 32 - len(SETTINGS['watchlist']['benchmarks']):
    raise ValueError('collection.maxPositions and benchmarks exceed the report source limit')
if SETTINGS['watchlist']['etfReferenceCode'] not in SETTINGS['watchlist']['etfs']:
    raise ValueError('watchlist.etfReferenceCode must be included in watchlist.etfs')
