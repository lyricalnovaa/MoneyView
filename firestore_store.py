import calendar
from datetime import date, timedelta

from google.cloud import firestore


class FirestoreStore:
    def __init__(self, client):
        self.client = client

    def ensure_defaults(self):
        account_ref = self.client.collection("settings").document("grom")
        if account_ref.get().exists:
            return
        account_ref.set({"name": "Grom", "total_owed_cents": 400000})
        self.client.collection("payments").document("initial-grom-payment").set(
            {
                "amount_cents": 62500,
                "paid_on": None,
                "category": "grom",
                "memo": "Paid before tracker setup",
            }
        )

    def account(self):
        return self.client.collection("settings").document("grom").get().to_dict()

    def _list(self, collection_name):
        return [
            {**snapshot.to_dict(), "id": snapshot.id}
            for snapshot in self.client.collection(collection_name).stream()
        ]

    def list_payments(self):
        payments = self._list("payments")
        dated = sorted(
            (payment for payment in payments if payment.get("paid_on")),
            key=lambda payment: (payment["paid_on"], payment["id"]),
            reverse=True,
        )
        undated = sorted(
            (payment for payment in payments if not payment.get("paid_on")),
            key=lambda payment: payment["id"],
            reverse=True,
        )
        return dated + undated

    def list_bills(self, active_only=True):
        bills = self._list("bills")
        if active_only:
            bills = [bill for bill in bills if bill.get("active", True)]
        return sorted(bills, key=lambda bill: (bill["due_date"], bill["id"]))

    def paid_cents(self, category="grom"):
        return sum(
            payment["amount_cents"]
            for payment in self._list("payments")
            if payment.get("category") == category
        )

    def add_payment(self, amount_cents, paid_on, category, memo):
        reference = self.client.collection("payments").document()
        reference.set(
            {
                "amount_cents": amount_cents,
                "paid_on": paid_on,
                "category": category,
                "memo": memo,
            }
        )

    def add_bill(self, name, amount_cents, due_date, frequency):
        reference = self.client.collection("bills").document()
        reference.set(
            {
                "name": name,
                "amount_cents": amount_cents,
                "due_date": due_date,
                "frequency": frequency,
                "active": True,
            }
        )
        return reference.id

    def get_bill(self, bill_id):
        snapshot = self.client.collection("bills").document(bill_id).get()
        if not snapshot.exists:
            return None
        return {**snapshot.to_dict(), "id": snapshot.id}

    def update_bill(self, bill_id, name, amount_cents, due_date, frequency):
        reference = self.client.collection("bills").document(bill_id)
        snapshot = reference.get()
        if not snapshot.exists or not snapshot.to_dict().get("active", True):
            return False
        reference.update(
            {
                "name": name,
                "amount_cents": amount_cents,
                "due_date": due_date,
                "frequency": frequency,
            }
        )
        return True

    def delete_bill(self, bill_id):
        self.client.collection("bills").document(bill_id).delete()

    def mark_bill_paid(self, bill_id, paid_on):
        bill_ref = self.client.collection("bills").document(bill_id)
        payment_ref = self.client.collection("payments").document()
        transaction = self.client.transaction()

        @firestore.transactional
        def record_payment(transaction):
            snapshot = bill_ref.get(transaction=transaction)
            if not snapshot.exists or not snapshot.to_dict().get("active", True):
                return False

            bill = snapshot.to_dict()
            transaction.set(
                payment_ref,
                {
                    "amount_cents": bill["amount_cents"],
                    "paid_on": paid_on,
                    "category": "bill",
                    "memo": bill["name"],
                },
            )
            if bill["frequency"] == "once":
                transaction.update(bill_ref, {"active": False})
            else:
                next_date = date.fromisoformat(bill["due_date"])
                next_date = advance_due_date(next_date, bill["frequency"])
                today = date.fromisoformat(paid_on)
                while next_date <= today:
                    next_date = advance_due_date(next_date, bill["frequency"])
                transaction.update(bill_ref, {"due_date": next_date.isoformat()})
            return True

        return record_payment(transaction)


def advance_due_date(current_date, frequency):
    if frequency == "weekly":
        return current_date + timedelta(days=7)
    month_index = current_date.month
    year = current_date.year + month_index // 12
    month = month_index % 12 + 1
    return current_date.replace(
        year=year,
        month=month,
        day=min(current_date.day, calendar.monthrange(year, month)[1]),
    )