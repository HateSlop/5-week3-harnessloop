"""최소 에이전트가 프로젝트의 일반 파일을 읽도록 합니다."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_file(path: str) -> str:
    """프로젝트의 UTF-8 파일을 읽되 .env 같은 숨김 파일은 노출하지 않습니다."""
    file_path = (PROJECT_ROOT / path).resolve()
    if not file_path.is_relative_to(PROJECT_ROOT):
        raise ValueError("저장소 안의 파일만 읽을 수 있습니다.")
    if any(part.startswith(".") for part in file_path.relative_to(PROJECT_ROOT).parts):
        raise ValueError("이 실습 툴에서는 숨김 파일을 읽을 수 없습니다.")
    return file_path.read_text(encoding="utf-8")
