"""기획서의 업무 규칙. 모든 변경은 Store.commit 성공 후에만 반환한다."""

from datetime import timedelta

from . import auth, validation as v
from .errors import RuleError


class Library:
    def __init__(self, store, current_date):
        self.store = store
        self.current_date = current_date
        self.user = None

    @property
    def data(self):
        return self.store.data

    def require(self, role=None):
        if self.user is None:
            raise RuleError("로그인이 필요합니다.")
        user = self.find("users", self.user["user_id"])
        if user["active"] != "true" or role and user["role"] != role:
            raise RuleError("이 기능을 사용할 권한이 없습니다.")
        return user

    def find(self, table, identity):
        key = {"users": "user_id", "books": "book_id", "copies": "copy_id", "loans": "loan_id"}[table]
        if key in v.IDS:
            v.identifier(identity, key)
        result = next((r for r in self.data[table] if r[key] == identity), None)
        if result is None:
            label = v.ID_LABELS.get(key, "회원 ID")
            raise RuleError(f"존재하지 않는 {label}입니다.")
        return dict(result)

    def new_id(self, table):
        field, prefix, size = {"books": ("book_id", "B", 4), "copies": ("copy_id", "C", 4),
                               "loans": ("loan_id", "L", 6)}[table]
        number = max((int(r[field][1:]) for r in self.data[table]), default=0) + 1
        if number >= 10 ** size:
            if table == "loans":
                raise RuleError("시스템의 최대 대출 누적 한도에 도달했습니다.")
            raise RuleError(f"{v.ID_LABELS[field]}의 발급 한도(9999)에 도달했습니다.")
        return f"{prefix}{number:0{size}d}"

    def has_admin(self):
        return any(u["role"] == "admin" for u in self.data["users"])

    def check_new_user_id(self, value):
        return auth.check_new_id(self.data["users"], value)

    def signup(self, user_id, name, password, first_admin=False):
        if self.user is not None:
            raise RuleError("회원 가입은 방문자만 할 수 있습니다.")
        account = auth.make_account(self.data["users"], user_id, name, password,
                                    "admin" if first_admin else "member")
        updated = self.store.snapshot()
        updated["users"].append(account)
        self.store.commit(updated)
        return account["user_id"]

    def login(self, user_id, password):
        self.user = auth.authenticate(self.data["users"], user_id, password)
        return self.user is not None

    def logout(self):
        self.user = None

    def active_loans(self, user_id=None, copy_id=None, book_id=None):
        copies = {c["copy_id"]: c for c in self.data["copies"]}
        return [dict(r) for r in self.data["loans"] if r["return_date"] == ""
                and (user_id is None or r["user_id"] == user_id)
                and (copy_id is None or r["copy_id"] == copy_id)
                and (book_id is None or copies[r["copy_id"]]["book_id"] == book_id)]

    def overdue(self, loan):
        return loan["return_date"] == "" and loan["due_date"] < self.current_date.isoformat()

    def loan_state(self, loan):
        if loan["return_date"]:
            return "반납 완료"
        return "연체" if self.overdue(loan) else "대출 중"

    def loan_details(self, loan):
        copy = self.find("copies", loan["copy_id"])
        book = self.find("books", copy["book_id"])
        user = self.find("users", loan["user_id"])
        return dict(loan, title=book["title"], display_name=user["display_name"], status=self.loan_state(loan))

    def copies_for(self, book_id):
        return sorted((dict(c) for c in self.data["copies"] if c["book_id"] == book_id), key=lambda c: c["copy_id"])

    def available_copies(self, book_id):
        occupied = {r["copy_id"] for r in self.active_loans()}
        return [c for c in self.copies_for(book_id) if c["active"] == "true" and c["copy_id"] not in occupied]

    def book_details(self, book):
        total = sum(c["active"] == "true" for c in self.copies_for(book["book_id"]))
        return dict(book, total=total, available=len(self.available_copies(book["book_id"])))

    def search_books(self, query, admin=False):
        if admin:
            self.require("admin")
        word = query.lower()
        books = [self.book_details(b) for b in self.data["books"]
                 if (admin or b["active"] == "true")
                 and any(word in b[field].lower() for field in ("title", "author", "publisher", "isbn"))]
        return sorted(books, key=lambda b: b["book_id"])

    def borrow(self, book_id):
        user = self.require("member")
        book = self.find("books", book_id)
        loans = self.active_loans(user_id=user["user_id"])
        if len(loans) >= 5:
            raise RuleError("회원당 최대 대출 한도(5권)에 도달하여 대출할 수 없습니다.")
        if any(self.overdue(r) for r in loans):
            raise RuleError("현재 연체 중인 도서가 있어 신규 대출이 불가합니다.")
        copies = self.available_copies(book_id)
        if book["active"] != "true" or not copies:
            raise RuleError("해당 도서는 현재 대출 가능한 재고(권본)가 없습니다.")
        identity = self.new_id("loans")
        try:
            due = self.current_date + timedelta(days=14)
        except OverflowError:
            raise RuleError("14일 뒤의 반납기한이 지원 날짜 범위(9999-12-31)를 벗어나 대출할 수 없습니다.") from None
        loan = {"loan_id": identity, "user_id": user["user_id"], "copy_id": copies[0]["copy_id"],
                "loan_date": self.current_date.isoformat(), "due_date": due.isoformat(), "return_date": ""}
        updated = self.store.snapshot()
        updated["loans"].append(loan)
        self.store.commit(updated)
        return self.loan_details(loan)

    def my_loans(self):
        user = self.require("member")
        return [self.loan_details(r) for r in sorted(self.active_loans(user_id=user["user_id"]), key=lambda r: r["loan_id"])]

    def return_target(self, loan_id):
        user = self.require()
        loan = self.find("loans", loan_id)
        if user["role"] == "member" and loan["user_id"] != user["user_id"]:
            raise RuleError("본인 소유의 대출 ID가 아닙니다.")
        if loan["return_date"]:
            raise RuleError("이미 반납된 대출 ID입니다.")
        return self.loan_details(loan)

    def return_loan(self, loan_id):
        self.return_target(loan_id)
        updated = self.store.snapshot()
        for loan in updated["loans"]:
            if loan["loan_id"] == loan_id:
                loan["return_date"] = self.current_date.isoformat()
        self.store.commit(updated)
        return self.current_date.isoformat()

    def save_book(self, fields, book_id=None):
        self.require("admin")
        values = {key: v.book_field(key, fields[key]) for key in
                  ("title", "author", "publisher", "published_year", "isbn", "category")}
        updated = self.store.snapshot()
        if book_id is None:
            book_id = self.new_id("books")
            updated["books"].append(dict(values, book_id=book_id, active="true"))
        else:
            self.find("books", book_id)
            for row in updated["books"]:
                if row["book_id"] == book_id:
                    row.update(values)
        self.store.commit(updated)
        return book_id

    def deactivation_target(self, book_id):
        self.require("admin")
        book = self.find("books", book_id)
        if book["active"] == "false":
            raise RuleError("이미 비활성화된 도서정보입니다.")
        loans = self.active_loans(book_id=book_id)
        if loans:
            raise RuleError("현재 대출 중인 자료가 있어 비활성화할 수 없습니다. 연결된 대출 ID: "
                            + ", ".join(r["loan_id"] for r in loans))
        return self.book_details(book)

    def deactivate_book(self, book_id):
        self.deactivation_target(book_id)
        updated = self.store.snapshot()
        for row in updated["books"]:
            if row["book_id"] == book_id:
                row["active"] = "false"
        for row in updated["copies"]:
            if row["book_id"] == book_id:
                row["active"] = "false"
        self.store.commit(updated)

    def new_copy_target(self, book_id):
        self.require("admin")
        book = self.find("books", book_id)
        if book["active"] == "false":
            raise RuleError("비활성 상태의 도서정보에는 신규 권본을 등록할 수 없습니다.")
        return book

    def add_copy(self, book_id):
        self.new_copy_target(book_id)
        copy_id = self.new_id("copies")
        updated = self.store.snapshot()
        updated["copies"].append({"copy_id": copy_id, "book_id": book_id, "active": "true"})
        self.store.commit(updated)
        return copy_id

    def retirement_target(self, copy_id):
        self.require("admin")
        copy = self.find("copies", copy_id)
        if copy["active"] == "false":
            raise RuleError("이미 제적된 권본입니다.")
        loans = self.active_loans(copy_id=copy_id)
        if loans:
            raise RuleError("현재 대출 중인 권본은 제적할 수 없습니다. 연결된 대출 ID: " + loans[0]["loan_id"])
        return copy

    def retire_copy(self, copy_id):
        self.retirement_target(copy_id)
        updated = self.store.snapshot()
        for row in updated["copies"]:
            if row["copy_id"] == copy_id:
                row["active"] = "false"
        self.store.commit(updated)

    def members(self, mode=None, query=None):
        self.require("admin")
        result = []
        for user in sorted(self.data["users"], key=lambda row: row["user_id"]):
            if user["role"] != "member":
                continue
            if mode == "id" and user["user_id"].lower() != query.lower():
                continue
            if mode == "name" and query not in user["display_name"]:
                continue
            loans = self.active_loans(user_id=user["user_id"])
            result.append({"user_id": user["user_id"], "display_name": user["display_name"],
                           "loan_count": len(loans), "overdue_count": sum(self.overdue(r) for r in loans)})
        return result

    def loan_history(self, mode):
        self.require("admin")
        selected = [r for r in self.data["loans"] if mode == "1"
                    or mode == "2" and r["return_date"] == ""
                    or mode == "3" and self.overdue(r)
                    or mode == "4" and r["return_date"] != ""]
        selected.sort(key=lambda r: r["loan_id"])
        selected.sort(key=lambda r: r["loan_date"], reverse=True)
        return [self.loan_details(r) for r in selected]
