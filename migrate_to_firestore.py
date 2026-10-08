import os
import sqlite3
from pathlib import Path

from google.cloud import firestore


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "moneyview-3c7bc")
DATABASE_PATH = Path(__file__).parent / "instance" / "payments.sqlite3"
BATCH_SIZE = 400


def main():
    client = firestore.Client(project=PROJECT_ID)
    marker_ref = client.collection("settings").document("sqlite_migration")
    marker = marker_ref.get()
    if marker.exists and marker.to_dict().get("status") == "complete":
        print("SQLite data has already been migrated.")
        return

    connection = sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        account = connection.execute(
            "SELECT name, total_owed_cents FROM debt_accounts WHERE id = 1"
        ).fetchone()
        payments = connection.execute(
            "SELECT id, amount_cents, paid_on, category, memo FROM payments ORDER BY id"
        ).fetchall()
        bills = connection.execute(
            "SELECT id, name, amount_cents, due_date, frequency, active FROM bills ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    if account is None:
        raise RuntimeError(f"No account was found in {DATABASE_PATH}.")

    if not marker.exists:
        account_snapshot = client.collection("settings").document("grom").get()
        has_payments = next(client.collection("payments").limit(1).stream(), None)
        has_bills = next(client.collection("bills").limit(1).stream(), None)
        if account_snapshot.exists or has_payments or has_bills:
            raise RuntimeError(
                "Firestore already contains MoneyView data. Migration stopped to avoid overwriting it."
            )

    marker_ref.set({"status": "in_progress", "source": str(DATABASE_PATH)})
    operations = [
        (
            client.collection("settings").document("grom"),
            {
                "name": account["name"],
                "total_owed_cents": account["total_owed_cents"],
            },
        )
    ]
    operations.extend(
        (
            client.collection("payments").document(f"sqlite-{payment['id']}"),
            {
                "amount_cents": payment["amount_cents"],
                "paid_on": payment["paid_on"],
                "category": payment["category"],
                "memo": payment["memo"],
            },
        )
        for payment in payments
    )
    operations.extend(
        (
            client.collection("bills").document(f"sqlite-{bill['id']}"),
            {
                "name": bill["name"],
                "amount_cents": bill["amount_cents"],
                "due_date": bill["due_date"],
                "frequency": bill["frequency"],
                "active": bool(bill["active"]),
            },
        )
        for bill in bills
    )

    for offset in range(0, len(operations), BATCH_SIZE):
        batch = client.batch()
        for reference, values in operations[offset : offset + BATCH_SIZE]:
            batch.set(reference, values)
        batch.commit()

    marker_ref.set(
        {
            "status": "complete",
            "source": str(DATABASE_PATH),
            "payments_migrated": len(payments),
            "bills_migrated": len(bills),
        }
    )
    print(f"Migrated 1 account, {len(payments)} payments, and {len(bills)} bills.")
    print("The SQLite source file was left in place as a backup.")


if __name__ == "__main__":
    main()