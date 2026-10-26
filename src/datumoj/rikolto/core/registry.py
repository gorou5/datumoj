"""YAML loader for the source registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from datumoj.rikolto.core.contracts import Registry, RegistryError, SourceSpec
from datumoj.rikolto.core.models import Redistribution


class RegistryImpl:
    def __init__(self, specs: Iterable[SourceSpec] = ()):
        self._specs: dict[str, SourceSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise ValueError(f"来源 id 重复：{spec.id}")
            self._specs[spec.id] = spec

    def get(self, source_id: str) -> SourceSpec | None:
        return self._specs.get(source_id)

    def all(self) -> list[SourceSpec]:
        return [self._specs[key] for key in sorted(self._specs)]


def _check_plaintext_keys(data: Any, path: Path, field: str = "") -> None:
    if isinstance(data, Mapping):
        for key, value in data.items():
            name = str(key)
            field_path = f"{field}.{name}" if field else name
            if any(part in name.casefold() for part in ("key", "token", "secret")):
                if value is not None and (not isinstance(value, str) or not value.startswith("env:")):
                    raise RegistryError(f"{path}: {field_path}: 凭据必须写成 env:NAME")
            _check_plaintext_keys(value, path, field_path)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            _check_plaintext_keys(value, path, f"{field}[{index}]")


def _validate_file(path: Path) -> SourceSpec:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        raise RegistryError(f"{path}: file: 无法读取 YAML 文件") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        field = f"line {mark.line + 1}" if mark else "yaml"
        raise RegistryError(f"{path}: {field}: YAML 格式错误") from None

    if not isinstance(data, Mapping):
        raise RegistryError(f"{path}: document: 顶层必须是映射")
    _check_plaintext_keys(data, path)

    try:
        spec = SourceSpec.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) or "document" for error in exc.errors()})
        raise RegistryError(f"{path}: {', '.join(fields)}: 来源字段校验失败") from None

    if spec.license.redistribution is Redistribution.allowed:
        missing = [
            name for name, value in (
                ("license.name", spec.license.name),
                ("license.url", spec.license.url),
                ("license.attribution", spec.license.attribution),
                ("license.verified_at", spec.license.verified_at),
            ) if not value
        ]
        if missing:
            raise RegistryError(f"{path}: {', '.join(missing)}: 放开再分发前必须完成许可核对")
    return spec


def load_registry(directory: str | Path) -> Registry:
    """Load and validate every ``*.yaml`` source file in a directory."""
    directory = Path(directory)
    if not directory.is_dir():
        raise RegistryError(f"{directory}: directory: 来源目录不存在")

    specs: dict[str, SourceSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = _validate_file(path)
        if spec.id in specs:
            raise RegistryError(f"{path}: id: 来源 id 重复：{spec.id}")
        if path.stem != spec.id:
            raise RegistryError(f"{path}: id: id={spec.id!r} 与文件名不一致")
        specs[spec.id] = spec
    return RegistryImpl(specs.values())


__all__ = ["RegistryImpl", "load_registry"]
