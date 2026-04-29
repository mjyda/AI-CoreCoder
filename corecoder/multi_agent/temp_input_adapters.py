from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


@dataclass(frozen=True)
class TempInputAdapter:
    input_types: tuple[str, ...]
    arg_name: str
    extractor: Callable[[Any, str], str]
    normalizer: Callable[[str], str]
    source: str = "system"


class TempInputAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, TempInputAdapter] = {}
        self._sources: dict[str, set[str]] = {}

    def register(self, adapter: TempInputAdapter) -> None:
        keys: set[str] = set()
        for item in adapter.input_types:
            key = str(item or "").strip().lower()
            if key:
                self._adapters[key] = adapter
                keys.add(key)
        if keys:
            self._sources.setdefault(adapter.source, set()).update(keys)

    def unregister_by_source(self, source: str) -> int:
        key = str(source or "").strip()
        if not key:
            return 0
        names = self._sources.pop(key, set())
        removed = 0
        for item in names:
            adapter = self._adapters.get(item)
            if adapter is not None and adapter.source == key:
                self._adapters.pop(item, None)
                removed += 1
        return removed

    def get(self, input_type: str) -> TempInputAdapter | None:
        key = str(input_type or "").strip().lower()
        if not key:
            return None
        return self._adapters.get(key)

    def describe_supported_inputs(self) -> str:
        rows: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for key in sorted(self._adapters.keys()):
            adapter = self._adapters[key]
            ident = (adapter.arg_name, ",".join(adapter.input_types), adapter.source)
            if ident in seen:
                continue
            seen.add(ident)
            label = f"- {adapter.input_types[0]} -> step['args']['{adapter.arg_name}']"
            if adapter.source != "system":
                label += f" (source={adapter.source})"
            rows.append(label)
        return "\n".join(rows) if rows else "- (none)"

    def build_step_args(self, owner: Any, user_input: str, input_type: str) -> dict[str, Any] | None:
        step_args: dict[str, Any] = {"user_input": user_input}
        adapter = self.get(input_type)
        if adapter is None:
            return step_args
        candidate = adapter.normalizer(adapter.extractor(owner, user_input))
        if not candidate:
            return None
        step_args[adapter.arg_name] = candidate
        return step_args

    def repair_args(self, owner: Any, user_input: str, input_type: str, step_args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        fixed = dict(step_args)
        trace: list[str] = []
        adapter = self.get(input_type)
        if adapter is None:
            return fixed, trace
        original = str(step_args.get(adapter.arg_name, ""))
        cleaned = adapter.normalizer(adapter.extractor(owner, user_input)) or adapter.normalizer(original)
        if cleaned and cleaned != original:
            fixed[adapter.arg_name] = cleaned
            trace.append(f"input_repair={input_type}:{original!r}->{cleaned!r}")
        return fixed, trace

    def register_from_specs(self, specs: Any, *, source: str) -> tuple[bool, str, int]:
        raw_specs = specs if isinstance(specs, list) else []
        adapters: list[TempInputAdapter] = []
        for item in raw_specs:
            adapter = adapter_from_spec(item, source=source)
            if adapter is None:
                return False, "invalid input_adapters spec", 0
            adapters.append(adapter)
        self.unregister_by_source(source)
        for adapter in adapters:
            self.register(adapter)
        return True, "ok", len(adapters)


def _extract_with_regex(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    if match.lastindex:
        return str(match.group(1) or "")
    return str(match.group(0) or "")


def _select_normalizer(name: str) -> Callable[[str], str] | None:
    key = str(name or "").strip().lower()
    if key == "url":
        return normalize_url_candidate
    if key == "email":
        return normalize_email_candidate
    if key == "path":
        return normalize_path_candidate
    if key in ("text", "identity", "raw"):
        return lambda value: str(value or "").strip()
    return None


def adapter_from_spec(spec: Any, *, source: str) -> TempInputAdapter | None:
    if not isinstance(spec, dict):
        return None
    raw_types = spec.get("input_types", [])
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    input_types = tuple(str(item).strip().lower() for item in raw_types if str(item).strip())
    arg_name = str(spec.get("arg_name", "")).strip()
    pattern = str(spec.get("pattern", "")).strip()
    normalizer = _select_normalizer(str(spec.get("normalizer", "text")))
    if not input_types or not arg_name or not pattern or normalizer is None:
        return None

    def _extractor(_owner: Any, text: str, *, _pattern: str = pattern) -> str:
        return _extract_with_regex(text, _pattern)

    return TempInputAdapter(
        input_types=input_types,
        arg_name=arg_name,
        extractor=_extractor,
        normalizer=normalizer,
        source=source,
    )


def normalize_url_candidate(value: str) -> str:
    text = (value or "").strip().strip("\"'")
    if not text:
        return ""
    text = re.split(r"[\s<>\u3000]+", text, maxsplit=1)[0]
    text = re.sub(r"(这个网页内容|这个网页|网页内容|网页|这个链接内容|这个链接|链接内容|链接)$", "", text)
    text = text.rstrip("，。,.;；:：!！?？)）]】>}」』")
    return text


def extract_first_url(_owner: Any, text: str) -> str:
    match = re.search(r"https?://\S+", text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return normalize_url_candidate(match.group(0))


def normalize_email_candidate(value: str) -> str:
    text = (value or "").strip().strip("\"'")
    return text.rstrip("，。,.;；:：!！?？)）]】>}」』")


def extract_email_candidate(owner: Any, text: str) -> str:
    return str(owner._extract_email_address(text) or "")


def normalize_path_candidate(value: str) -> str:
    text = (value or "").strip().strip("\"'")
    if not text:
        return ""
    return text.rstrip("，。,.;；:：!！?？")


def extract_path_candidate(owner: Any, text: str) -> str:
    return str(owner._extract_windows_path(text) or owner._resolve_followup_file_path(text) or "")


def create_default_temp_input_adapter_registry() -> TempInputAdapterRegistry:
    registry = TempInputAdapterRegistry()
    registry.register(
        TempInputAdapter(
            input_types=("url",),
            arg_name="url",
            extractor=extract_first_url,
            normalizer=normalize_url_candidate,
        )
    )
    registry.register(
        TempInputAdapter(
            input_types=("source_file", "file_path", "path"),
            arg_name="file_path",
            extractor=extract_path_candidate,
            normalizer=normalize_path_candidate,
        )
    )
    registry.register(
        TempInputAdapter(
            input_types=("email", "recipient"),
            arg_name="recipient",
            extractor=extract_email_candidate,
            normalizer=normalize_email_candidate,
        )
    )
    return registry
