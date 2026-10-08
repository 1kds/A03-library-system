"""A03 도서 대출·반납 관리 시스템. 실행: python main.py"""

import sys
from pathlib import Path

from library.cli import Console, ask
from library.errors import CancelInput, DataError, EndInput, StorageError
from library.service import Library
from library.storage import Store
from library.validation import date_value


def main():
    if len(sys.argv) != 1:
        print("실행 인자는 받지 않습니다. python main.py로 실행해 주세요.")
        return 2
    store = Store(Path(__file__).resolve().parent / "data")
    try:
        store.load()
        if store.recovered:
            print("중단된 저장 작업을 마지막 저장 완료 상태로 복구했습니다.")
        print("도서 대출·반납 관리 시스템을 시작합니다.")
        current_date = ask("시스템의 가상 현재 일시를 입력하세요 (YYYY-MM-DD)> ", date_value)
        print("가상 현재 일시: " + current_date.isoformat())
        app = Console(Library(store, current_date))
        if not app.lib.has_admin():
            app.signup(first_admin=True)
        app.visitor_menu()
    except DataError as exc:
        print(f"!!! 데이터 오류: {exc}\n자료를 자동 수정하거나 덮어쓰지 않고 종료합니다.")
        return 1
    except StorageError as exc:
        print(f"!!! 파일 오류: {exc}\n현재 작업의 성공을 확인할 수 없어 종료합니다.")
        return 1
    except EndInput:
        print("입력 스트림이 종료되었습니다. 저장을 마친 작업을 유지하고 종료합니다.")
        return 0
    except (CancelInput, KeyboardInterrupt):
        print("입력을 취소했습니다. 저장을 마친 작업을 유지하고 종료합니다.")
        return 0
    print("저장 완료. 프로그램을 종료합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
