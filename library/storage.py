"""UTF-8 CSV 입출력, 시작 시 무결성 검사, 여러 파일의 저장/복구.

정상 데이터는 네 CSV에만 저장한다. .transaction.json은 저장 도중에만
존재하는 복구 기록이다. 이를 지운 시점을 저장 완료 시점으로 삼는다.
"""

import base64
import csv
import io
import json
import os
import signal
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from . import validation as v
from .auth import valid_hash
from .errors import DataError, RuleError, StorageError

SCHEMAS = {
    "users": ("user_id", "display_name", "password_hash", "role", "active"),
    "books": ("book_id", "title", "author", "publisher", "published_year", "isbn", "category", "active"),
    "copies": ("copy_id", "book_id", "active"),
    "loans": ("loan_id", "user_id", "copy_id", "loan_date", "due_date", "return_date"),
}


@contextmanager
def finish_save_before_interrupt():
    """저장 임계 구간에서는 Ctrl+C를 완료 뒤 전달해 반쪽 저장을 피한다."""
    interrupted = []
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda signum, frame: interrupted.append(True))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt


def encode_csv(table, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=SCHEMAS[table], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def check_csv_quotes(text, filename):
    """csv.reader가 관대하게 허용하는 따옴표의 잘못된 위치도 거부한다."""
    state, line, previous = "start", 1, ""
    for ch in text:
        if state == "quoted":
            if ch == '"':
                state = "closed"
        elif state == "closed":
            if ch == '"':
                state = "quoted"
            elif ch == "," or ch in "\r\n":
                state = "start"
            else:
                raise DataError(filename, line, "CSV 문법", "닫는 따옴표 뒤에는 구분자 또는 줄바꿈이 필요합니다.")
        elif ch == '"':
            if state != "start":
                raise DataError(filename, line, "CSV 문법", "필드 안의 큰따옴표는 인용하고 두 번 써야 합니다.")
            state = "quoted"
        elif ch == "," or ch in "\r\n":
            state = "start"
        else:
            state = "plain"
        if ch == "\n" and previous != "\r" or ch == "\r":
            line += 1
        previous = ch
    if state == "quoted":
        raise DataError(filename, line, "CSV 문법", "닫히지 않은 큰따옴표가 있습니다.")


def parse_csv(table, content):
    filename = table + ".csv"
    try:
        text = content.decode("utf-8-sig")  # UTF-8 BOM이 있는 파일도 읽는다.
    except UnicodeDecodeError as exc:
        line = content[:exc.start].count(b"\n") + 1
        raise DataError(filename, line, "인코딩", "UTF-8 파일이어야 합니다.") from None
    check_csv_quotes(text, filename)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows, lines = [], []
    try:
        header = next(reader, None)
        if header != list(SCHEMAS[table]):
            raise DataError(filename, 1, "헤더", "열 이름과 순서가 일치해야 합니다: " + ",".join(SCHEMAS[table]))
        while True:
            line = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            if len(values) != len(header):
                raise DataError(filename, line, "열 수", f"{len(header)}개가 필요하지만 {len(values)}개입니다.")
            rows.append(dict(zip(header, values)))
            lines.append(line)
    except csv.Error as exc:
        raise DataError(filename, reader.line_num or 1, "CSV 문법", str(exc)) from None
    return rows, lines


def validate_data(data, lines=None):
    """시작 시 모든 파일을 읽은 뒤 필드 → 중복 → 참조 순서로 검사한다."""
    lines = lines or {key: list(range(2, len(rows) + 2)) for key, rows in data.items()}

    def fail(table, index, field, reason):
        raise DataError(table + ".csv", lines[table][index], field, reason)

    def check(table, index, field, validator):
        try:
            validator(data[table][index][field])
        except RuleError as exc:
            fail(table, index, field, str(exc))

    for table, rows in data.items():
        key = SCHEMAS[table][0]
        seen = set()
        for index, row in enumerate(rows):
            if set(row) != set(SCHEMAS[table]):
                fail(table, index, "열 구조", "필요한 필드가 누락되었거나 추가되었습니다.")
            if table == "users":
                check(table, index, key, v.user_id)
            else:
                check(table, index, key, lambda value: v.identifier(value, key))
            identity = row[key].lower() if table == "users" else row[key]
            if identity in seen:
                fail(table, index, key, "중복 ID입니다.")
            seen.add(identity)
            if "active" in row and row["active"] not in ("true", "false"):
                fail(table, index, "active", "true 또는 false여야 합니다.")
            if table == "users":
                check(table, index, "display_name", v.display_name)
                if row["display_name"] != row["display_name"].strip():
                    fail(table, index, "display_name", "앞뒤 공백이 제거된 값이어야 합니다.")
                if not valid_hash(row["password_hash"]):
                    fail(table, index, "password_hash", "지원하는 PBKDF2 해시 형식이 아닙니다. 원문 비밀번호는 사용할 수 없습니다.")
                if row["role"] not in ("member", "admin"):
                    fail(table, index, "role", "member 또는 admin이어야 합니다.")
            elif table == "books":
                for field in ("title", "author", "publisher", "published_year", "isbn", "category"):
                    check(table, index, field, lambda value, f=field: v.book_field(f, value))
                    if row[field] != row[field].strip():
                        fail(table, index, field, "앞뒤 공백이 제거된 값이어야 합니다.")
            elif table == "copies":
                check(table, index, "book_id", lambda value: v.identifier(value, "book_id"))
            else:
                check(table, index, "user_id", v.user_id)
                check(table, index, "copy_id", lambda value: v.identifier(value, "copy_id"))
                for field in ("loan_date", "due_date", "return_date"):
                    if field != "return_date" or row[field] != "":
                        check(table, index, field, v.date_value)
                try:
                    due = v.date_value(row["loan_date"]) + timedelta(days=14)
                except OverflowError:
                    fail(table, index, "loan_date", "14일 뒤가 지원 날짜 범위를 벗어납니다.")
                if due.isoformat() != row["due_date"]:
                    fail(table, index, "due_date", "반납기한은 대출일의 14일 뒤여야 합니다.")

    users = {row["user_id"]: row for row in data["users"]}
    books = {row["book_id"]: row for row in data["books"]}
    copies = {row["copy_id"]: row for row in data["copies"]}
    for i, row in enumerate(data["copies"]):
        if row["book_id"] not in books:
            fail("copies", i, "book_id", "존재하지 않는 도서정보를 참조합니다.")
        if row["active"] == "true" and books[row["book_id"]]["active"] != "true":
            fail("copies", i, "active", "비활성 도서정보에는 활성 권본이 있을 수 없습니다.")
    occupied = set()
    for i, row in enumerate(data["loans"]):
        if row["user_id"] not in users:
            fail("loans", i, "user_id", "존재하지 않는 회원을 참조합니다.")
        if users[row["user_id"]]["role"] != "member":
            fail("loans", i, "user_id", "관리자 계정은 대출자가 될 수 없습니다.")
        if row["copy_id"] not in copies:
            fail("loans", i, "copy_id", "존재하지 않는 권본을 참조합니다.")
        if row["return_date"] == "":
            if row["copy_id"] in occupied:
                fail("loans", i, "copy_id", "같은 권본에 활성 대출이 두 건 이상 있습니다.")
            if copies[row["copy_id"]]["active"] != "true":
                fail("loans", i, "copy_id", "제적된 권본에 활성 대출이 있습니다.")
            occupied.add(row["copy_id"])


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.journal = self.directory / ".transaction.json"
        self.data = {key: [] for key in SCHEMAS}
        self.raw = {}
        self.recovered = False

    def _read(self, path):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError(f"{path.name}: 읽기 실패: {exc}") from exc

    def _replace(self, path, content):
        """같은 폴더의 임시 파일을 완전히 쓴 다음 원자적으로 교체한다."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".a03-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise StorageError(f"{path.name}: 저장 실패: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _restore(self, before):
        for filename, content in before.items():
            path = self.directory / filename
            if content is None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    raise StorageError(f"{filename}: 복구 실패: {exc}") from exc
            else:
                self._replace(path, content)

    def _remove_journal(self):
        try:
            self.journal.unlink()
        except OSError as exc:
            raise StorageError(f"{self.journal.name}: 저장 완료 처리 실패: {exc}") from exc

    def _recover(self):
        content = self._read(self.journal)
        if content is None:
            return
        try:
            record = json.loads(content)
            if record["version"] != 1 or set(record) != {"version", "before"}:
                raise ValueError("지원하지 않는 복구 기록입니다.")
            items = record["before"]
            if not isinstance(items, dict) or not items or not set(items) <= {k + ".csv" for k in SCHEMAS}:
                raise ValueError("복구 대상 파일 목록이 잘못되었습니다.")
            before = {name: None if value is None else base64.b64decode(value, validate=True)
                      for name, value in items.items()}
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise StorageError(f"{self.journal.name}: 복구 기록 오류. 원본을 보존하고 종료합니다: {exc}") from exc
        with finish_save_before_interrupt():
            self._restore(before)
            self._remove_journal()
            self.recovered = True

    def _write_transaction(self, changes):
        before = {}
        # 변경 대상 외의 참조 파일이 외부에서 바뀐 경우에도 저장하지 않는다.
        for name in self.raw:
            current = self._read(self.directory / name)
            if current != self.raw.get(name):
                raise StorageError(f"{name}: 실행 중 파일이 외부에서 변경되었습니다. 덮어쓰지 않고 종료합니다.")
            if name in changes:
                before[name] = current
        record = {"version": 1, "before": {
            name: None if value is None else base64.b64encode(value).decode("ascii")
            for name, value in before.items()
        }}
        self._replace(self.journal, json.dumps(record).encode("utf-8"))
        try:
            for name, content in changes.items():
                self._replace(self.directory / name, content)
            self._remove_journal()
        except StorageError as original:
            try:
                self._restore(before)
                self._remove_journal()
            except StorageError as recovery:
                raise StorageError(f"{original}\n복구를 완료하지 못했습니다: {recovery}\n"
                                   "data 폴더의 복구 기록을 보존해 주세요. 다음 실행 시 다시 복구합니다.") from original
            raise original
        self.raw.update(changes)

    def load(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"{self.directory}: 폴더 생성 실패: {exc}") from exc
        self._recover()
        lines, missing = {}, {}
        for table in SCHEMAS:
            name = table + ".csv"
            content = self._read(self.directory / name)
            self.raw[name] = content
            if content is None:
                self.data[table], lines[table] = [], []
                missing[name] = encode_csv(table, [])
            else:
                self.data[table], lines[table] = parse_csv(table, content)
        # 오류 파일이 있으면 새 헤더조차 쓰지 않는다.
        validate_data(self.data, lines)
        if missing:
            with finish_save_before_interrupt():
                self._write_transaction(missing)

    def snapshot(self):
        return deepcopy(self.data)

    def commit(self, new_data):
        validate_data(new_data)
        changes = {table + ".csv": encode_csv(table, rows)
                   for table, rows in new_data.items() if rows != self.data[table]}
        if changes:
            with finish_save_before_interrupt():
                self._write_transaction(changes)
                self.data = deepcopy(new_data)
