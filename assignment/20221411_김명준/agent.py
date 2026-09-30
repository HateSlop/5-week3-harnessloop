from __future__ import annotations

"""Adaptive Workout Planner Agent.

Replace the assignment's ``agent.py`` with this file.  The core parser can be
tested without an API key:

    python agent.py --self-test

The actual agent reads ``workout_log.json`` from the same directory as this
file and then asks for today's condition interactively.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
import argparse
import json
import math
import os
import re

try:
    from dotenv import load_dotenv

    DOTENV_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as error:
    # Keep the parser/self-test usable in a bare Python environment.  The
    # interactive agent still gives an actionable dependency error below.
    DOTENV_IMPORT_ERROR = error

    def load_dotenv(*_: Any, **__: Any) -> bool:
        return False

try:
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_openai import ChatOpenAI

    LANGCHAIN_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as error:
    # ``--self-test`` deliberately works before LangChain is installed.
    ChatOpenAI = None  # type: ignore[assignment,misc]
    create_agent = None  # type: ignore[assignment,misc]
    LANGCHAIN_IMPORT_ERROR = error

    def tool(function: Callable[..., Any]) -> Callable[..., Any]:
        return function


# ---------------------------------------------------------------------------
# Environment and limits
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
WORKOUT_LOG_FILE = "workout_log.json"

MAX_TOOL_CALLS_PER_ATTEMPT = 6
MAX_TOTAL_TOOL_CALLS = 15
MAX_ATTEMPTS = 3
AGENT_RECURSION_LIMIT = 16


def find_project_root(start: Path) -> Path:
    """Find the repository root without assuming a fixed folder depth."""

    for candidate in (start, *start.parents):
        if (candidate / ".env").exists() or (candidate / "requirements.txt").exists():
            return candidate
    return start


load_dotenv(find_project_root(BASE_DIR) / ".env")


# ---------------------------------------------------------------------------
# Explicit exercise recognition
# ---------------------------------------------------------------------------

LOAD_TOTAL = "listed_total_load"
LOAD_PER_HAND = "per_hand"
LOAD_PER_SIDE = "per_side"
LOAD_BODYWEIGHT_UNKNOWN = "bodyweight_or_assistance_unspecified"


def normalize_exercise_name(name: str) -> str:
    """Normalise Korean/English exercise aliases for deterministic matching."""

    return re.sub(r"[\s_\-()/·.]", "", name).lower()


@dataclass(frozen=True)
class ExerciseDefinition:
    aliases: tuple[str, ...]
    canonical_name: str
    movement_pattern: str
    primary_muscles: tuple[str, ...]
    equipment: str
    load_interpretation: str
    default_sides_trained: int
    note: str


@dataclass(frozen=True)
class ExerciseRecognition:
    canonical_name: str
    movement_pattern: str
    primary_muscles: tuple[str, ...]
    equipment: str
    load_interpretation: str
    default_sides_trained: int
    note: str
    confidence: str
    source: str

    @property
    def uses_per_side_weight(self) -> bool:
        return self.load_interpretation in {LOAD_PER_HAND, LOAD_PER_SIDE}

    @property
    def supports_external_volume(self) -> bool:
        return self.load_interpretation != LOAD_BODYWEIGHT_UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "movement_pattern": self.movement_pattern,
            "primary_muscles": list(self.primary_muscles),
            "equipment": self.equipment,
            "load_interpretation": self.load_interpretation,
            "default_sides_trained": self.default_sides_trained,
            "recorded_weight_meaning": self.note,
            "recognition_confidence": self.confidence,
            "recognition_source": self.source,
        }


EXERCISE_CATALOG: tuple[ExerciseDefinition, ...] = (
    ExerciseDefinition(
        aliases=("스미스 머신 벤치 프레스", "스미스 벤치 프레스", "smith machine bench press"),
        canonical_name="스미스 머신 벤치 프레스",
        movement_pattern="수평 푸시",
        primary_muscles=("대흉근", "삼두근", "전면 삼각근"),
        equipment="스미스 머신",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 바에 설정한 전체 중량으로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("덤벨 체스트 프레스", "덤벨 프레스", "dumbbell chest press"),
        canonical_name="덤벨 체스트 프레스",
        movement_pattern="수평 푸시",
        primary_muscles=("대흉근", "삼두근", "전면 삼각근"),
        equipment="덤벨",
        load_interpretation=LOAD_PER_HAND,
        default_sides_trained=2,
        note="weight_kg는 덤벨 한 개, 즉 한 손당 중량입니다. 양손 수행은 총 외부부하 계산에 2배를 반영합니다.",
    ),
    ExerciseDefinition(
        aliases=("인클라인 체스트 프레스", "incline chest press"),
        canonical_name="인클라인 체스트 프레스",
        movement_pattern="상부 가슴 푸시",
        primary_muscles=("상부 대흉근", "삼두근", "전면 삼각근"),
        equipment="운동명만으로 바벨/머신을 확정할 수 없음",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="덤벨 또는 원암이라는 표기가 없으므로 weight_kg는 기록된 하나의 전체 부하로만 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("인클라인 머신 체스트 프레스", "인클라인 머신 프레스", "incline machine chest press"),
        canonical_name="인클라인 머신 체스트 프레스",
        movement_pattern="상부 가슴 푸시",
        primary_muscles=("상부 대흉근", "삼두근", "전면 삼각근"),
        equipment="머신",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 머신에 표시된 전체 설정중량입니다. 머신 간 수치는 직접 비교하지 않습니다.",
    ),
    ExerciseDefinition(
        aliases=("풀업", "pull up", "pullup", "친업", "chin up"),
        canonical_name="풀업/친업",
        movement_pattern="수직 풀",
        primary_muscles=("광배근", "상완이두근", "대원근"),
        equipment="체중 또는 보조 풀업 머신",
        load_interpretation=LOAD_BODYWEIGHT_UNKNOWN,
        default_sides_trained=1,
        note="weight_kg만으로는 체중, 추가중량, 보조중량 중 무엇인지 알 수 없습니다. 명시 전에는 외부 훈련량에 합산하지 않습니다.",
    ),
    ExerciseDefinition(
        aliases=("티바로우", "티바 로우", "t bar row", "tbar row"),
        canonical_name="티바로우",
        movement_pattern="수평 풀",
        primary_muscles=("광배근", "중부 승모근", "능형근", "상완이두근"),
        equipment="티바로우 머신/바",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 장비에 기록한 하나의 전체 부하로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("렛풀다운", "랫풀다운", "lat pulldown"),
        canonical_name="렛풀다운",
        movement_pattern="수직 풀",
        primary_muscles=("광배근", "상완이두근", "대원근"),
        equipment="케이블 머신",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 케이블 스택의 표시중량입니다. 도르래비 때문에 자유중량과 직접 비교하지 않습니다.",
    ),
    ExerciseDefinition(
        aliases=("원암 하이로우", "원암 하이 로우", "one arm high row", "onearm high row"),
        canonical_name="원암 하이로우",
        movement_pattern="단측 수평/사선 풀",
        primary_muscles=("광배근", "대원근", "후면 삼각근", "상완이두근"),
        equipment="단측 머신 또는 케이블",
        load_interpretation=LOAD_PER_SIDE,
        default_sides_trained=2,
        note="weight_kg는 한쪽 팔당 중량입니다. 기본값은 좌우 모두 수행한 것으로 보고 총 외부부하에 2배를 반영합니다.",
    ),
    ExerciseDefinition(
        aliases=("파나타 머신 하이로우", "파나타 머신 하이 로우", "panatta machine high row"),
        canonical_name="파나타 머신 하이로우",
        movement_pattern="수평/사선 풀",
        primary_muscles=("광배근", "중부 승모근", "후면 삼각근", "상완이두근"),
        equipment="파나타 양측 머신",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 양팔 머신의 표시중량으로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("오버헤드 프레스", "ohp", "overhead press"),
        canonical_name="오버헤드 프레스",
        movement_pattern="수직 푸시",
        primary_muscles=("전면/측면 삼각근", "삼두근"),
        equipment="바벨 또는 지정 장비",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="덤벨/원암 표기가 없으므로 weight_kg는 기록한 하나의 전체 부하로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("머신 숄더 프레스", "machine shoulder press"),
        canonical_name="머신 숄더 프레스",
        movement_pattern="수직 푸시",
        primary_muscles=("전면/측면 삼각근", "삼두근"),
        equipment="머신",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 양팔 머신의 표시중량입니다.",
    ),
    ExerciseDefinition(
        aliases=("스탠딩 숄더 프레스", "standing shoulder press"),
        canonical_name="스탠딩 숄더 프레스",
        movement_pattern="수직 푸시",
        primary_muscles=("전면/측면 삼각근", "삼두근"),
        equipment="기록명만으로 장비 미상",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="덤벨/원암 표기가 없으므로 weight_kg는 기록한 하나의 전체 부하로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("덤벨 사이드레터럴 레이즈", "덤벨 사이드 레터럴 레이즈", "dumbbell lateral raise"),
        canonical_name="덤벨 사이드레터럴 레이즈",
        movement_pattern="어깨 외전",
        primary_muscles=("측면 삼각근",),
        equipment="덤벨",
        load_interpretation=LOAD_PER_HAND,
        default_sides_trained=2,
        note="weight_kg는 덤벨 한 개, 즉 한 손당 중량입니다. 양손 수행은 총 외부부하 계산에 2배를 반영합니다.",
    ),
    ExerciseDefinition(
        aliases=("원암 머신 사이드레터럴 레이즈", "원암 머신 사이드 레터럴 레이즈", "one arm machine lateral raise"),
        canonical_name="원암 머신 사이드레터럴 레이즈",
        movement_pattern="단측 어깨 외전",
        primary_muscles=("측면 삼각근",),
        equipment="단측 머신",
        load_interpretation=LOAD_PER_SIDE,
        default_sides_trained=2,
        note="weight_kg는 한쪽 팔당 머신 설정중량입니다. 기본값은 좌우 모두 수행한 것으로 해석합니다.",
    ),
    ExerciseDefinition(
        aliases=("페이스 풀", "face pull"),
        canonical_name="페이스 풀",
        movement_pattern="후면 어깨/견갑 안정",
        primary_muscles=("후면 삼각근", "중하부 승모근", "회전근개"),
        equipment="케이블",
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note="weight_kg는 케이블 스택의 표시중량입니다.",
    ),
    ExerciseDefinition(
        # This intentionally covers the user's no-'덤벨' shorthand too.
        aliases=(
            "덤벨 벤트오버 레터럴 레이즈",
            "덤벨 벤트오버 레터럴레이즈",
            "벤트오버 레터럴 레이즈",
            "벤트오버 레터럴레이즈",
            "bent over lateral raise",
        ),
        canonical_name="덤벨 벤트오버 레터럴 레이즈",
        movement_pattern="후면 어깨 외전",
        primary_muscles=("후면 삼각근", "중부 승모근", "능형근"),
        equipment="덤벨",
        load_interpretation=LOAD_PER_HAND,
        default_sides_trained=2,
        note="운동명에 '덤벨'이 생략되어도 벤트오버 레터럴 레이즈는 이 카탈로그에서 덤벨 한 개(한 손)당 중량으로 인식합니다.",
    ),
)


CATALOG_BY_ALIAS: dict[str, ExerciseDefinition] = {
    normalize_exercise_name(alias): definition
    for definition in EXERCISE_CATALOG
    for alias in definition.aliases
}


def recognition_from_definition(definition: ExerciseDefinition, source: str = "catalog") -> ExerciseRecognition:
    return ExerciseRecognition(
        canonical_name=definition.canonical_name,
        movement_pattern=definition.movement_pattern,
        primary_muscles=definition.primary_muscles,
        equipment=definition.equipment,
        load_interpretation=definition.load_interpretation,
        default_sides_trained=definition.default_sides_trained,
        note=definition.note,
        confidence="high",
        source=source,
    )


def recognize_exercise(exercise_name: str) -> ExerciseRecognition:
    """Recognise a movement without ever guessing that a generic lift is per-side.

    Priority matters: a known alias such as '벤트오버 레터럴 레이즈' is
    recognised before generic name rules; then one-arm terms precede dumbbell
    terms so a one-arm dumbbell row remains *per side*, not merely *per hand*.
    """

    normalized = normalize_exercise_name(exercise_name)
    if not normalized:
        raise ValueError("운동명이 비어 있습니다.")

    definition = CATALOG_BY_ALIAS.get(normalized)
    if definition is not None:
        return recognition_from_definition(definition)

    one_arm_markers = ("원암", "한팔", "onearm", "unilateral", "singlearm")
    dumbbell_markers = ("덤벨", "dumbbell", "db")
    pullup_markers = ("풀업", "pullup", "chinup", "친업")

    if any(marker in normalized for marker in one_arm_markers):
        return ExerciseRecognition(
            canonical_name=exercise_name,
            movement_pattern="단측 운동",
            primary_muscles=("운동명만으로 세부 근육 미상",),
            equipment="단측 장비 또는 덤벨",
            load_interpretation=LOAD_PER_SIDE,
            default_sides_trained=2,
            note="'원암/한팔' 표기 때문에 weight_kg를 한쪽 팔당 중량으로 해석합니다. 기본값은 좌우 모두 수행입니다.",
            confidence="medium",
            source="one-arm rule",
        )

    if any(marker in normalized for marker in dumbbell_markers):
        return ExerciseRecognition(
            canonical_name=exercise_name,
            movement_pattern="운동명만으로 세부 패턴 미상",
            primary_muscles=("운동명만으로 세부 근육 미상",),
            equipment="덤벨",
            load_interpretation=LOAD_PER_HAND,
            default_sides_trained=2,
            note="'덤벨' 표기 때문에 weight_kg를 덤벨 한 개(한 손)당 중량으로 해석합니다. 양손 수행은 총 외부부하 계산에 2배를 반영합니다.",
            confidence="medium",
            source="dumbbell rule",
        )

    if any(marker in normalized for marker in pullup_markers):
        return ExerciseRecognition(
            canonical_name=exercise_name,
            movement_pattern="수직 풀",
            primary_muscles=("광배근", "상완이두근"),
            equipment="체중 또는 보조 풀업 머신",
            load_interpretation=LOAD_BODYWEIGHT_UNKNOWN,
            default_sides_trained=1,
            note="풀업의 weight_kg는 체중/추가중량/보조중량 중 무엇인지 명시되지 않아 외부 훈련량에서 제외합니다.",
            confidence="medium",
            source="pull-up rule",
        )

    if any(marker in normalized for marker in ("머신", "케이블", "랫풀", "렛풀", "레그프레스")):
        equipment = "머신 또는 케이블"
        note = "weight_kg는 장비의 표시중량으로 해석합니다. 다른 장비의 kg와 직접 비교하지 않습니다."
    else:
        equipment = "운동명만으로 장비 미상"
        note = "덤벨/원암 표기가 없으므로 weight_kg는 기록한 하나의 전체 부하로만 해석합니다."

    return ExerciseRecognition(
        canonical_name=exercise_name,
        movement_pattern="운동명만으로 세부 패턴 미상",
        primary_muscles=("운동명만으로 세부 근육 미상",),
        equipment=equipment,
        load_interpretation=LOAD_TOTAL,
        default_sides_trained=1,
        note=note,
        confidence="low",
        source="conservative fallback",
    )


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExerciseBlock:
    weight_kg: float
    reps_per_set: int
    total_sets: int
    rpe: float
    sides_trained: int
    pullup_load_mode: str = "unspecified"


@dataclass(frozen=True)
class ExerciseRecord:
    exercise: str
    recognition: ExerciseRecognition
    blocks: tuple[ExerciseBlock, ...]


@dataclass(frozen=True)
class DailyWorkout:
    day_ago: int
    exercises: tuple[ExerciseRecord, ...]


@dataclass(frozen=True)
class UserProfile:
    goal: str
    experience_level: str
    available_minutes: int
    subjective_fatigue: float
    sleep_hours: Optional[float] = None
    soreness: Optional[float] = None


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

def _as_sequence(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, tuple):
        raw_values = list(value)
    elif isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_values = [value]

    if not raw_values:
        raise ValueError(f"{field_name} 값이 비어 있습니다.")
    return raw_values


def parse_float_sequence(value: Any, field_name: str) -> list[float]:
    parsed: list[float] = []
    for item in _as_sequence(value, field_name):
        if isinstance(item, bool):
            raise ValueError(f"{field_name}에 true/false는 사용할 수 없습니다.")
        try:
            number = float(item)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field_name}에 잘못된 값이 있습니다: {item}") from error
        if not math.isfinite(number):
            raise ValueError(f"{field_name}에는 유한한 숫자만 사용할 수 있습니다: {item}")
        parsed.append(number)
    return parsed


def parse_int_sequence(value: Any, field_name: str) -> list[int]:
    parsed: list[int] = []
    for item in _as_sequence(value, field_name):
        if isinstance(item, bool):
            raise ValueError(f"{field_name}에 true/false는 사용할 수 없습니다.")
        try:
            number = float(item)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field_name}에 잘못된 값이 있습니다: {item}") from error
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError(f"{field_name}에는 정수만 사용할 수 있습니다: {item}")
        parsed.append(int(number))
    return parsed


def parse_text_sequence(value: Any, field_name: str) -> list[str]:
    values = [str(item).strip() for item in _as_sequence(value, field_name)]
    if any(not item for item in values):
        raise ValueError(f"{field_name}에 빈 값이 있습니다.")
    return values


def broadcast_sequence(values: list[Any], target_length: int, field_name: str) -> list[Any]:
    if len(values) == target_length:
        return values
    if len(values) == 1:
        return values * target_length
    raise ValueError(f"{field_name}의 항목 개수가 다른 운동 정보와 일치하지 않습니다.")


PULLUP_MODE_ALIASES = {
    "unspecified": "unspecified",
    "미상": "unspecified",
    "체중": "bodyweight",
    "bodyweight": "bodyweight",
    "추가중량": "additional",
    "additional": "additional",
    "weighted": "additional",
    "보조": "assistance",
    "보조중량": "assistance",
    "assistance": "assistance",
    "assisted": "assistance",
}


def normalise_pullup_mode(value: str) -> str:
    normalized = normalize_exercise_name(value)
    mode = PULLUP_MODE_ALIASES.get(normalized)
    if mode is None:
        allowed = "체중/bodyweight, 추가중량/additional, 보조중량/assistance"
        raise ValueError(f"pullup_load_mode는 {allowed} 중 하나여야 합니다.")
    return mode


def build_exercise_blocks(exercise_data: dict[str, Any], recognition: ExerciseRecognition) -> tuple[ExerciseBlock, ...]:
    required_fields = ("weight_kg", "reps_per_set", "total_sets", "rpe")
    missing = [field for field in required_fields if field not in exercise_data]
    if missing:
        raise ValueError(f"필수 항목이 없습니다: {', '.join(missing)}")

    weights = parse_float_sequence(exercise_data["weight_kg"], "weight_kg")
    reps = parse_int_sequence(exercise_data["reps_per_set"], "reps_per_set")
    sets = parse_int_sequence(exercise_data["total_sets"], "total_sets")
    rpes = parse_float_sequence(exercise_data["rpe"], "rpe")

    lengths = [len(weights), len(reps), len(sets), len(rpes)]
    if "sides_trained" in exercise_data:
        sides = parse_int_sequence(exercise_data["sides_trained"], "sides_trained")
        lengths.append(len(sides))
    else:
        sides = [recognition.default_sides_trained]

    if "pullup_load_mode" in exercise_data:
        pullup_modes = [normalise_pullup_mode(item) for item in parse_text_sequence(exercise_data["pullup_load_mode"], "pullup_load_mode")]
        lengths.append(len(pullup_modes))
    else:
        pullup_modes = ["unspecified"]

    target_length = max(lengths)
    weights = broadcast_sequence(weights, target_length, "weight_kg")
    reps = broadcast_sequence(reps, target_length, "reps_per_set")
    sets = broadcast_sequence(sets, target_length, "total_sets")
    rpes = broadcast_sequence(rpes, target_length, "rpe")
    sides = broadcast_sequence(sides, target_length, "sides_trained")
    pullup_modes = broadcast_sequence(pullup_modes, target_length, "pullup_load_mode")

    blocks: list[ExerciseBlock] = []
    for index in range(target_length):
        weight = weights[index]
        rep = reps[index]
        total_set = sets[index]
        rpe = rpes[index]
        side_count = sides[index]
        pullup_mode = pullup_modes[index]

        if weight < 0:
            raise ValueError("weight_kg는 0 이상이어야 합니다.")
        if rep <= 0:
            raise ValueError("reps_per_set은 1 이상이어야 합니다.")
        if total_set <= 0:
            raise ValueError("total_sets는 1 이상이어야 합니다.")
        if not 1 <= rpe <= 10:
            raise ValueError("RPE는 1~10 사이여야 합니다.")

        if recognition.uses_per_side_weight:
            if side_count not in (1, 2):
                raise ValueError("덤벨/원암 운동의 sides_trained는 1 또는 2여야 합니다.")
        elif side_count != 1:
            raise ValueError("양측/전체 중량 운동의 sides_trained는 1이어야 합니다.")

        if recognition.load_interpretation != LOAD_BODYWEIGHT_UNKNOWN and pullup_mode != "unspecified":
            raise ValueError("pullup_load_mode는 풀업/친업 기록에서만 사용할 수 있습니다.")

        blocks.append(
            ExerciseBlock(
                weight_kg=weight,
                reps_per_set=rep,
                total_sets=total_set,
                rpe=rpe,
                sides_trained=side_count,
                pullup_load_mode=pullup_mode,
            )
        )
    return tuple(blocks)


def parse_workout_data(raw_data: Any, source_name: str = WORKOUT_LOG_FILE) -> list[DailyWorkout]:
    if not isinstance(raw_data, list):
        raise ValueError(f"{source_name}의 최상위 구조는 배열이어야 합니다.")

    daily_workouts: list[DailyWorkout] = []
    used_days: set[int] = set()

    for day_index, day_data in enumerate(raw_data, start=1):
        try:
            if not isinstance(day_data, dict):
                raise ValueError("날짜 기록은 객체여야 합니다.")
            parsed_day = parse_int_sequence(day_data["day_ago"], "day_ago")
            if len(parsed_day) != 1:
                raise ValueError("day_ago는 하나의 정수여야 합니다.")
            day_ago = parsed_day[0]
            if day_ago not in (1, 2, 3):
                raise ValueError("day_ago는 1, 2, 3 중 하나여야 합니다.")
            if day_ago in used_days:
                raise ValueError(f"day_ago={day_ago}가 중복되어 있습니다.")
            used_days.add(day_ago)

            raw_exercises = day_data["exercises"]
            if not isinstance(raw_exercises, list):
                raise ValueError("exercises는 배열이어야 합니다.")
            if not raw_exercises:
                raise ValueError("exercises가 비어 있습니다.")

            records: list[ExerciseRecord] = []
            for exercise_data in raw_exercises:
                if not isinstance(exercise_data, dict):
                    raise ValueError("각 운동 기록은 객체여야 합니다.")
                exercise_name = str(exercise_data["exercise"]).strip()
                recognition = recognize_exercise(exercise_name)
                records.append(
                    ExerciseRecord(
                        exercise=exercise_name,
                        recognition=recognition,
                        blocks=build_exercise_blocks(exercise_data, recognition),
                    )
                )

            daily_workouts.append(DailyWorkout(day_ago=day_ago, exercises=tuple(records)))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{source_name}의 {day_index}번째 날짜 기록에 문제가 있습니다: {error}") from error

    return sorted(daily_workouts, key=lambda workout: workout.day_ago)


def get_workout_log_path() -> Path:
    return BASE_DIR / WORKOUT_LOG_FILE


def read_workout_log() -> list[DailyWorkout]:
    path = get_workout_log_path()
    if not path.exists():
        raise FileNotFoundError(f"운동 기록 파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as file:
        return parse_workout_data(json.load(file), path.name)


def validate_workout_semantics(workouts: list[DailyWorkout]) -> list[str]:
    """Validate the non-negotiable per-side rules before the LLM runs."""

    errors: list[str] = []
    for workout in workouts:
        for record in workout.exercises:
            recognition = record.recognition
            if recognition.load_interpretation == LOAD_PER_HAND:
                if any(block.sides_trained not in (1, 2) for block in record.blocks):
                    errors.append(f"{record.exercise}: 덤벨 운동의 sides_trained 값이 잘못되었습니다.")
            if recognition.load_interpretation == LOAD_PER_SIDE:
                if any(block.sides_trained not in (1, 2) for block in record.blocks):
                    errors.append(f"{record.exercise}: 원암 운동의 sides_trained 값이 잘못되었습니다.")
    return errors


# ---------------------------------------------------------------------------
# Deterministic analysis (the LLM receives this, not raw guesses)
# ---------------------------------------------------------------------------

def _weight_label(recognition: ExerciseRecognition, block: ExerciseBlock) -> str:
    weight = f"{block.weight_kg:g}kg"
    if recognition.load_interpretation == LOAD_PER_HAND:
        return f"{weight}/한 손(덤벨 한 개당)"
    if recognition.load_interpretation == LOAD_PER_SIDE:
        return f"{weight}/한쪽 팔"
    if recognition.load_interpretation == LOAD_BODYWEIGHT_UNKNOWN:
        if block.pullup_load_mode == "additional":
            return f"{weight} 추가중량"
        if block.pullup_load_mode == "assistance":
            return f"{weight} 보조중량"
        if block.pullup_load_mode == "bodyweight":
            return f"{weight} 체중 기록"
        return f"{weight}(체중/추가/보조 의미 미상)"
    return f"{weight}(기록된 전체 부하)"


def calculate_block_analysis(record: ExerciseRecord, block: ExerciseBlock) -> dict[str, Any]:
    """Calculate only a clearly defined external-load estimate.

    ``total_sets`` stays the conventional logged set count.  For dumbbell and
    one-arm work, the separate total-external-volume field includes both sides,
    while the displayed recorded weight remains per hand/per side.
    """

    recognition = record.recognition
    logged_total_reps = block.reps_per_set * block.total_sets
    side_multiplier = block.sides_trained if recognition.uses_per_side_weight else 1

    result: dict[str, Any] = {
        "listed_weight_kg": block.weight_kg,
        "weight_display": _weight_label(recognition, block),
        "reps_per_set": block.reps_per_set,
        "total_sets": block.total_sets,
        "logged_total_reps": logged_total_reps,
        "rpe": block.rpe,
        "sides_trained": block.sides_trained if recognition.uses_per_side_weight else None,
        "load_interpretation": recognition.load_interpretation,
        "estimated_effective_load_kg": None,
        "estimated_external_volume_kg": None,
        "volume_formula": None,
        "volume_caveat": None,
    }

    if recognition.load_interpretation == LOAD_BODYWEIGHT_UNKNOWN:
        result["volume_caveat"] = (
            "풀업의 기록중량이 체중·추가중량·보조중량 중 무엇인지 확정되지 않아 "
            "최근 총 외부 훈련량에서는 제외했습니다."
        )
        return result

    effective_load = block.weight_kg * side_multiplier
    volume = effective_load * logged_total_reps
    result["estimated_effective_load_kg"] = round(effective_load, 2)
    result["estimated_external_volume_kg"] = round(volume, 2)

    if recognition.load_interpretation == LOAD_PER_HAND:
        result["volume_formula"] = (
            f"{block.weight_kg:g}kg/한 손 × {block.sides_trained}손 × "
            f"{block.reps_per_set}회 × {block.total_sets}세트 = {volume:g}kg"
        )
    elif recognition.load_interpretation == LOAD_PER_SIDE:
        result["volume_formula"] = (
            f"{block.weight_kg:g}kg/한쪽 × {block.sides_trained}쪽 × "
            f"{block.reps_per_set}회 × {block.total_sets}세트 = {volume:g}kg"
        )
    else:
        result["volume_formula"] = (
            f"{block.weight_kg:g}kg × {block.reps_per_set}회 × "
            f"{block.total_sets}세트 = {volume:g}kg"
        )

    if "머신" in recognition.equipment or "케이블" in recognition.equipment:
        result["volume_caveat"] = "머신/케이블의 표시중량은 장비별 도르래비·레버비가 달라 자유중량과 직접 비교하지 않습니다."
    return result


def analyze_exercise(record: ExerciseRecord) -> dict[str, Any]:
    block_details = [calculate_block_analysis(record, block) for block in record.blocks]
    total_sets = sum(block.total_sets for block in record.blocks)
    logged_total_reps = sum(block.reps_per_set * block.total_sets for block in record.blocks)
    weighted_rpe = sum(block.rpe * block.total_sets for block in record.blocks)
    quantified_volume = sum(
        detail["estimated_external_volume_kg"] or 0.0 for detail in block_details
    )
    has_unquantified_block = any(detail["estimated_external_volume_kg"] is None for detail in block_details)

    return {
        "exercise": record.exercise,
        "recognition": record.recognition.as_dict(),
        "total_sets": total_sets,
        "logged_total_reps": logged_total_reps,
        "set_weighted_average_rpe": round(weighted_rpe / total_sets, 2),
        "max_rpe": max(block.rpe for block in record.blocks),
        "estimated_external_volume_kg": round(quantified_volume, 2) if not has_unquantified_block else None,
        "volume_status": "partial_or_not_quantified" if has_unquantified_block else "quantified",
        "blocks": block_details,
    }


def analyze_daily_workout(workout: DailyWorkout) -> dict[str, Any]:
    exercise_details = [analyze_exercise(record) for record in workout.exercises]
    total_sets = sum(detail["total_sets"] for detail in exercise_details)
    logged_total_reps = sum(detail["logged_total_reps"] for detail in exercise_details)
    weighted_rpe_sum = sum(
        block.rpe * block.total_sets
        for record in workout.exercises
        for block in record.blocks
    )
    quantified_volume = sum(
        detail["estimated_external_volume_kg"] or 0.0 for detail in exercise_details
    )
    excluded_exercises = [
        detail["exercise"]
        for detail in exercise_details
        if detail["estimated_external_volume_kg"] is None
    ]
    high_effort_blocks = [
        {
            "exercise": record.exercise,
            "weight_display": detail["weight_display"],
            "reps_per_set": detail["reps_per_set"],
            "total_sets": detail["total_sets"],
            "rpe": detail["rpe"],
        }
        for record in workout.exercises
        for detail in (calculate_block_analysis(record, block) for block in record.blocks)
        if detail["rpe"] >= 9
    ]

    return {
        "day_ago": workout.day_ago,
        "total_sets": total_sets,
        "logged_total_reps": logged_total_reps,
        "set_weighted_average_rpe": round(weighted_rpe_sum / total_sets, 2),
        "estimated_external_volume_kg": round(quantified_volume, 2),
        "excluded_from_volume_due_to_ambiguous_load": excluded_exercises,
        "high_effort_blocks": high_effort_blocks,
        "exercises": exercise_details,
    }


def summarize_recent_workouts(workouts: list[DailyWorkout]) -> dict[str, Any]:
    daily_details = [analyze_daily_workout(workout) for workout in workouts]
    total_sets = sum(detail["total_sets"] for detail in daily_details)
    logged_total_reps = sum(detail["logged_total_reps"] for detail in daily_details)
    weighted_rpe_sum = sum(
        block.rpe * block.total_sets
        for workout in workouts
        for record in workout.exercises
        for block in record.blocks
    )
    total_quantified_volume = sum(detail["estimated_external_volume_kg"] for detail in daily_details)
    excluded = [
        {"day_ago": detail["day_ago"], "exercises": detail["excluded_from_volume_due_to_ambiguous_load"]}
        for detail in daily_details
        if detail["excluded_from_volume_due_to_ambiguous_load"]
    ]
    return {
        "analysis_period": "최근 3일",
        "training_days": len(workouts),
        "total_sets": total_sets,
        "logged_total_reps": logged_total_reps,
        "set_weighted_average_rpe": round(weighted_rpe_sum / total_sets, 2),
        "estimated_external_volume_kg": round(total_quantified_volume, 2),
        "volume_definition": "덤벨/원암은 한 손·한쪽당 중량에 실제 수행한 쪽 수를 곱한 추정 외부부하입니다. 머신 kg와 자유중량 kg를 직접 비교하지 않습니다.",
        "excluded_from_volume_due_to_ambiguous_load": excluded,
        "daily_details": daily_details,
    }


def search_exercise_history(workouts: list[DailyWorkout], exercise_keyword: str) -> list[dict[str, Any]]:
    keyword = normalize_exercise_name(exercise_keyword)
    if not keyword:
        raise ValueError("검색할 운동명을 입력해주세요.")

    matches: list[dict[str, Any]] = []
    for workout in workouts:
        for record in workout.exercises:
            searchable_name = normalize_exercise_name(record.exercise)
            canonical_name = normalize_exercise_name(record.recognition.canonical_name)
            if keyword in searchable_name or keyword in canonical_name:
                detail = analyze_exercise(record)
                detail["day_ago"] = workout.day_ago
                matches.append(detail)
    return matches


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

@tool
def get_recent_workout_summary() -> dict[str, Any]:
    """최근 1~3일의 운동을 명시적으로 인식해 분석한다.

    각 운동의 recognition과 blocks를 반드시 확인한다. 덤벨/원암의
    weight_kg는 한 손/한쪽당이며, estimated_external_volume_kg에는
    양측 수행이 반영되어 있다. 풀업의 모호한 weight_kg는 합산하지 않는다.
    """

    return summarize_recent_workouts(read_workout_log())


@tool
def get_day_workout_detail(day_ago: int) -> dict[str, Any]:
    """특정 날짜(1, 2, 3일 전)의 운동 인식·중량 의미·세트·RPE를 반환한다."""

    if day_ago not in (1, 2, 3):
        return {"error": "day_ago는 1, 2, 3만 가능합니다."}
    for workout in read_workout_log():
        if workout.day_ago == day_ago:
            return analyze_daily_workout(workout)
    return {"day_ago": day_ago, "message": "해당 날짜의 운동 기록이 없습니다."}


@tool
def get_exercise_history(exercise_keyword: str) -> list[dict[str, Any]]:
    """최근 3일에서 운동명을 검색해 실제 수행 블록과 명시적 운동 인식을 반환한다."""

    return search_exercise_history(read_workout_log(), exercise_keyword)


# ---------------------------------------------------------------------------
# Agent and prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
당신은 근거 중심 Adaptive Workout Planner Agent입니다. 최종 답변은 반드시 한국어입니다.

운동 기록은 Tool로만 확인합니다. 답변 전에 최소 한 번은 Tool을 호출하고, 최근 훈련을
언급할 때는 Tool의 recognition과 blocks를 근거로 사용하세요.

[운동 인식과 중량의 절대 규칙]
1. Tool의 recognition은 결정론적 파서가 확정한 값입니다. 운동명을 다시 추측하거나
   덤벨/원암의 중량 의미를 임의로 바꾸지 마세요.
2. load_interpretation이 per_hand이면 weight_kg는 덤벨 한 개, 한 손당 중량입니다.
   per_side이면 weight_kg는 한쪽 팔/한쪽당 중량입니다. blocks의 estimated_external_volume_kg에는
   sides_trained가 이미 반영되어 있으므로 다시 두 배로 곱하지 마세요.
3. 덤벨·원암 운동의 total_sets와 reps_per_set은 기록자가 적은 통상적 세트·반복 표기입니다.
   계획에 쓸 때 세트를 기계적으로 두 배로 쓰지 마세요. 단, 기록 해석에는 반드시
   '한 손당/한쪽당'이라는 표현을 한 번 이상 명시하세요.
4. 풀업의 weight_kg가 bodyweight_or_assistance_unspecified이면 체중, 추가중량, 보조중량 중
   무엇인지 알 수 없습니다. 이를 총 훈련량에 임의로 합산하거나 60kg 체중이라고 단정하지 마세요.
5. 머신/케이블의 표시 kg와 자유중량 kg의 volume은 장비 기구학이 달라 직접 우열 비교에 쓰지 마세요.

[해석 원칙]
- 여러 중량 구간이 있으면 blocks를 모두 보세요.
- RPE 9 이상은 높은 노력도라는 관측으로만 사용하세요. CNS 피로, 과훈련, 부상 위험,
  회복 완료를 확정 진단하지 마세요.
- 동일 운동의 기록이 있으면 그 실제 중량과 RPE를 참고할 수 있습니다. 없는 운동의 임의 kg는
  만들지 말고 목표 RPE로 중량을 선택하게 하세요.
- 운동 가능 시간 안에 현실적으로 끝나는 계획을 제시하세요.

[최종 답변의 순서]
1. 사용자 현재 상태
2. 최근 3일 운동 기록과 운동 인식
3. 최근 훈련량·RPE 해석
4. 회복 관련 고려사항
5. 오늘 운동 계획(운동명, 세트×반복, 목표 RPE, 중량 선택 기준, 휴식)
6. 이 계획을 선택한 이유
7. 분석의 한계
"""


def create_workout_agent() -> Any:
    if LANGCHAIN_IMPORT_ERROR is not None or ChatOpenAI is None or create_agent is None:
        raise RuntimeError(
            "LangChain 패키지가 설치되어 있지 않습니다. 프로젝트 루트에서 "
            "pip install -r requirements.txt 를 실행하세요."
        ) from LANGCHAIN_IMPORT_ERROR

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        dotenv_hint = (
            " python-dotenv도 설치되지 않아 .env를 읽지 못했습니다."
            if DOTENV_IMPORT_ERROR is not None
            else ""
        )
        raise ValueError("OPENAI_API_KEY가 .env 또는 환경 변수에 설정되어 있지 않습니다." + dotenv_hint)

    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        temperature=0,
        api_key=api_key,
    )
    return create_agent(
        model=model,
        tools=[get_recent_workout_summary, get_day_workout_detail, get_exercise_history],
        system_prompt=SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _message_value(message: Any, key: str, default: Any = None) -> Any:
    return message.get(key, default) if isinstance(message, dict) else getattr(message, key, default)


def count_tool_calls(messages: list[Any]) -> int:
    return sum(len(_message_value(message, "tool_calls", []) or []) for message in messages)


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content or "")


def print_agent_trace(messages: list[Any], attempt_number: int) -> None:
    print(f"\n=== AGENT TRACE (시도 {attempt_number}) ===")
    call_number = 0
    for message in messages:
        for tool_call in _message_value(message, "tool_calls", []) or []:
            call_number += 1
            if isinstance(tool_call, dict):
                print(f"[Tool 호출 {call_number}] {tool_call.get('name')} {tool_call.get('args')}")
            else:
                print(f"[Tool 호출 {call_number}] {tool_call}")
        tool_name = _message_value(message, "name")
        if tool_name:
            print(f"[Tool 결과 반환] {tool_name}")
    if call_number == 0:
        print("Tool이 호출되지 않았습니다.")


def contains_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def validate_final_answer(answer: str, tool_call_count: int, available_minutes: int, requires_per_side_language: bool) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not answer.strip():
        return False, ["최종 답변이 생성되지 않았습니다."]
    if tool_call_count == 0:
        errors.append("운동 기록 Tool이 호출되지 않았습니다.")
    if not contains_korean(answer):
        errors.append("최종 답변이 한국어가 아닙니다.")

    lower_answer = answer.lower()
    if "rpe" not in lower_answer:
        errors.append("RPE를 활용한 분석이 없습니다.")
    if not ("오늘" in answer and "세트" in answer):
        errors.append("오늘의 구체적인 운동 계획이 충분하지 않습니다.")
    if not any(token in answer for token in (f"{available_minutes}분", f"{available_minutes} 분", "운동 가능 시간", "시간 내")):
        errors.append("사용 가능한 운동 시간이 답변에 반영되지 않았습니다.")
    if requires_per_side_language and not any(
        token in answer for token in ("한 손당", "한손당", "한쪽당", "한 쪽당", "한 팔당", "한팔당", "덤벨 한 개")
    ):
        errors.append("덤벨/원암 중량이 한 손·한쪽당이라는 해석이 답변에 명시되지 않았습니다.")
    if not any(token in answer for token in ("한계", "데이터", "개인차", "추정", "가능성", "단정")):
        errors.append("분석의 한계 또는 불확실성이 명시되지 않았습니다.")

    dangerous_claims = (
        "cns 피로가 높다",
        "cns 피로가 높습니다",
        "중추신경계 피로가 높",
        "과훈련 상태입니다",
        "과훈련 상태이다",
        "회복되지 않았습니다",
        "회복되지 않았다",
        "부상 위험이 높습니다",
        "부상 위험이 높다",
    )
    for claim in dangerous_claims:
        if claim in lower_answer:
            errors.append(f"과도한 생리적 단정이 발견되었습니다: {claim}")
    return not errors, errors


def build_retry_request(original_request: str, previous_answer: str, validation_errors: list[str]) -> str:
    error_text = "\n".join(f"- {error}" for error in validation_errors)
    return f"""
이전 답변이 Harness 검증을 통과하지 못했습니다.

[원래 요청]
{original_request}

[이전 답변]
{previous_answer}

[검증 실패 이유]
{error_text}

Tool로 workout_log.json을 다시 확인하여 위 문제를 수정하세요. blocks와 recognition을 모두 확인하고,
덤벨/원암 중량은 반드시 한 손당 또는 한쪽당이라고 명시하세요. 최종 답변은 한국어로 작성하고,
운동 가능 시간을 반영하며 마지막에 분석의 한계를 설명하세요. CNS 피로·과훈련·부상 위험·회복 완료를
확정 진단하지 마세요.
"""


def run_with_harness(agent: Any, user_request: str, available_minutes: int, workouts: list[DailyWorkout]) -> str:
    semantic_errors = validate_workout_semantics(workouts)
    if semantic_errors:
        raise ValueError("운동 인식 사전 검증 실패:\n" + "\n".join(f"- {error}" for error in semantic_errors))

    requires_per_side_language = any(
        record.recognition.uses_per_side_weight
        for workout in workouts
        for record in workout.exercises
    )
    total_tool_calls = 0
    current_request = user_request
    last_answer = ""
    last_errors: list[str] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n{'=' * 40}\nHARNESS 실행 시도 {attempt}/{MAX_ATTEMPTS}\n{'=' * 40}")
        result = agent.invoke(
            {"messages": [{"role": "user", "content": current_request}]},
            config={"recursion_limit": AGENT_RECURSION_LIMIT},
        )
        messages = result.get("messages", [])
        if not messages:
            raise RuntimeError("Agent가 메시지를 반환하지 않았습니다.")
        print_agent_trace(messages, attempt)

        attempt_tool_calls = count_tool_calls(messages)
        total_tool_calls += attempt_tool_calls
        print(f"\n이번 시도 Tool 호출: {attempt_tool_calls}/{MAX_TOOL_CALLS_PER_ATTEMPT}")
        print(f"누적 Tool 호출: {total_tool_calls}/{MAX_TOTAL_TOOL_CALLS}")
        if attempt_tool_calls > MAX_TOOL_CALLS_PER_ATTEMPT:
            raise RuntimeError("한 번의 Agent 실행에서 Tool 호출 예산을 초과했습니다.")
        if total_tool_calls > MAX_TOTAL_TOOL_CALLS:
            raise RuntimeError("Harness 전체 Tool 호출 예산을 초과했습니다.")

        last_answer = content_to_text(_message_value(messages[-1], "content", ""))
        passed, errors = validate_final_answer(
            answer=last_answer,
            tool_call_count=attempt_tool_calls,
            available_minutes=available_minutes,
            requires_per_side_language=requires_per_side_language,
        )
        if passed:
            print("\n=== HARNESS 검증 ===\nPASS: 모든 검증 조건을 통과했습니다.")
            return last_answer

        last_errors = errors
        print("\n=== HARNESS 검증 ===\nFAIL:")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        if attempt < MAX_ATTEMPTS:
            current_request = build_retry_request(user_request, last_answer, errors)

    error_summary = "\n".join(f"- {error}" for error in last_errors)
    raise RuntimeError(f"최대 재시도 횟수에 도달했습니다.\n{error_summary}\n\n마지막 답변:\n{last_answer}")


# ---------------------------------------------------------------------------
# Interactive input
# ---------------------------------------------------------------------------

def ask_text(prompt: str, default: Optional[str] = None) -> str:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("값을 입력해주세요.")


def ask_int(prompt: str, minimum: int, maximum: int, default: Optional[int] = None) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"{minimum}~{maximum} 사이의 정수를 입력해주세요.")


def ask_float(prompt: str, minimum: float, maximum: float, default: Optional[float] = None) -> float:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = float(raw)
            if math.isfinite(value) and minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"{minimum}~{maximum} 사이의 숫자를 입력해주세요.")


def ask_optional_float(prompt: str, minimum: float, maximum: float) -> Optional[float]:
    while True:
        raw = input(f"{prompt} (모르면 Enter): ").strip()
        if not raw:
            return None
        try:
            value = float(raw)
            if math.isfinite(value) and minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"{minimum}~{maximum} 사이의 숫자를 입력해주세요.")


def collect_user_input() -> tuple[UserProfile, str, list[DailyWorkout]]:
    print("\n" + "=" * 40)
    print("Adaptive Workout Planner Agent")
    print("=" * 40)
    print("운동 기록은 agent.py와 같은 폴더의 workout_log.json에서 읽습니다.")

    workouts = read_workout_log()
    if not workouts:
        raise ValueError("workout_log.json에 운동 기록이 없습니다.")
    semantic_errors = validate_workout_semantics(workouts)
    if semantic_errors:
        raise ValueError("운동 인식 사전 검증 실패:\n" + "\n".join(semantic_errors))

    profile = UserProfile(
        goal=ask_text("운동 목표 (예: 근비대 / 근력 / 일반 체력)", "근비대"),
        experience_level=ask_text("운동 경력 (초보 / 중급 / 고급)", "중급"),
        available_minutes=ask_int("오늘 운동 가능한 시간(분)", 10, 240, 60),
        subjective_fatigue=ask_float("현재 주관적 피로도 (1=매우 낮음, 10=매우 높음)", 1, 10, 5),
        sleep_hours=ask_optional_float("지난밤 수면시간", 0, 24),
        soreness=ask_optional_float("현재 근육통 정도 (1=거의 없음, 10=매우 심함)", 1, 10),
    )
    additional_request = input("\n오늘 운동에 대한 추가 요청 (없으면 Enter): ").strip()
    return profile, additional_request, workouts


def build_user_request(profile: UserProfile, additional_request: str) -> str:
    sleep_text = f"{profile.sleep_hours}시간" if profile.sleep_hours is not None else "미입력"
    soreness_text = f"{profile.soreness}/10" if profile.soreness is not None else "미입력"
    return f"""
Adaptive Workout Planner 요청입니다.

[현재 사용자 상태]
- 운동 목표: {profile.goal}
- 운동 경력: {profile.experience_level}
- 오늘 운동 가능 시간: {profile.available_minutes}분
- 주관적 피로도: {profile.subjective_fatigue}/10
- 지난밤 수면시간: {sleep_text}
- 현재 근육통: {soreness_text}

[추가 요청]
{additional_request or '특별한 추가 요청 없음'}

workout_log.json의 최근 3일 기록을 Tool로 직접 확인하고 오늘 계획을 세우세요. 여러 중량 구간은
blocks를 모두 보며, recognition의 한 손당/한쪽당 중량 규칙을 준수하세요. 계획은 {profile.available_minutes}분
이내여야 하며, 각 운동의 세트×반복·목표 RPE·중량 선택 기준·휴식시간을 포함하세요.
"""


# ---------------------------------------------------------------------------
# Offline self-test: supplied workout case + edge cases
# ---------------------------------------------------------------------------

SELF_TEST_LOG = [
    {
        "day_ago": 1,
        "exercises": [
            {"exercise": "스미스 머신 벤치 프레스", "weight_kg": [100, 90, 80], "reps_per_set": [6, 8, 8], "total_sets": [2, 2, 2], "rpe": [9, 9, 9]},
            {"exercise": "덤벨 체스트 프레스", "weight_kg": [30, 28], "reps_per_set": [8, 8], "total_sets": [2, 2], "rpe": [9, 9]},
            {"exercise": "인클라인 체스트 프레스", "weight_kg": 70, "reps_per_set": 8, "total_sets": 4, "rpe": 9},
            {"exercise": "인클라인 머신 체스트 프레스", "weight_kg": 70, "reps_per_set": 8, "total_sets": 4, "rpe": 9},
        ],
    },
    {
        "day_ago": 2,
        "exercises": [
            {"exercise": "풀업", "weight_kg": 60, "reps_per_set": 8, "total_sets": 3, "rpe": 5},
            {"exercise": "티바로우", "weight_kg": [57.5, 40], "reps_per_set": [8, 10], "total_sets": [3, 1], "rpe": [9, 7.5]},
            {"exercise": "렛풀다운", "weight_kg": 70, "reps_per_set": 8, "total_sets": 4, "rpe": 9},
            {"exercise": "원암 하이로우", "weight_kg": 30, "reps_per_set": 8, "total_sets": 3, "rpe": 9},
            {"exercise": "파나타 머신 하이로우", "weight_kg": 70, "reps_per_set": 8, "total_sets": 3, "rpe": 9},
        ],
    },
    {
        "day_ago": 3,
        "exercises": [
            {"exercise": "오버헤드 프레스", "weight_kg": [50, 40], "reps_per_set": [8, 10], "total_sets": [4, 1], "rpe": [9, 8]},
            {"exercise": "머신 숄더 프레스", "weight_kg": 90, "reps_per_set": 8, "total_sets": 4, "rpe": 9},
            {"exercise": "스탠딩 숄더 프레스", "weight_kg": 30, "reps_per_set": 40, "total_sets": 3, "rpe": 9},
            {"exercise": "덤벨 사이드레터럴 레이즈", "weight_kg": 5, "reps_per_set": 40, "total_sets": 3, "rpe": 9},
            {"exercise": "원암 머신 사이드레터럴 레이즈", "weight_kg": 5, "reps_per_set": 30, "total_sets": 3, "rpe": 9},
            {"exercise": "페이스 풀", "weight_kg": 40, "reps_per_set": 10, "total_sets": 4, "rpe": 8},
            {"exercise": "덤벨 벤트오버 레터럴 레이즈", "weight_kg": 2, "reps_per_set": 45, "total_sets": 2, "rpe": 8},
        ],
    },
]


def _find_record(workouts: list[DailyWorkout], day_ago: int, exercise: str) -> ExerciseRecord:
    for workout in workouts:
        if workout.day_ago == day_ago:
            for record in workout.exercises:
                if record.exercise == exercise:
                    return record
    raise AssertionError(f"테스트 운동을 찾지 못했습니다: {day_ago}일 전 {exercise}")


def run_self_test() -> None:
    workouts = parse_workout_data(SELF_TEST_LOG, "SELF_TEST_LOG")
    assert not validate_workout_semantics(workouts)

    dumbbell_press = analyze_exercise(_find_record(workouts, 1, "덤벨 체스트 프레스"))
    assert dumbbell_press["recognition"]["load_interpretation"] == LOAD_PER_HAND
    assert dumbbell_press["blocks"][0]["weight_display"] == "30kg/한 손(덤벨 한 개당)"
    assert dumbbell_press["estimated_external_volume_kg"] == 1856.0

    one_arm_row = analyze_exercise(_find_record(workouts, 2, "원암 하이로우"))
    assert one_arm_row["recognition"]["load_interpretation"] == LOAD_PER_SIDE
    assert one_arm_row["blocks"][0]["estimated_effective_load_kg"] == 60.0
    assert one_arm_row["estimated_external_volume_kg"] == 1440.0

    shorthand_rear_delt = analyze_exercise(_find_record(workouts, 3, "덤벨 벤트오버 레터럴 레이즈"))
    assert shorthand_rear_delt["recognition"]["load_interpretation"] == LOAD_PER_HAND
    assert shorthand_rear_delt["estimated_external_volume_kg"] == 360.0
    no_dumbbell_word = recognize_exercise("벤트오버 레터럴 레이즈")
    assert no_dumbbell_word.load_interpretation == LOAD_PER_HAND
    assert no_dumbbell_word.source == "catalog"

    pullup = analyze_exercise(_find_record(workouts, 2, "풀업"))
    assert pullup["recognition"]["load_interpretation"] == LOAD_BODYWEIGHT_UNKNOWN
    assert pullup["estimated_external_volume_kg"] is None
    assert pullup["blocks"][0]["weight_display"] == "60kg(체중/추가/보조 의미 미상)"

    summary = summarize_recent_workouts(workouts)
    assert summary["estimated_external_volume_kg"] == 29936.0

    comma_data = [{
        "day_ago": 1,
        "exercises": [{
            "exercise": "덤벨 컬",
            "weight_kg": "10, 8",
            "reps_per_set": "10, 12",
            "total_sets": "2, 1",
            "rpe": "8",
        }],
    }]
    comma_record = parse_workout_data(comma_data, "comma_data")[0].exercises[0]
    assert len(comma_record.blocks) == 2
    assert analyze_exercise(comma_record)["estimated_external_volume_kg"] == 592.0

    try:
        parse_workout_data([{
            "day_ago": 1,
            "exercises": [{"exercise": "벤치 프레스", "weight_kg": 100, "reps_per_set": 8.5, "total_sets": 3, "rpe": 8}],
        }], "invalid_data")
    except ValueError:
        pass
    else:
        raise AssertionError("소수 반복수는 거부되어야 합니다.")

    print("SELF-TEST PASS")
    print("- 덤벨 중량: 한 손당으로 인식, 양손 총 외부부하 반영")
    print("- 원암 중량: 한쪽당으로 인식, 좌우 수행 총 외부부하 반영")
    print("- 벤트오버 레터럴 레이즈: 카탈로그로 덤벨 운동 인식")
    print("- 풀업 60kg: 체중/보조/추가중량 미상으로 훈련량에서 제외")
    print("- 배열/쉼표 입력, broadcast, 정수 검증 통과")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive Workout Planner Agent")
    parser.add_argument("--self-test", action="store_true", help="API 호출 없이 파서와 중량 의미를 검증합니다.")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return

    try:
        profile, additional_request, workouts = collect_user_input()
        agent = create_workout_agent()
        final_answer = run_with_harness(
            agent=agent,
            user_request=build_user_request(profile, additional_request),
            available_minutes=profile.available_minutes,
            workouts=workouts,
        )
        print("\n" + "=" * 40)
        print("최종 운동 계획")
        print("=" * 40)
        print(final_answer)
    except FileNotFoundError as error:
        print("\n=== 파일 오류 ===\n" + str(error))
    except json.JSONDecodeError as error:
        print("\n=== JSON 오류 ===\nworkout_log.json의 JSON 문법이 올바르지 않습니다.\n" + str(error))
    except (ValueError, RuntimeError) as error:
        print("\n=== 실행 오류 ===\n" + str(error))
    except KeyboardInterrupt:
        print("\n\n사용자가 실행을 중단했습니다.")
    except Exception as error:
        print(f"\n=== 예상하지 못한 오류 ===\n{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
