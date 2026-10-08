import os
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from google.cloud import firestore
from flask import Flask, flash, g, redirect, render_template, request, url_for

from firestore_store import FirestoreStore


def amount_to_cents(value):
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid payment amount.") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Payment amount must be greater than $0.00.")
    return int(amount * 100)


def valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        raise ValueError("Choose a valid date.") from None


def create_app(firestore_client=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        GOOGLE_CLOUD_PROJECT=os.environ.get("GOOGLE_CLOUD_PROJECT", "moneyview-3c7bc"),
    )
    firestore_client = firestore_client
    firestore_store = None

    @app.before_request
    def open_firestore():
        nonlocal firestore_client, firestore_store
        if firestore_client is None:
            firestore_client = firestore.Client(project=app.config["GOOGLE_CLOUD_PROJECT"])
        if firestore_store is None:
            firestore_store = FirestoreStore(firestore_client)
            firestore_store.ensure_defaults()
        g.store = firestore_store

    @app.get("/")
    def index():
        account = g.store.account()
        bills = g.store.list_bills()
        paid_cents = g.store.paid_cents()
        remaining_cents = max(account["total_owed_cents"] - paid_cents, 0)
        percent_paid = min(paid_cents / account["total_owed_cents"] * 100, 100)
        return render_template(
            "overview.html",
            active_page="overview",
            page_title="Financial overview",
            page_eyebrow="Your money, in view",
            account=account,
            bills=bills,
            paid_cents=paid_cents,
            remaining_cents=remaining_cents,
            percent_paid=percent_paid,
            next_bill=bills[0] if bills else None,
            today=date.today().isoformat(),
        )

    @app.get("/payments")
    def payments_page():
        payments = g.store.list_payments()
        bills = g.store.list_bills()
        return render_template(
            "payments.html",
            active_page="payments",
            page_title="Transactions",
            page_eyebrow="Payment history",
            payments=payments,
            next_bill=bills[0] if bills else None,
            today=date.today().isoformat(),
        )

    @app.get("/bills")
    def bills_page():
        bills = g.store.list_bills()
        return render_template(
            "bills.html",
            active_page="bills",
            page_title="Bills & schedules",
            page_eyebrow="Plan ahead",
            bills=bills,
            today=date.today().isoformat(),
        )

    @app.post("/payments")
    def add_payment():
        try:
            amount_cents = amount_to_cents(request.form.get("amount", ""))
            paid_on = valid_date(request.form.get("paid_on", ""))
            memo = request.form.get("memo", "").strip()[:120]
            account = g.store.account()
            paid = g.store.paid_cents()
            if amount_cents > account["total_owed_cents"] - paid:
                raise ValueError("That amount is more than the remaining Grom balance.")
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("payments_page", _anchor="add-payment"))
        g.store.add_payment(amount_cents, paid_on, "grom", memo or "Grom payment")
        flash("Payment added and saved.", "success")
        return redirect(url_for("payments_page"))

    @app.post("/bills")
    def add_bill():
        try:
            name = request.form.get("name", "").strip()[:80]
            amount_cents = amount_to_cents(request.form.get("amount", ""))
            due_date = valid_date(request.form.get("due_date", ""))
            frequency = request.form.get("frequency", "once")
            if not name:
                raise ValueError("Enter a name for this bill.")
            if frequency not in {"once", "weekly", "monthly"}:
                raise ValueError("Choose a valid payment schedule.")
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("bills_page", _anchor="add-bill"))
        g.store.add_bill(name, amount_cents, due_date, frequency)
        flash("Bill scheduled.", "success")
        return redirect(url_for("bills_page"))

    @app.post("/bills/<int:bill_id>/update")
    def update_bill(bill_id):
        try:
            name = request.form.get("name", "").strip()[:80]
            amount_cents = amount_to_cents(request.form.get("amount", ""))
            due_date = valid_date(request.form.get("due_date", ""))
            frequency = request.form.get("frequency", "once")
            if not name:
                raise ValueError("Enter a name for this bill.")
            if frequency not in {"once", "weekly", "monthly"}:
                raise ValueError("Choose a valid payment schedule.")
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("bills_page"))
        if not g.store.update_bill(bill_id, name, amount_cents, due_date, frequency):
            flash("That scheduled bill could not be found.", "error")
            return redirect(url_for("bills_page"))
        flash("Bill details updated.", "success")
        return redirect(url_for("bills_page"))

    @app.post("/bills/<int:bill_id>/delete")
    def delete_bill(bill_id):
        g.store.delete_bill(bill_id)
        flash("Bill removed.", "success")
        return redirect(url_for("bills_page"))

    @app.post("/bills/<int:bill_id>/paid")
    def mark_bill_paid(bill_id):
        if not g.store.mark_bill_paid(bill_id, date.today().isoformat()):
            flash("That scheduled bill could not be found.", "error")
            return redirect(url_for("bills_page"))
        flash("Bill payment recorded.", "success")
        return redirect(url_for("payments_page"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
