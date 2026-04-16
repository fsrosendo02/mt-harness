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
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    language: str = "java"
    target_id: Optional[str] = None


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
    target_id: Optional[str] = None
    run_name: Optional[str] = None
    mutant_hash: Optional[str] = None
