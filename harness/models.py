from dataclasses import dataclass
from typing import Optional


@dataclass
class Subject:
    dataset: str
    subject_id: str
    language: str
    version: str = "f"

@dataclass
class Target:
    file_path: str
    function_name: str
    start_line: int
    end_line: int


@dataclass
class Mutant:
    mutant_id: str
    code: str
    source: str
    raw_response: Optional[str] = None


@dataclass
class MutantResult:
    dataset: str
    subject_id: str
    function_name: str
    mutant_id: str
    build_status: str
    test_status: str
    killed: bool
    executable: bool
    log_path: str
