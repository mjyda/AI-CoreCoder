from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import shutil
from types import ModuleType
from typing import Any, Callable

from .temp_input_adapters import adapter_from_spec


@dataclass
class TempCapabilityRecord:
    name: str
    action_name: str
    route: str
    description: str
    source_requirement: str
    file_path: str
    metadata_path: str
    trigger_keywords: list[str]
    default_target: str
    self_test_plan: dict[str, Any]
    created_at: str
    status: str  # temp | kept | discarded


class TempCapabilityRegistry:
    def __init__(self, owner: Any, temp_root: Path, kept_root: Path):
        self._owner = owner
        self.temp_root = temp_root.resolve()
        self.kept_root = kept_root.resolve()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.kept_root.mkdir(parents=True, exist_ok=True)

        self.records: dict[str, TempCapabilityRecord] = {}
        self.step_executors: dict[str, Callable[[dict[str, Any], dict[str, str], list[str]], str | None]] = {}
        self.action_specs: dict[str, dict[str, Any]] = {}
        self.modules: dict[str, ModuleType] = {}
        self.autoload_kept = False

    @staticmethod
    def _metadata_path_for(file_path: Path) -> Path:
        return file_path.with_suffix(".meta.json")

    @staticmethod
    def _normalize_keywords(raw: Any) -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            items = re.split(r"[\s,，;；]+", raw)
        else:
            items = []
        for item in items:
            v = str(item).strip().lower()
            if v and v not in out:
                out.append(v)
        return out

    def _write_metadata(self, record: TempCapabilityRecord) -> None:
        path = Path(record.metadata_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_metadata(self, file_path: Path) -> dict[str, Any]:
        meta_path = self._metadata_path_for(file_path)
        if not meta_path.is_file():
            return {}
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _build_executor(
        self,
        func: Callable[[Any, dict[str, Any], dict[str, str], list[str]], str | None],
    ) -> Callable[[dict[str, Any], dict[str, str], list[str]], str | None]:
        def _wrapped(step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
            out = func(self._owner, step, intermediate, evidence)
            if out is None:
                return None
            if isinstance(out, (dict, list)):
                intermediate["working_text"] = json.dumps(out, ensure_ascii=False, indent=2)
                return None
            text = str(out)
            if text.lower().startswith(("error:", "failed", "exception")):
                return text
            intermediate["working_text"] = text
            return None

        return _wrapped

    def validate_candidate(self, file_path: Path) -> dict[str, Any]:
        path = file_path.expanduser().resolve()
        if not path.is_file():
            return {"ok": False, "error": f"file not found: {path}"}
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"read failed: {exc}"}
        protocol_violations: list[str] = []
        if re.search(r"intermediate\[['\"](?:url|file_path|path|query|input|text)['\"]\]", source):
            protocol_violations.append("raw input must not be read from intermediate[...]")
        if re.search(r"(?<![\w'\"])\bargs\[[\'\"]", source):
            protocol_violations.append("use step['args'][...] instead of bare args[...]")
        # Hard rule: block nested execute definitions which cause false-success (Done.)
        # Pattern: outer execute(owner, ...) contains another `def execute(...)`.
        nested_execute = re.search(
            r"def\s+execute\(\s*owner\s*:\s*Any\s*,.*?\)\s*->\s*str\s*\|\s*None\s*:\s*[\r\n]+(?:[ \t].*[\r\n]+)*?[ \t]+def\s+execute\(",
            source,
            flags=re.DOTALL,
        )
        if nested_execute:
            protocol_violations.append(
                "invalid temp executor structure: nested def execute(...) inside top-level execute(...) is forbidden"
            )
        # Hard signature check: owner._get_tool must be called as owner._get_tool(route, name)
        # to avoid runtime crashes like missing positional arg / unexpected kwargs.
        for m in re.finditer(r"owner\._get_tool\s*\((.*?)\)", source, flags=re.DOTALL):
            args_src = str(m.group(1) or "").strip()
            if not args_src:
                protocol_violations.append("_get_tool signature invalid: expected owner._get_tool(route, name)")
                continue
            if re.search(r"\b(url|args)\s*=", args_src):
                protocol_violations.append("_get_tool signature invalid: unexpected kwargs (url/args)")
                continue
            # Simple argument counting (good enough for generated snippets).
            argc = len([x for x in re.split(r"\s*,\s*", args_src) if x.strip()])
            if argc != 2:
                protocol_violations.append(
                    f"_get_tool signature invalid: expected 2 args (route,name), got {argc}"
                )
        if protocol_violations:
            return {
                "ok": False,
                "error": "input protocol violation: " + "; ".join(protocol_violations),
                "snapshot": {"source_excerpt": source[:1200]},
            }
        try:
            compile(source, str(path), "exec")
        except Exception as exc:
            return {"ok": False, "error": f"compile failed: {exc}"}

        try:
            module_name = f"corecoder_temp_validate_{path.stem}_{int(datetime.now(timezone.utc).timestamp())}"
            spec = spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return {"ok": False, "error": f"unable to load spec: {path}"}
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            return {"ok": False, "error": f"import failed: {exc}"}

        register = getattr(module, "register", None)
        if not callable(register):
            return {"ok": False, "error": "register() is missing or not callable"}
        try:
            payload = register()
        except Exception as exc:
            return {"ok": False, "error": f"register() failed: {exc}"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "register() must return dict"}
        action_name = str(payload.get("action_name", "")).strip()
        action_spec = payload.get("action_spec", {})
        executor = payload.get("executor")
        input_adapters = payload.get("input_adapters", [])
        if not action_name:
            return {"ok": False, "error": "action_name is empty"}
        if not isinstance(action_spec, dict):
            return {"ok": False, "error": "action_spec must be dict"}
        if not callable(executor):
            return {"ok": False, "error": "executor is not callable"}
        if input_adapters not in (None, []) and not isinstance(input_adapters, list):
            return {"ok": False, "error": "input_adapters must be a list when provided"}
        adapter_specs: list[dict[str, Any]] = []
        for item in input_adapters or []:
            if adapter_from_spec(item, source=f"validate:{action_name}") is None:
                return {"ok": False, "error": "invalid input_adapters entry"}
            adapter_specs.append(dict(item))
        meta = self._read_metadata(path)
        self_test_plan = meta.get("self_test_plan", {})
        if self_test_plan and isinstance(self_test_plan, dict):
            args = self_test_plan.get("args", {})
            if not isinstance(args, dict):
                return {"ok": False, "error": "self_test_plan.args must be dict"}
            evidence: list[str] = []
            intermediate: dict[str, str] = {}
            snapshot: dict[str, Any] = {
                "args": args,
                "evidence": [],
                "working_text": "",
                "returned": None,
                "exception": "",
            }
            try:
                dry_run_out = executor(
                    self._owner,
                    {"route": str(payload.get("action_spec", {}).get("route", "file")), "action": action_name, "args": args},
                    intermediate,
                    evidence,
                )
                snapshot["returned"] = dry_run_out if isinstance(dry_run_out, (dict, list, str, int, float, bool)) or dry_run_out is None else repr(dry_run_out)
                snapshot["evidence"] = list(evidence)
                snapshot["working_text"] = str(intermediate.get("working_text", ""))
            except Exception as exc:
                snapshot["exception"] = str(exc)
                snapshot["evidence"] = list(evidence)
                snapshot["working_text"] = str(intermediate.get("working_text", ""))
                return {"ok": False, "error": f"self_test_plan execution failed: {exc}", "snapshot": snapshot}
            if isinstance(dry_run_out, str) and dry_run_out.lower().startswith(("error:", "failed", "exception")):
                return {"ok": False, "error": f"self_test_plan returned error: {dry_run_out}", "snapshot": snapshot}
            must_contain = self_test_plan.get("expect_working_text_contains", [])
            if isinstance(must_contain, str):
                must_contain = [must_contain]
            working_text = str(intermediate.get("working_text", ""))
            for item in must_contain if isinstance(must_contain, list) else []:
                token = str(item).strip()
                if token and token not in working_text:
                    return {
                        "ok": False,
                        "error": f"self_test_plan assertion failed: missing {token!r} in working_text",
                        "snapshot": snapshot,
                    }
        return {
            "ok": True,
            "payload": {
                "name": str(payload.get("name", "")).strip(),
                "action_name": action_name,
                "description": str(payload.get("description", "")).strip(),
                "self_test_plan": self_test_plan if isinstance(self_test_plan, dict) else {},
                "input_adapters": adapter_specs,
            },
        }

    def list_records(self) -> list[TempCapabilityRecord]:
        return sorted(self.records.values(), key=lambda x: x.created_at)

    def get_record(self, name: str) -> TempCapabilityRecord | None:
        return self.records.get(name)

    def load_from_file(
        self,
        file_path: Path,
        *,
        source_requirement: str = "",
        metadata: dict[str, Any] | None = None,
        status: str = "temp",
    ) -> TempCapabilityRecord:
        path = file_path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)

        module_name = f"corecoder_temp_{path.stem}_{int(datetime.now(timezone.utc).timestamp())}"
        spec = spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load temp capability: {path}")

        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "register"):
            raise RuntimeError("Temp capability must expose register()")

        payload = module.register()
        if not isinstance(payload, dict):
            raise RuntimeError("register() must return a dict")

        action_name = str(payload.get("action_name", "")).strip()
        action_spec = dict(payload.get("action_spec", {}) or {})
        executor_func = payload.get("executor")
        input_adapters = payload.get("input_adapters", [])
        if not action_name or not callable(executor_func):
            raise RuntimeError("Temp capability must provide action_name and callable executor")
        if input_adapters not in (None, []) and not isinstance(input_adapters, list):
            raise RuntimeError("Temp capability input_adapters must be a list")

        name = str(payload.get("name", "")).strip() or action_name
        route = str(action_spec.get("route", "")).strip() or "file"
        description = str(payload.get("description", "")).strip() or f"Temp capability {name}"
        meta = dict(self._read_metadata(path))
        if isinstance(metadata, dict):
            meta.update(metadata)
        trigger_keywords = self._normalize_keywords(
            meta.get("trigger_keywords", payload.get("trigger_keywords", []))
        )
        default_target = str(
            meta.get("default_target", payload.get("default_target", ""))
        ).strip()
        self_test_plan = meta.get("self_test_plan", {})
        if not isinstance(self_test_plan, dict):
            self_test_plan = {}
        metadata_path = str(self._metadata_path_for(path).resolve())
        adapter_source = f"temp_capability:{name}"
        ok, adapter_detail, _count = self._owner._temp_input_adapters.register_from_specs(
            input_adapters or [],
            source=adapter_source,
        )
        if not ok:
            raise RuntimeError(f"failed to register input adapters: {adapter_detail}")

        self.modules[name] = module
        self.step_executors[action_name] = self._build_executor(executor_func)
        self.action_specs[action_name] = action_spec

        record = TempCapabilityRecord(
            name=name,
            action_name=action_name,
            route=route,
            description=description,
            source_requirement=str(source_requirement or meta.get("source_requirement", "")).strip(),
            file_path=str(path),
            metadata_path=metadata_path,
            trigger_keywords=trigger_keywords,
            default_target=default_target,
            self_test_plan=self_test_plan,
            created_at=datetime.now(timezone.utc).isoformat(),
            status=status,
        )
        self._write_metadata(record)
        self.records[name] = record
        return record

    def export_record_dict(self, name: str) -> dict[str, Any] | None:
        record = self.records.get(name)
        return asdict(record) if record else None

    def discard(self, name: str, *, delete_file: bool = True) -> bool:
        record = self.records.get(name)
        if record is None:
            return False
        self.step_executors.pop(record.action_name, None)
        self.action_specs.pop(record.action_name, None)
        self.modules.pop(name, None)
        self._owner._temp_input_adapters.unregister_by_source(f"temp_capability:{name}")
        path = Path(record.file_path)
        meta_path = Path(record.metadata_path)
        record.status = "discarded"
        self.records.pop(name, None)
        if delete_file:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                meta_path.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def keep(self, name: str) -> tuple[bool, str]:
        record = self.records.get(name)
        if record is None:
            return False, "not found"
        src = Path(record.file_path)
        if not src.is_file():
            self.discard(name, delete_file=False)
            return False, "file missing"
        dst = self.kept_root / src.name
        src_meta = Path(record.metadata_path)
        dst_meta = self._metadata_path_for(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        if src_meta.is_file():
            shutil.move(str(src_meta), str(dst_meta))
        self.step_executors.pop(record.action_name, None)
        self.action_specs.pop(record.action_name, None)
        self.modules.pop(name, None)
        self._owner._temp_input_adapters.unregister_by_source(f"temp_capability:{name}")
        record.file_path = str(dst.resolve())
        record.metadata_path = str(dst_meta.resolve())
        record.status = "kept"
        self._write_metadata(record)
        self.records.pop(name, None)
        return True, str(dst.resolve())

    def clear_temp(self) -> int:
        names = [name for name, record in self.records.items() if record.status == "temp"]
        for name in names:
            self.discard(name, delete_file=True)
        return len(names)

    def resolve_candidate(self, value: str) -> Path:
        raw = (value or "").strip().strip("\"'")
        p = Path(raw).expanduser()
        if p.is_file():
            return p.resolve()
        if not p.suffix:
            p = p.with_suffix(".py")
        temp_candidate = (self.temp_root / p.name).resolve()
        if temp_candidate.is_file():
            return temp_candidate
        kept_candidate = (self.kept_root / p.name).resolve()
        if kept_candidate.is_file():
            return kept_candidate
        return p.resolve()

    def list_kept_files(self) -> list[Path]:
        if not self.kept_root.is_dir():
            return []
        return sorted(self.kept_root.glob("*.py"))

    def set_autoload_kept(self, enabled: bool) -> None:
        self.autoload_kept = bool(enabled)

    def autoload_kept_capabilities(self) -> list[str]:
        loaded: list[str] = []
        for path in self.list_kept_files():
            try:
                record = self.load_from_file(path, source_requirement="autoload_kept", status="kept")
                loaded.append(f"{record.name} ({record.action_name})")
            except Exception:
                continue
        return loaded

    def match_query(self, text: str) -> TempCapabilityRecord | None:
        query = (text or "").strip().lower()
        if not query:
            return None
        best: tuple[int, TempCapabilityRecord] | None = None
        for record in self.list_records():
            score = 0
            action_spec = self.action_specs.get(record.action_name, {})
            input_type = str(action_spec.get("input_type", "")).strip().lower()
            for kw in record.trigger_keywords:
                if kw and kw in query:
                    score += 3 if len(kw) > 2 else 1
            if record.default_target and record.default_target.lower() in query:
                score += 2
            requirement = record.source_requirement.lower()
            if "github" in requirement and any(k in query for k in ("github", "仓库", "readme", "提交", "文件")):
                score += 2
            if input_type == "url" and re.search(r"https?://\S+", text or "", flags=re.IGNORECASE):
                score += 4
                if any(k in query for k in ("网页", "链接", "url", "网页内容", "页面内容", "读取")):
                    score += 2
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, record)
        return best[1] if best else None

    def debug_preview(self) -> str:
        rows = ["[Temp Capabilities]"]
        items = self.list_records()
        rows.append(f"count={len(items)}")
        rows.append(f"temp_root={self.temp_root}")
        rows.append(f"kept_root={self.kept_root}")
        rows.append(f"autoload_kept={self.autoload_kept}")
        kept_files = self.list_kept_files()
        rows.append(f"kept_files={len(kept_files)}")
        for item in items:
            rows.append(
                f"- {item.name} action={item.action_name} route={item.route} status={item.status} keywords={item.trigger_keywords}"
            )
        return "\n".join(rows)
