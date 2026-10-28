# i18n

## Overview

Lookup of user-facing strings for English, Brazilian Portuguese, and Spanish. Catalogs are TOML files next to this package. Callers pass a dotted key; a missing translation falls back to English, then to the key itself.

## Interface

- [`LANGUAGES`](./__init__.py): `en`, `pt-BR`, `es` and their English names. Not a gate on lookup.
- [`t`](./__init__.py): `t(key, language=None, /, **values) -> str`. Other modules call `i18n.t`.
- [`words`](./__init__.py): `words(key, language=None) -> list[str]`.
- [`language_name`](./__init__.py): `language_name(language=None) -> str`. Returns `t("language_name_in_english", language)`.

## Invariants

- [`t`](./__init__.py): a missing key falls back to the English catalog. If that is also missing, `t` returns the key string and does not raise.
- [`t`](./__init__.py): a falsy `language` uses [`config.LANGUAGE`](../config.py). `t` does not consult `LANGUAGES`.
- [`t`](./__init__.py): `.format(**values)` runs only when `values` is non-empty. Format errors are not caught.
- [`words`](./__init__.py): a list node is returned as a list and does not fall back to English. A non-list node falls back to English; a missing English node becomes `[]`.
- [`_find`](./__init__.py): `key.split(".")` walks nested dicts. Returns `None` if an intermediate is not a dict or a segment is absent.
- [`_catalog`](./__init__.py): a missing `{language}.toml` is an empty catalog. A present file is parsed with `tomllib.loads`; decode errors are not caught.

## Side Effects

- [`_catalog`](./__init__.py): on a cache miss, reads the TOML file as UTF-8. `@cache` keeps that dict for the process. This package does not write, and it does not call the network.
- Importing this package imports [`config`](../config.py), which reads `config.toml` and `secrets.env` at import. This package does not read those files itself.

## Verification

- Run: `uv run python -m unittest discover -s tests`
