"""입력 오류, 업무 조건 오류, 파일 오류를 구분한다."""


class RuleError(ValueError):
    """현재 입력 단계에서 안내할 오류."""


class DataError(Exception):
    """잘못된 CSV: 자료를 수정하지 않고 실행을 중단한다."""

    def __init__(self, filename, line, field, reason):
        super().__init__(f"{filename}: {line}행 / {field}: {reason}")


class StorageError(Exception):
    """파일 입출력 실패: 성공 메시지를 출력하면 안 된다."""


class CancelInput(Exception):
    """Ctrl+C로 현재 작업을 취소한다."""


class EndInput(Exception):
    """입력 스트림 종료."""
