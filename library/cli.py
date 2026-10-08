"""사용자에게 보이는 메뉴·입력·출력. 업무 규칙은 service.py에 위임한다."""

import getpass
import unicodedata
import warnings

from . import validation as v
from .errors import CancelInput, EndInput, RuleError


def safe_text(value):
    """CSV의 제어 문자가 터미널 제어 명령으로 실행되지 않도록 표시한다."""
    text = str(value) if value != "" else "-"
    output = []
    for ch in text:
        if unicodedata.category(ch).startswith("C"):
            output.append(f"\\u{ord(ch):04x}")
        elif ch == "|":
            output.append("｜")
        else:
            output.append(ch)
    return "".join(output)


def width(text):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def wrap_cell(text, maximum):
    parts, part, used = [], "", 0
    for ch in text:
        size = width(ch)
        if used + size > maximum:
            parts.append(part)
            part, used = "", 0
        part += ch
        used += size
    parts.append(part)
    return parts


def table(headers, rows):
    """긴 제목도 생략하지 않고 줄을 나눈다. 한글 표시 폭을 고려한다."""
    values = [[safe_text(cell) for cell in row] for row in rows]
    sizes = [max(width(h), min(24, max((width(row[i]) for row in values), default=0)))
             for i, h in enumerate(headers)]

    def line(cells):
        print(" | ".join(cell + " " * max(0, sizes[i] - width(cell)) for i, cell in enumerate(cells)))

    line(headers)
    print("-+-".join("-" * size for size in sizes))
    for row in values:
        wrapped = [wrap_cell(cell, sizes[i]) for i, cell in enumerate(row)]
        for j in range(max(len(cell) for cell in wrapped)):
            line([cell[j] if j < len(cell) else "" for cell in wrapped])


def read(prompt, secret=False):
    try:
        if secret:
            # getpass가 비밀번호를 화면에 노출하는 fallback은 허용하지 않는다.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                return getpass.getpass(prompt)
        return input(prompt)
    except getpass.GetPassWarning:
        print("비밀번호를 숨겨 입력할 수 없습니다. 운영체제의 터미널에서 python main.py로 실행해 주세요.")
        raise EndInput from None
    except KeyboardInterrupt:
        print()
        raise CancelInput from None
    except EOFError:
        print()
        raise EndInput from None


def ask(prompt, validator, secret=False):
    while True:
        try:
            return validator(read(prompt, secret))
        except RuleError as exc:
            print(f".!! 오류: {exc}")


def confirm(prompt):
    while True:
        value = read(prompt + " [Y/N]> ")
        if value in ("Y", "y"):
            return True
        if value in ("N", "n"):
            return False
        print(".!! 오류: Y 또는 N을 입력해 주세요.")


def pause(destination):
    while read(f"[엔터 키를 누르면 {destination}로 돌아갑니다]> ") != "":
        print("엔터 키만 눌러 주세요.")


class Console:
    def __init__(self, library):
        self.lib = library

    @staticmethod
    def show_menu(title, choices, zero):
        print("\n" + title + ": " + " ".join(f"[{key}] {label}" for key, (label, _) in choices.items()) + f" [0] {zero}")

    def menu(self, title, choices, zero="돌아가기"):
        while True:
            self.show_menu(title, choices, zero)
            try:
                value = read("선택> ").strip()
            except CancelInput:
                print("입력을 취소했습니다.")
                return
            if value == "0":
                return
            if len(value) != 1 or value not in choices:
                print(".!! 오류: 허용되지 않은 메뉴 번호입니다. 표시된 한 자리 ASCII 숫자를 입력해 주세요.")
                continue
            try:
                choices[value][1]()
            except (CancelInput, KeyboardInterrupt):
                print("입력을 취소했습니다. 저장을 마친 작업은 유지됩니다.")
            except EndInput:
                # EOF가 지속되는 입력 스트림을 무한히 다시 읽지 않는다.
                self.show_menu(title, choices, zero)
                raise
            except RuleError as exc:
                print(f".!! 오류: {exc}")

    def signup(self, first_admin=False):
        print("\n[최초 관리자 등록]" if first_admin else "\n[회원 가입]")
        user_id = ask("회원 ID> ", self.lib.check_new_user_id)
        name = ask("표시 이름> ", v.display_name)
        password = ask("비밀번호 (8~64자, 화면에 표시되지 않음)> ", v.password, secret=True)
        while read("비밀번호 확인> ", secret=True) != password:
            print(".!! 오류: 비밀번호가 일치하지 않습니다. 확인 값을 다시 입력해 주세요.")
        identity = self.lib.signup(user_id, name, password, first_admin)
        print(f"{'최초 관리자 등록' if first_admin else '회원 가입'} 완료: {identity}")

    def login(self):
        user_id = read("회원 ID> ")
        password = read("비밀번호> ", secret=True)
        if not self.lib.login(user_id, password):
            print("회원 ID 또는 비밀번호를 확인해 주세요.")
            return
        print(f"로그인 완료: {safe_text(self.lib.user['display_name'])}")
        try:
            if self.lib.user["role"] == "admin":
                self.admin_menu()
            else:
                self.member_menu()
        finally:
            self.lib.logout()
        print("로그아웃했습니다.")

    def visitor_menu(self):
        self.menu("방문자 메뉴", {"1": ("도서 검색", self.search), "2": ("회원 가입", self.signup),
                                 "3": ("로그인", self.login)}, zero="종료")

    def member_menu(self):
        self.menu("회원 메뉴", {"1": ("도서 검색·대출", lambda: self.search(borrow=True)),
                               "2": ("내 대출 목록", self.my_loans),
                               "3": ("반납", self.member_return)}, zero="로그아웃")

    def admin_menu(self):
        self.menu("관리자 메뉴", {"1": ("도서정보", self.books_menu), "2": ("권본", self.copies_menu),
                                 "3": ("회원", self.members_menu), "4": ("대출 현황", self.history_menu),
                                 "5": ("반납", self.admin_return)}, zero="로그아웃")

    def search(self, borrow=False, admin=False):
        while True:
            query = ask("검색할 도서의 정보 입력 (입력 없이 Enter=취소)> ", v.search_query)
            if query is None:
                print("..! 안내: 검색기능 이용을 취소하였습니다. 메뉴로 돌아갑니다.")
                return
            results = self.lib.search_books(query, admin=admin)
            if not results:
                print("..! 안내: 검색된 결과가 없습니다.")
                continue
            if admin:
                table(["도서ID", "도서명", "저자", "출판사", "발행 연도", "ISBN", "분류", "전체", "대출 가능", "상태"],
                      [[b["book_id"], b["title"], b["author"], b["publisher"], b["published_year"], b["isbn"],
                        b["category"] + " " + v.KDC[b["category"]], b["total"], b["available"],
                        "활성" if b["active"] == "true" else "비활성"] for b in results])
            else:
                table(["번호", "도서ID", "도서명", "저자", "분류", "출판사", "전체", "대출 가능"],
                      [[i, b["book_id"], b["title"], b["author"], b["category"] + " " + v.KDC[b["category"]],
                        b["publisher"], str(b["total"]) + "권", str(b["available"]) + "권"]
                       for i, b in enumerate(results, 1)])
            print(f"검색 결과가 {len(results)}건입니다.")
            if borrow:
                selected = ask("대출할 도서 번호 (0=취소)> ", lambda value: v.loan_selection(value, len(results)))
                if selected is None:
                    print("대출을 취소했습니다.")
                    return
                try:
                    loan = self.lib.borrow(results[selected]["book_id"])
                except RuleError as exc:
                    print(f"!!! 오류: {exc}")
                    return
                print(f"... 대출 완료: {loan['loan_id']} / {safe_text(loan['title'])} / 권본 {loan['copy_id']}")
                print(f"==> 대출일: {loan['loan_date']}\n==> 반납기한: {loan['due_date']}")
                pause("회원 메뉴")
            else:
                pause("도서정보 메뉴" if admin else "방문자 메뉴")
            return

    @staticmethod
    def loan_table(loans, admin=False, history=False):
        headers = ["대출ID", "도서명", "권본ID", "대출일", "반납기한", "상태"]
        fields = ["loan_id", "title", "copy_id", "loan_date", "due_date", "status"]
        if admin:
            headers[1:1], fields[1:1] = ["회원ID", "회원 이름"], ["user_id", "display_name"]
        if history:
            headers.insert(-1, "반납일")
            fields.insert(-1, "return_date")
        table(headers, [[loan[field] for field in fields] for loan in loans])

    def my_loans(self):
        loans = self.lib.my_loans()
        if not loans:
            print("..! 안내: 현재 대출 중인 도서가 없습니다.")
        else:
            self.loan_table(loans)
        pause("회원 메뉴")

    def member_return(self):
        while True:
            loans = self.lib.my_loans()
            if not loans:
                print("..! 안내: 현재 대출 중인 도서가 없습니다.")
                pause("회원 메뉴")
                return
            self.loan_table(loans)
            try:
                loan = self.lib.return_target(read("반납할 대출 ID> "))
            except RuleError as exc:
                print(f".!! 오류: {exc}")
                continue
            if confirm(f"{loan['loan_id']} {safe_text(loan['title'])} (권본 {loan['copy_id']})를 반납할까요?"):
                returned = self.lib.return_loan(loan["loan_id"])
                print(f"반납 완료: {returned}")
                return
            print("반납을 취소했습니다. 반납 목록으로 돌아갑니다.")

    def books_menu(self):
        self.menu("도서정보 메뉴", {"1": ("조회·검색", lambda: self.search(admin=True)),
                                   "2": ("신규 등록", self.new_book), "3": ("수정", self.edit_book),
                                   "4": ("비활성화", self.deactivate_book)}, zero="관리자 메뉴")

    def choose_record(self, table_name, field, prompt):
        return ask(prompt, lambda identity: self.lib.find(table_name, v.identifier(identity, field)))

    @staticmethod
    def book_info(book):
        fields = [("도서ID", "book_id"), ("도서명", "title"), ("저자", "author"),
                  ("출판사", "publisher"), ("발행 연도", "published_year"), ("ISBN", "isbn")]
        for label, field in fields:
            if field in book:
                print(f"{label}: {safe_text(book[field])}")
        print(f"분류: {book['category']} {v.KDC[book['category']]}")
        if "active" in book:
            print("상태: " + ("활성" if book["active"] == "true" else "비활성"))
        if "total" in book:
            print(f"전체 소장 권본: {book['total']}권 / 대출 가능: {book['available']}권")

    @staticmethod
    def read_book_fields():
        result = {}
        fields = [("title", "도서명> "), ("author", "저자> "), ("publisher", "출판사 (선택)> "),
                  ("published_year", "발행 연도 (선택)> "), ("isbn", "ISBN (선택)> "),
                  ("category", "분류 번호> ")]
        for field, prompt in fields:
            if field == "category":
                print(" / ".join(f"{key} {name}" for key, name in v.KDC.items()))
            result[field] = ask(prompt, lambda value, f=field: v.book_field(f, value))
        return result

    def new_book(self):
        fields = self.read_book_fields()
        self.book_info(fields)
        if confirm("도서정보를 등록하시겠습니까?"):
            print("도서정보 등록 완료: " + self.lib.save_book(fields))
        else:
            print("도서정보 등록을 취소했습니다.")

    def edit_book(self):
        book = self.choose_record("books", "book_id", "수정할 도서정보 ID> ")
        self.book_info(book)
        print("각 항목을 다시 입력해 주세요. 선택 항목을 비우면 기존 값이 삭제됩니다.")
        fields = self.read_book_fields()
        self.book_info(dict(book, **fields))
        if confirm("변경 내용을 저장하시겠습니까?"):
            print("도서정보 수정 완료: " + self.lib.save_book(fields, book["book_id"]))
        else:
            print("도서정보 수정을 취소했습니다.")

    def deactivate_book(self):
        book = self.choose_record("books", "book_id", "비활성화할 도서정보 ID> ")
        self.book_info(self.lib.deactivation_target(book["book_id"]))
        if confirm("도서정보를 비활성화하고 연결된 활성 권본을 모두 제적하시겠습니까?"):
            self.lib.deactivate_book(book["book_id"])
            print("도서정보 비활성화 및 연결 권본 제적 완료.")
        else:
            print("비활성화를 취소했습니다.")

    def copies_menu(self):
        self.menu("권본 메뉴", {"1": ("조회", self.show_copies), "2": ("신규 등록", self.new_copy),
                               "3": ("제적", self.retire_copy)}, zero="관리자 메뉴")

    def show_copies(self):
        book = self.choose_record("books", "book_id", "도서정보 ID> ")
        print(f"도서명: {safe_text(book['title'])} / 저자: {safe_text(book['author'])}")
        rows, counts = [], {"대출 가능": 0, "대출 중": 0, "제적": 0}
        for copy in self.lib.copies_for(book["book_id"]):
            active = self.lib.active_loans(copy_id=copy["copy_id"])
            state = "제적" if copy["active"] == "false" else "대출 중" if active else "대출 가능"
            counts[state] += 1
            rows.append([copy["copy_id"], state, active[0]["loan_id"] if active else "-",
                         active[0]["user_id"] if active else "-"])
        if rows:
            table(["권본ID", "상태", "대출ID", "회원ID"], rows)
        else:
            print("등록된 권본이 없습니다.")
        print(f"전체 {len(rows)}권 / 대출 가능 {counts['대출 가능']}권 / 대출 중 {counts['대출 중']}권 / 제적 {counts['제적']}권")
        pause("권본 메뉴")

    def new_copy(self):
        book = self.choose_record("books", "book_id", "권본을 추가할 도서정보 ID> ")
        self.lib.new_copy_target(book["book_id"])
        print(f"도서명: {safe_text(book['title'])} / 저자: {safe_text(book['author'])}")
        if confirm("이 도서정보에 새로운 권본을 추가하시겠습니까?"):
            print("권본 등록 완료: " + self.lib.add_copy(book["book_id"]))
        else:
            print("권본 등록을 취소했습니다.")

    def retire_copy(self):
        copy = self.choose_record("copies", "copy_id", "제적할 권본 ID> ")
        self.lib.retirement_target(copy["copy_id"])
        print("권본 ID: " + copy["copy_id"])
        self.book_info(self.lib.find("books", copy["book_id"]))
        if confirm("이 권본을 제적하시겠습니까?"):
            self.lib.retire_copy(copy["copy_id"])
            print("권본 제적 완료: " + copy["copy_id"])
        else:
            print("권본 제적을 취소했습니다.")

    def members_menu(self):
        self.menu("회원 관리 메뉴", {"1": ("전체 회원 조회", self.all_members),
                                    "2": ("회원 검색", self.member_search_menu)}, zero="관리자 메뉴")

    @staticmethod
    def member_table(members):
        table(["회원ID", "표시 이름", "현재 대출 수", "연체 수"],
              [[r["user_id"], r["display_name"], r["loan_count"], r["overdue_count"]] for r in members])

    def all_members(self):
        members = self.lib.members()
        if members:
            self.member_table(members)
        else:
            print("등록된 일반 회원이 없습니다.")
        pause("회원 관리 메뉴")

    def member_search_menu(self):
        self.menu("회원 검색", {"1": ("회원 ID", lambda: self.find_members("id")),
                               "2": ("표시 이름", lambda: self.find_members("name"))})

    def find_members(self, mode):
        # 존재하지 않을 수 있는 검색 문자열이므로 가입 ID의 문법은 강제하지 않는다.
        query = ask("검색어> ", self.member_query)
        members = self.lib.members(mode, query)
        if members:
            self.member_table(members)
        else:
            print("검색 조건에 해당하는 회원이 없습니다.")
        pause("회원 검색 메뉴")

    @staticmethod
    def member_query(value):
        value = value.strip()
        if not value:
            raise RuleError("검색어를 입력해 주세요.")
        return value

    def history_menu(self):
        self.menu("대출 현황", {"1": ("전체", lambda: self.show_history("1")),
                               "2": ("진행 중", lambda: self.show_history("2")),
                               "3": ("연체", lambda: self.show_history("3")),
                               "4": ("반납 완료", lambda: self.show_history("4"))}, zero="관리자 메뉴")

    def show_history(self, mode):
        loans = self.lib.loan_history(mode)
        if not loans:
            print("조건에 해당하는 대출 기록이 없습니다.")
            return
        self.loan_table(loans, admin=True, history=True)
        pause("대출 현황 메뉴")

    def admin_return(self):
        loan = ask("반납 처리할 대출 ID> ", self.lib.return_target)
        self.loan_table([loan], admin=True)
        if confirm("이 대출을 반납 처리하시겠습니까?"):
            print("반납 완료: " + self.lib.return_loan(loan["loan_id"]))
        else:
            print("반납을 취소했습니다.")
