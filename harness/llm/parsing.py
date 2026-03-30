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


class LLMResponseParser:
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
        items = data.get("mutants", [])

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
                        item,
                    )
                )
                continue


            syntax_ok, syntax_reason = validate_syntax_fragment(full_code, language)
            if not syntax_ok:
                rejections.append(
                    RejectedMutant(
                        idx_item,
                        f"non_executable_structural_change: {syntax_reason}",
                        item,
                    )
                )
                continue

            norm_code = self._normalize_code(full_code)

            if norm_code == original_norm:
                rejections.append(RejectedMutant(idx_item, "unchanged_mutant", item))
                continue

            if norm_code in seen_codes:
                rejections.append(RejectedMutant(idx_item, "duplicate_mutant", item))
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
        mutated_lines = list(original_lines)
        indent = actual_line[: len(actual_line) - len(actual_line.lstrip())]
        mutated_lines[idx] = indent + replacement_line.strip()

        return "\n".join(mutated_lines), match_reason

    def _clean_model_line(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"^\s*\d+\s*[:|]\s*", "", text)
        text = re.sub(r"^\s*\d+\s+", "", text)
        return text.strip()


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
            return data
        except (json.JSONDecodeError, ValueError):
            pass

        unfenced = self._strip_code_fences(text)
        try:
            data = json.loads(unfenced)
            if not isinstance(data, dict):
                raise ValueError("Expected top-level JSON object")
            return data
        except (json.JSONDecodeError, ValueError):
            pass

        candidate = self._extract_first_json_object(unfenced)
        if candidate is None:
            candidate = self._extract_first_json_object(text)

        if candidate is None:
            raise ValueError("Failed to locate a JSON object in LLM output")

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse extracted JSON object: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("Expected top-level JSON object")

        return data

    def _strip_code_fences(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _extract_first_json_object(self, text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None

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

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return None

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