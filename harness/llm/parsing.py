import json
import re
from dataclasses import dataclass, asdict
from typing import Any

from harness.llm.syntax_sanity import validate_syntax_fragment
from harness.models import Mutant


@dataclass
class RejectedMutant:
    index: int
    reason: str
    payload: dict[str, Any] | Any


@dataclass
class ParseReport:
    requested_count: int
    accepted_count: int
    rejected_count: int
    rejections: list[RejectedMutant]


class MissingMutantsListError(ValueError):
    pass


class LLMResponseParser:
    def _build_rejection_payload(
        self,
        item: dict[str, Any] | Any,
        *,
        candidate_code: str | None = None,
        resolution_reason: str | None = None,
    ) -> dict[str, Any] | Any:
        if not isinstance(item, dict):
            return item

        payload = dict(item)
        if candidate_code is not None:
            payload["candidate_code"] = candidate_code.rstrip() + "\n"
        if resolution_reason is not None:
            payload["resolution_reason"] = resolution_reason
        return payload

    def parse(
        self,
        *,
        raw_text: str,
        requested_count: int,
        original_target_code: str,
        language: str = "java",
    ) -> list[Mutant]:
        mutants, _ = self.parse_with_report(
            raw_text=raw_text,
            requested_count=requested_count,
            original_target_code=original_target_code,
            language=language,
        )
        return mutants

    def parse_with_report(
        self,
        *,
        raw_text: str,
        requested_count: int,
        original_target_code: str,
        language: str = "java",
    ) -> tuple[list[Mutant], ParseReport]:
        data = self._load_json(raw_text)
        items = self._extract_mutant_items(data)

        if not isinstance(items, list):
            raise ValueError("Expected 'mutants' to be a list")

        original_norm = self._normalize_code(original_target_code)
        original_lines = original_target_code.splitlines()

        seen_codes: set[str] = set()
        used_ids: set[str] = set()
        mutants: list[Mutant] = []
        rejections: list[RejectedMutant] = []

        next_fallback_index = 1

        for idx_item, item in enumerate(items, start=1):
            if len(mutants) >= requested_count:
                break

            if not isinstance(item, dict):
                rejections.append(RejectedMutant(idx_item, "item_not_object", item))
                continue

            line_value = item.get("line")
            precode = item.get("precode")
            aftercode = item.get("aftercode")

            if not isinstance(line_value, int):
                rejections.append(RejectedMutant(idx_item, "missing_or_invalid_line", item))
                continue
            if not isinstance(precode, str) or not precode.strip():
                rejections.append(RejectedMutant(idx_item, "missing_or_invalid_precode", item))
                continue
            if not isinstance(aftercode, str) or not aftercode.strip():
                rejections.append(RejectedMutant(idx_item, "missing_or_invalid_aftercode", item))
                continue

            if self._is_non_executable_change(precode, aftercode):
                rejections.append(RejectedMutant(idx_item, "non_executable_change", item))
                continue

            full_code, resolution_reason = self._apply_line_edit(
                original_lines=original_lines,
                line_number=line_value,
                expected_original_line=precode,
                replacement_line=aftercode,
            )

            if full_code is None:
                rejections.append(
                    RejectedMutant(
                        idx_item,
                        resolution_reason or "line_resolution_failed",
                        self._build_rejection_payload(
                            item,
                            resolution_reason=resolution_reason,
                        ),
                    )
                )
                continue

            syntax_ok, syntax_reason = validate_syntax_fragment(full_code, language)
            if not syntax_ok:
                rejections.append(
                    RejectedMutant(
                        idx_item,
                        f"non_executable_structural_change: {syntax_reason}",
                        self._build_rejection_payload(
                            item,
                            candidate_code=full_code,
                            resolution_reason=resolution_reason,
                        ),
                    )
                )
                continue

            norm_code = self._normalize_code(full_code)

            if norm_code == original_norm:
                rejections.append(
                    RejectedMutant(
                        idx_item,
                        "unchanged_mutant",
                        self._build_rejection_payload(
                            item,
                            candidate_code=full_code,
                            resolution_reason=resolution_reason,
                        ),
                    )
                )
                continue

            if norm_code in seen_codes:
                rejections.append(
                    RejectedMutant(
                        idx_item,
                        "duplicate_mutant",
                        self._build_rejection_payload(
                            item,
                            candidate_code=full_code,
                            resolution_reason=resolution_reason,
                        ),
                    )
                )
                continue

            seen_codes.add(norm_code)

            raw_id = item.get("id")
            mutant_id = self._normalize_mutant_id(raw_id, next_fallback_index)

            while mutant_id in used_ids:
                next_fallback_index += 1
                mutant_id = f"m{next_fallback_index:02d}"

            used_ids.add(mutant_id)
            next_fallback_index += 1

            mutants.append(
                Mutant(
                    mutant_id=mutant_id,
                    code=full_code.rstrip() + "\n",
                    source="llm",
                    raw_response=raw_text,
                )
            )

        report = ParseReport(
            requested_count=requested_count,
            accepted_count=len(mutants),
            rejected_count=len(rejections),
            rejections=rejections,
        )
        return mutants, report

    def _apply_line_edit(
        self,
        *,
        original_lines: list[str],
        line_number: int,
        expected_original_line: str,
        replacement_line: str,
    ) -> tuple[str | None, str | None]:
        idx, match_reason = self._resolve_target_line_index(
            original_lines=original_lines,
            line_number=line_number,
            expected_original_line=expected_original_line,
        )
        if idx is None:
            return None, match_reason

        actual_line = original_lines[idx]

        replacement_clean = replacement_line.strip()
        replacement_clean = self._preserve_leading_structural_prefix(
            actual_line=actual_line,
            replacement_line=replacement_clean,
        )

        # Reject dangerous structure-changing replacements
        if self._changes_block_structure(actual_line, replacement_clean):
            return None, "non_executable_structural_change: block_structure_change"

        mutated_lines = list(original_lines)

        indent = actual_line[: len(actual_line) - len(actual_line.lstrip())]
        actual_suffix = self._extract_trailing_structural_suffix(actual_line)

        # If the model omitted a trailing "{" or trailing comment that exists in the
        # actual source line, preserve it safely.
        if actual_suffix:
            repl_no_comment = replacement_clean.split("//", 1)[0].rstrip()
            if not repl_no_comment.endswith("{") and "{" in actual_suffix:
                replacement_clean += " {"

            if "//" in actual_suffix and "//" not in replacement_clean:
                replacement_clean += actual_suffix[actual_suffix.index("//"):]

        mutated_lines[idx] = indent + replacement_clean

        return "\n".join(mutated_lines), match_reason

    def _preserve_leading_structural_prefix(
        self,
        *,
        actual_line: str,
        replacement_line: str,
    ) -> str:
        actual = actual_line.strip()
        replacement = replacement_line.strip()

        if not actual or not replacement or replacement.startswith("}"):
            return replacement

        control_keywords = (
            "else if",
            "if",
            "else",
            "catch",
            "finally",
            "for",
            "while",
            "switch",
            "try",
        )

        for keyword in control_keywords:
            if not replacement.startswith(keyword):
                continue

            idx = actual.find(keyword)
            if idx <= 0:
                continue

            prefix = actual[:idx]
            if self._is_safe_leading_structural_prefix(prefix):
                return prefix + replacement

        return replacement

    def _is_safe_leading_structural_prefix(self, prefix: str) -> bool:
        text = " ".join(prefix.strip().split())
        if not text:
            return False

        safe_prefixes = {
            "}",
            "} else",
            "} else {",
            "} finally",
        }
        if text in safe_prefixes:
            return True

        return bool(re.fullmatch(r"\}+\s*", prefix))

    def _clean_model_line(self, text: str) -> str:
        text = text.strip()
        # Remove accidental line-number prefixes copied from numbered prompts
        text = re.sub(r"^\s*\d+\s*[:|]\s*", "", text)
        text = re.sub(r"^\s*\d+\s+", "", text)
        return text.strip()


    def _resolve_target_line_index(
        self,
        *,
        original_lines: list[str],
        line_number: int,
        expected_original_line: str,
    ) -> tuple[int | None, str | None]:
        expected_norm = self._clean_model_line(expected_original_line)

        # 1) exact local line match
        if 1 <= line_number <= len(original_lines):
            idx = line_number - 1
            if original_lines[idx].strip() == expected_norm:
                return idx, "exact_line_match"

        # 2) exact full-line match anywhere
        exact_matches = [
            i for i, line in enumerate(original_lines)
            if line.strip() == expected_norm
        ]
        if len(exact_matches) == 1:
            return exact_matches[0], "exact_search_match"
        if len(exact_matches) > 1:
            if 1 <= line_number <= len(original_lines):
                return min(exact_matches, key=lambda i: abs(i - (line_number - 1))), "exact_search_nearest_match"
            return None, "ambiguous_precode_match"

        # 3) whitespace-insensitive match
        expected_ws = " ".join(expected_norm.split())
        ws_matches = [
            i for i, line in enumerate(original_lines)
            if " ".join(line.strip().split()) == expected_ws
        ]
        if len(ws_matches) == 1:
            return ws_matches[0], "whitespace_match"
        if len(ws_matches) > 1:
            if 1 <= line_number <= len(original_lines):
                return min(ws_matches, key=lambda i: abs(i - (line_number - 1))), "whitespace_nearest_match"
            return None, "ambiguous_precode_match"

        # 4) executable-content match (ignores comments + whitespace-only differences)
        expected_exec = self._normalize_executable_content(expected_norm)
        exec_matches = [
            i for i, line in enumerate(original_lines)
            if self._normalize_executable_content(line) == expected_exec
        ]
        if len(exec_matches) == 1:
            return exec_matches[0], "comment_insensitive_match"
        if len(exec_matches) > 1:
            if 1 <= line_number <= len(original_lines):
                return min(exec_matches, key=lambda i: abs(i - (line_number - 1))), "comment_insensitive_nearest_match"
            return None, "ambiguous_precode_match"

        # 5) fallback: trust the reported line number if it points inside the extracted method
        if 1 <= line_number <= len(original_lines):
            return line_number - 1, "line_fallback"

        return None, "precode_not_found"
    


    def _is_non_executable_change(self, precode: str, aftercode: str) -> bool:
        pre_exec = self._normalize_executable_content(precode)
        after_exec = self._normalize_executable_content(aftercode)
        return pre_exec == after_exec

    def _normalize_executable_content(self, line: str) -> str:
        text = line
        text = re.sub(r"/\*.*?\*/", "", text)
        text = re.sub(r"//.*$", "", text)
        text = " ".join(text.strip().split())
        return text

    def _load_json(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Expected top-level JSON object")
            return self._select_response_object(data)
        except (json.JSONDecodeError, ValueError):
            pass

        unfenced = self._strip_code_fences(text)
        try:
            data = json.loads(unfenced)
            if not isinstance(data, dict):
                raise ValueError("Expected top-level JSON object")
            return self._select_response_object(data)
        except (json.JSONDecodeError, ValueError):
            pass

        candidates = self._extract_json_objects(unfenced)
        if not candidates:
            candidates = self._extract_json_objects(text)

        if not candidates:
            raise ValueError("Failed to locate a JSON object in LLM output")

        parsed_candidates: list[dict[str, Any]] = []
        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
                continue

            if isinstance(data, dict):
                parsed_candidates.append(data)

        if not parsed_candidates:
            if last_error is not None:
                raise ValueError(f"Failed to parse extracted JSON object: {last_error}") from last_error
            raise ValueError("Expected top-level JSON object")

        return self._select_response_object(*parsed_candidates)

    def _extract_mutant_items(self, data: dict[str, Any]) -> list[Any]:
        if "mutants" in data:
            items = data["mutants"]
            if not isinstance(items, list):
                raise ValueError("Expected 'mutants' to be a list")
            return items

        nested = self._find_mutants_list(data)
        if nested is not None:
            return nested

        raise MissingMutantsListError("Expected LLM response to contain a 'mutants' list")

    def _select_response_object(self, *objects: dict[str, Any]) -> dict[str, Any]:
        for obj in objects:
            if self._find_mutants_list(obj) is not None:
                return obj
        return objects[0]

    def _find_mutants_list(self, value: Any) -> list[Any] | None:
        if isinstance(value, dict):
            mutants = value.get("mutants")
            if isinstance(mutants, list):
                return mutants

            for child in value.values():
                found = self._find_mutants_list(child)
                if found is not None:
                    return found

        if isinstance(value, list):
            for child in value:
                found = self._find_mutants_list(child)
                if found is not None:
                    return found

        return None

    def _strip_code_fences(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _extract_first_json_object(self, text: str) -> str | None:
        objects = self._extract_json_objects(text, max_objects=1)
        return objects[0] if objects else None

    def _extract_json_objects(self, text: str, max_objects: int | None = None) -> list[str]:
        objects: list[str] = []
        start = text.find("{")

        while start != -1:
            depth = 0
            in_string = False
            escape = False

            for i in range(start, len(text)):
                ch = text[i]

                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue

                elif ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[start:i + 1])
                        if max_objects is not None and len(objects) >= max_objects:
                            return objects
                        start = text.find("{", i + 1)
                        break
            else:
                break

        return objects
    
    def _extract_trailing_structural_suffix(self, original_line: str) -> str:
        """
        Preserve a safe trailing suffix from the original line when the LLM
        omits it, e.g.:
        if (x) {          -> preserve " {"
        while (...) { // comment -> preserve " { // comment"

        We only preserve suffixes that start at the first unmatched structural
        token after the logical code content, typically '{' and/or trailing comment.
        """
        stripped = original_line.rstrip("\n")
        if not stripped.strip():
            return ""

        # Preserve trailing inline comment if present
        comment = ""
        if "//" in stripped:
            code_part, comment_part = stripped.split("//", 1)
            stripped = code_part.rstrip()
            comment = " //" + comment_part.rstrip()

        # Preserve trailing opening brace if present
        brace = ""
        if stripped.rstrip().endswith("{"):
            brace = " {"

        return f"{brace}{comment}"
    
    def _changes_block_structure(self, actual_line: str, replacement_line: str) -> bool:
        """
        Reject obviously dangerous single-line edits that alter block structure.
        """
        actual = actual_line.strip()
        repl = replacement_line.strip()

        actual_has_open = "{" in actual
        repl_has_open = "{" in repl
        actual_has_close = "}" in actual
        repl_has_close = "}" in repl

        # If replacement introduces a new brace pattern not present in the original line,
        # it is risky for single-line mutation and should be rejected.
        if repl_has_open and not actual_has_open:
            return True
        if repl_has_close and not actual_has_close:
            return True

        return False

    def _normalize_code(self, code: str) -> str:
        return code.strip().replace("\r\n", "\n").replace("\r", "\n")

    def _normalize_mutant_id(self, value: Any, fallback_index: int) -> str:
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text.startswith("m"):
                return text
            if text.isdigit():
                return f"m{int(text):02d}"
            return text
        if isinstance(value, int):
            return f"m{value:02d}"
        return f"m{fallback_index:02d}"


def parse_report_to_dict(report: ParseReport) -> dict[str, Any]:
    return {
        "requested_count": report.requested_count,
        "accepted_count": report.accepted_count,
        "rejected_count": report.rejected_count,
        "rejections": [asdict(r) for r in report.rejections],
    }

    
